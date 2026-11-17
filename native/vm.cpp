// Agen native backend: a bytecode virtual machine for pure Agen functions.
//
// The Python compiler (src/agen/native.py) translates functions that only use
// numbers, strings, booleans, null, lists, loops and calls to other pure
// functions into the bytecode below. The VM runs that bytecode without the GIL.
//
// The VM never has to reproduce an error message: whenever an operation would
// fail, overflow 64-bit integers, or format a value in a way only Python knows
// (floats as text, for example), it throws Bail and the caller reruns the call
// in the tree-walking interpreter. That is safe because compiled functions
// have no side effects.

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>
#include <memory>
#include <new>
#include <string>
#include <utility>
#include <vector>

#if defined(_MSC_VER) && !defined(__clang__)
#include <intrin.h>
#endif

namespace {

// Keep in sync with OPS and BUILTINS in src/agen/native.py.
enum Op : int32_t {
    CONST, LOAD, STORE, POP,
    ADD, SUB, MUL, DIV, MOD,
    EQ, NE, LT, LE, GT, GE,
    NEG, NOT,
    JUMP, JUMP_IF_FALSE, AND_JUMP, OR_JUMP,
    CALL, BUILTIN, INDEX, LIST, RETURN,
    ITER_PREP, ITER_NEXT, RANGE_PREP, RANGE_NEXT,
    CMP_JUMP, ADD_LOCAL,
    // Operands read straight from locals (L) or constants (K), or from the stack top (S).
    BINOP_LL, BINOP_LK, CMP_JUMP_LL, CMP_JUMP_LK, CMP_JUMP_SL, CMP_JUMP_SK,
    BINOP_SL, BINOP_SK, ADD_LOCAL_S,
    OP_COUNT
};
constexpr int kOperands[OP_COUNT] = {
    1, 1, 1, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0,
    0, 0,
    1, 1, 1, 1,
    2, 2, 0, 1, 0,
    2, 4, 2, 4,
    2, 2,
    3, 3, 4, 4, 3, 3,
    2, 2, 1,
};

enum Builtin : int32_t { B_LEN, B_RANGE, B_INT, B_FLOAT, B_STR, B_ROUND, B_COUNT };

constexpr int64_t kExact = int64_t(1) << 53;  // integers a double holds exactly
constexpr size_t kMaxFrames = 100000;
constexpr size_t kMaxRange = 50000000;
constexpr uint32_t kCheckEvery = 1u << 20;    // instructions between timeout/Ctrl+C checks

struct Bail {
    const char* reason;
};
struct Timeout {};
struct Interrupted {};

[[noreturn]] void bail(const char* reason) { throw Bail{reason}; }

// Values ---------------------------------------------------------------------------
//
// 16 bytes. Strings and lists are reference counted without atomics: each run
// owns its values, and program constants are immortal so concurrent runs of the
// same program can share them.

enum class Tag : uint8_t { Undef, Null, Bool, Int, Float, Str, List };

struct Obj {
    uint32_t refs = 1;
    bool immortal = false;
};

struct Value {
    Tag tag;
    union {
        bool b;
        int64_t i;
        double f;
        Obj* o;
    };

    Value() : tag(Tag::Undef), i(0) {}
    Value(const Value& v) : tag(v.tag), i(v.i) {
        if (heap() && !o->immortal) ++o->refs;
    }
    Value(Value&& v) noexcept : tag(v.tag), i(v.i) { v.tag = Tag::Undef; }
    Value& operator=(const Value& v) {
        if (this != &v) {
            if (v.heap() && !v.o->immortal) ++v.o->refs;
            release();
            tag = v.tag;
            i = v.i;
        }
        return *this;
    }
    Value& operator=(Value&& v) noexcept {
        if (this != &v) {
            release();
            tag = v.tag;
            i = v.i;
            v.tag = Tag::Undef;
        }
        return *this;
    }
    ~Value() { release(); }

    bool heap() const { return tag == Tag::Str || tag == Tag::List; }
    void reset() {
        release();
        tag = Tag::Undef;
    }
    inline void release();

    static Value null() {
        Value v;
        v.tag = Tag::Null;
        return v;
    }
    static Value boolean(bool x) {
        Value v;
        v.tag = Tag::Bool;
        v.b = x;
        return v;
    }
    static Value integer(int64_t x) {
        Value v;
        v.tag = Tag::Int;
        v.i = x;
        return v;
    }
    static Value real(double x) {
        Value v;
        v.tag = Tag::Float;
        v.f = x;
        return v;
    }
    static Value string(std::string s);
    static Value of_list(std::vector<Value> items);
};

struct StrObj : Obj {
    std::string s;
    bool ascii;
};

struct ListObj : Obj {
    std::vector<Value> items;
};

inline void Value::release() {
    if (!heap() || o->immortal || --o->refs != 0) return;
    if (tag == Tag::Str) {
        delete static_cast<StrObj*>(o);
    } else {
        delete static_cast<ListObj*>(o);
    }
}

Value Value::string(std::string s) {
    auto* obj = new StrObj();
    obj->ascii = std::all_of(s.begin(), s.end(), [](char c) { return static_cast<unsigned char>(c) < 0x80; });
    obj->s = std::move(s);
    Value v;
    v.tag = Tag::Str;
    v.o = obj;
    return v;
}

Value Value::of_list(std::vector<Value> items) {
    auto* obj = new ListObj();
    obj->items = std::move(items);
    Value v;
    v.tag = Tag::List;
    v.o = obj;
    return v;
}

inline const StrObj& str(const Value& v) { return *static_cast<const StrObj*>(v.o); }
inline const std::vector<Value>& items(const Value& v) { return static_cast<const ListObj*>(v.o)->items; }

struct Function {
    std::string name;
    size_t nparams = 0;
    size_t nlocals = 0;
    size_t max_stack = 0;
    std::vector<int32_t> code;
    std::vector<Value> consts;
};

struct Program {
    std::vector<Function> functions;

    Program() = default;
    Program(const Program&) = delete;
    Program& operator=(const Program&) = delete;
    ~Program() {
        for (auto& fn : functions) {
            for (auto& c : fn.consts) {
                if (c.heap()) c.o->immortal = false;
            }
        }
    }
};

// Semantics (mirror values.py) ------------------------------------------------

inline bool is_num(const Value& v) { return v.tag == Tag::Int || v.tag == Tag::Float; }

inline double as_double(const Value& v) { return v.tag == Tag::Int ? double(v.i) : v.f; }

inline bool exact(int64_t i) { return i <= kExact && i >= -kExact; }

inline bool truthy(const Value& v) {
    switch (v.tag) {
        case Tag::Null: return false;
        case Tag::Bool: return v.b;
        case Tag::Int: return v.i != 0;
        case Tag::Float: return v.f != 0.0;
        case Tag::Str: return !str(v).s.empty();
        case Tag::List: return !items(v).empty();
        default: bail("undefined value");
    }
}

inline bool add_overflow(int64_t a, int64_t b, int64_t* out) {
#if defined(_MSC_VER) && !defined(__clang__)
    *out = int64_t(uint64_t(a) + uint64_t(b));
    return ((a ^ *out) & (b ^ *out)) < 0;
#else
    return __builtin_add_overflow(a, b, out);
#endif
}

inline bool sub_overflow(int64_t a, int64_t b, int64_t* out) {
#if defined(_MSC_VER) && !defined(__clang__)
    *out = int64_t(uint64_t(a) - uint64_t(b));
    return ((a ^ b) & (a ^ *out)) < 0;
#else
    return __builtin_sub_overflow(a, b, out);
#endif
}

inline bool mul_overflow(int64_t a, int64_t b, int64_t* out) {
#if defined(_MSC_VER) && !defined(__clang__)
    int64_t high;
    *out = _mul128(a, b, &high);
    return high != (*out >> 63);
#else
    return __builtin_mul_overflow(a, b, out);
#endif
}

// format_value() for the types whose text form is unambiguous.
std::string to_text(const Value& v) {
    switch (v.tag) {
        case Tag::Str: return str(v).s;
        case Tag::Int: return std::to_string(v.i);
        case Tag::Bool: return v.b ? "true" : "false";
        case Tag::Null: return "null";
        default: bail("value needs Python formatting");
    }
}

// Python's == (used inside lists): bool, int and float compare as numbers.
bool py_equal(const Value& a, const Value& b) {
    auto numeric = [](const Value& v) { return v.tag == Tag::Bool || is_num(v); };
    if (numeric(a) && numeric(b)) {
        if (a.tag == Tag::Float || b.tag == Tag::Float) {
            const Value& n = a.tag == Tag::Float ? b : a;
            if (n.tag == Tag::Int && !exact(n.i)) bail("inexact int/float comparison");
            double x = a.tag == Tag::Bool ? double(a.b) : as_double(a);
            double y = b.tag == Tag::Bool ? double(b.b) : as_double(b);
            return x == y;
        }
        int64_t x = a.tag == Tag::Bool ? int64_t(a.b) : a.i;
        int64_t y = b.tag == Tag::Bool ? int64_t(b.b) : b.i;
        return x == y;
    }
    if (a.tag != b.tag) return false;
    switch (a.tag) {
        case Tag::Null: return true;
        case Tag::Str: return str(a).s == str(b).s;
        case Tag::List: {
            const auto& x = items(a);
            const auto& y = items(b);
            if (x.size() != y.size()) return false;
            for (size_t k = 0; k < x.size(); ++k) {
                if (!py_equal(x[k], y[k])) return false;
            }
            return true;
        }
        default: bail("undefined value");
    }
}

// values._equal: numbers by value, everything else needs the same type.
bool agen_equal(const Value& a, const Value& b) {
    if (is_num(a) && is_num(b)) return py_equal(a, b);
    if (a.tag != b.tag) return false;
    return py_equal(a, b);
}

int compare(const Value& a, const Value& b) {
    if (a.tag == Tag::Int && b.tag == Tag::Int) return (a.i > b.i) - (a.i < b.i);
    if (is_num(a) && is_num(b)) {
        if ((a.tag == Tag::Int && !exact(a.i)) || (b.tag == Tag::Int && !exact(b.i))) {
            bail("inexact int/float comparison");
        }
        double x = as_double(a), y = as_double(b);
        if (std::isnan(x) || std::isnan(y)) bail("nan comparison");
        return (x > y) - (x < y);
    }
    if (a.tag == Tag::Str && b.tag == Tag::Str) {
        // UTF-8 byte order equals code point order, which is how Python compares.
        int c = str(a).s.compare(str(b).s);
        return (c > 0) - (c < 0);
    }
    bail("values are not comparable");
}

inline bool compare_op(int32_t op, const Value& a, const Value& b) {
    if (op == EQ) return agen_equal(a, b);
    if (op == NE) return !agen_equal(a, b);
    int c = (a.tag == Tag::Int && b.tag == Tag::Int) ? (a.i > b.i) - (a.i < b.i) : compare(a, b);
    switch (op) {
        case LT: return c < 0;
        case LE: return c <= 0;
        case GT: return c > 0;
        default: return c >= 0;
    }
}

Value arith(int32_t op, const Value& a, const Value& b) {
    if (op == ADD) {
        if (a.tag == Tag::Int && b.tag == Tag::Int) {
            int64_t r;
            if (add_overflow(a.i, b.i, &r)) bail("integer overflow");
            return Value::integer(r);
        }
        if (is_num(a) && is_num(b)) return Value::real(as_double(a) + as_double(b));
        if (a.tag == Tag::Str || b.tag == Tag::Str) return Value::string(to_text(a) + to_text(b));
        if (a.tag == Tag::List && b.tag == Tag::List) {
            std::vector<Value> out(items(a));
            out.insert(out.end(), items(b).begin(), items(b).end());
            return Value::of_list(std::move(out));
        }
        bail("unsupported operands for +");
    }
    if (!is_num(a) || !is_num(b)) bail("arithmetic needs numbers");
    bool ints = a.tag == Tag::Int && b.tag == Tag::Int;
    switch (op) {
        case SUB: {
            if (!ints) return Value::real(as_double(a) - as_double(b));
            int64_t r;
            if (sub_overflow(a.i, b.i, &r)) bail("integer overflow");
            return Value::integer(r);
        }
        case MUL: {
            if (!ints) return Value::real(as_double(a) * as_double(b));
            int64_t r;
            if (mul_overflow(a.i, b.i, &r)) bail("integer overflow");
            return Value::integer(r);
        }
        case DIV: {
            if (as_double(b) == 0.0) bail("division by zero");
            if (ints && (!exact(a.i) || !exact(b.i))) bail("inexact integer division");
            return Value::real(as_double(a) / as_double(b));
        }
        case MOD: {
            if (as_double(b) == 0.0) bail("division by zero");
            if (ints) {
                if (b.i == -1) return Value::integer(0);
                int64_t r = a.i % b.i;
                if (r != 0 && ((r < 0) != (b.i < 0))) r += b.i;
                return Value::integer(r);
            }
            double x = as_double(a), y = as_double(b);
            double r = std::fmod(x, y);
            if (r != 0.0) {
                if ((y < 0) != (r < 0)) r += y;
            } else {
                r = std::copysign(0.0, y);
            }
            return Value::real(r);
        }
        default: bail("unknown operator");
    }
}

// Any binary operator, with the integer cases inlined.
inline Value binary(int32_t op, const Value& a, const Value& b) {
    if (a.tag == Tag::Int && b.tag == Tag::Int) {
        int64_t r;
        switch (op) {
            case ADD:
                if (add_overflow(a.i, b.i, &r)) bail("integer overflow");
                return Value::integer(r);
            case SUB:
                if (sub_overflow(a.i, b.i, &r)) bail("integer overflow");
                return Value::integer(r);
            case MUL:
                if (mul_overflow(a.i, b.i, &r)) bail("integer overflow");
                return Value::integer(r);
            case MOD:
                if (b.i == 0) bail("division by zero");
                if (b.i == -1) return Value::integer(0);
                r = a.i % b.i;
                if (r != 0 && ((r < 0) != (b.i < 0))) r += b.i;
                return Value::integer(r);
            case EQ: return Value::boolean(a.i == b.i);
            case NE: return Value::boolean(a.i != b.i);
            case LT: return Value::boolean(a.i < b.i);
            case LE: return Value::boolean(a.i <= b.i);
            case GT: return Value::boolean(a.i > b.i);
            case GE: return Value::boolean(a.i >= b.i);
            default: break;
        }
    }
    if (a.tag == Tag::Undef || b.tag == Tag::Undef) bail("variable read before assignment");
    if (op >= EQ) return Value::boolean(compare_op(op, a, b));
    return arith(op, a, b);
}

// x = x + y. When x holds the only reference to a string or list, append in
// place instead of copying, as CPython does for strings.
inline void add_into(Value& x, const Value& y) {
    if (x.tag == Tag::Int && y.tag == Tag::Int) {
        int64_t r;
        if (add_overflow(x.i, y.i, &r)) bail("integer overflow");
        x.i = r;
        return;
    }
    if (x.tag == Tag::Undef || y.tag == Tag::Undef) bail("variable read before assignment");
    bool sole_owner = x.heap() && x.o->refs == 1 && !x.o->immortal && !(y.heap() && y.o == x.o);
    if (sole_owner && x.tag == Tag::Str) {
        auto* s = static_cast<StrObj*>(x.o);
        if (y.tag == Tag::Str) {
            s->s += str(y).s;
            s->ascii = s->ascii && str(y).ascii;
            return;
        }
        if (y.tag == Tag::Int || y.tag == Tag::Bool || y.tag == Tag::Null) {
            s->s += to_text(y);
            return;
        }
    }
    if (sole_owner && x.tag == Tag::List && y.tag == Tag::List) {
        auto& out = static_cast<ListObj*>(x.o)->items;
        out.insert(out.end(), items(y).begin(), items(y).end());
        return;
    }
    x = arith(ADD, x, y);
}

inline bool test(int32_t op, const Value& a, const Value& b) {
    if (a.tag == Tag::Int && b.tag == Tag::Int) {
        switch (op) {
            case EQ: return a.i == b.i;
            case NE: return a.i != b.i;
            case LT: return a.i < b.i;
            case LE: return a.i <= b.i;
            case GT: return a.i > b.i;
            default: return a.i >= b.i;
        }
    }
    if (a.tag == Tag::Undef || b.tag == Tag::Undef) bail("variable read before assignment");
    return compare_op(op, a, b);
}

Value index_of(const Value& target, const Value& index) {
    if (index.tag != Tag::Int) bail("index must be an integer");
    int64_t k = index.i;
    if (target.tag == Tag::List) {
        int64_t n = int64_t(items(target).size());
        if (k < -n || k >= n) bail("index out of range");
        return items(target)[size_t(k < 0 ? k + n : k)];
    }
    if (target.tag == Tag::Str) {
        const StrObj& s = str(target);
        if (!s.ascii) bail("non-ASCII string index");
        int64_t n = int64_t(s.s.size());
        if (k < -n || k >= n) bail("index out of range");
        return Value::string(std::string(1, s.s[size_t(k < 0 ? k + n : k)]));
    }
    bail("value is not indexable");
}

int64_t length_of(const Value& v) {
    if (v.tag == Tag::List) return int64_t(items(v).size());
    if (v.tag == Tag::Str) {
        if (!str(v).ascii) bail("non-ASCII string length");
        return int64_t(str(v).s.size());
    }
    bail("value has no length");
}

Value float_to_int(double f) {
    if (!std::isfinite(f) || f >= 9.2233720368547758e18 || f < -9.2233720368547758e18) bail("int range");
    return Value::integer(int64_t(f));
}

Value call_builtin(int32_t id, const Value* args, int32_t argc) {
    switch (id) {
        case B_LEN:
            if (argc != 1) bail("len arity");
            return Value::integer(length_of(args[0]));
        case B_RANGE: {
            if (argc != 1 && argc != 2) bail("range arity");
            int64_t start = 0, stop;
            if (argc == 1) {
                if (args[0].tag != Tag::Int) bail("range needs integers");
                stop = args[0].i;
            } else {
                if (args[0].tag != Tag::Int || args[1].tag != Tag::Int) bail("range needs integers");
                start = args[0].i;
                stop = args[1].i;
            }
            std::vector<Value> out;
            if (stop > start) {
                if (uint64_t(stop) - uint64_t(start) > kMaxRange) bail("range too large");
                out.reserve(size_t(stop - start));
                for (int64_t k = start; k < stop; ++k) out.push_back(Value::integer(k));
            }
            return Value::of_list(std::move(out));
        }
        case B_INT:
            if (argc != 1) bail("int arity");
            if (args[0].tag == Tag::Int) return args[0];
            if (args[0].tag == Tag::Bool) return Value::integer(args[0].b);
            if (args[0].tag == Tag::Float) return float_to_int(std::trunc(args[0].f));
            bail("int() needs Python");
        case B_FLOAT:
            if (argc != 1) bail("float arity");
            if (is_num(args[0])) return Value::real(as_double(args[0]));
            if (args[0].tag == Tag::Bool) return Value::real(args[0].b ? 1.0 : 0.0);
            bail("float() needs Python");
        case B_STR:
            if (argc != 1) bail("str arity");
            return Value::string(to_text(args[0]));
        case B_ROUND: {
            if (argc != 1 && argc != 2) bail("round arity");
            const Value& v = args[0];
            if (!is_num(v)) bail("round needs a number");
            int64_t digits = 0;
            if (argc == 2) {
                if (args[1].tag != Tag::Int) bail("round digits");
                digits = args[1].i;
            }
            if (v.tag == Tag::Int && digits >= 0) return v;
            if (v.tag != Tag::Float || digits != 0) bail("round() needs Python");
            // Python rounds halfway cases to the nearest even integer.
            double r = std::round(v.f);
            if (std::fabs(v.f - r) == 0.5) r = 2.0 * std::round(v.f / 2.0);
            return float_to_int(r);
        }
        default: bail("unknown builtin");
    }
}

// Interpreter loop -------------------------------------------------------------

struct Budget {
    bool limited;
    std::chrono::steady_clock::time_point deadline;
};

void checkpoint(const Budget& budget, PyThreadState*& ts) {
    if (budget.limited && std::chrono::steady_clock::now() > budget.deadline) throw Timeout{};
    PyEval_RestoreThread(ts);
    int interrupted = PyErr_CheckSignals();
    ts = PyEval_SaveThread();
    if (interrupted < 0) throw Interrupted{};
}

Value execute(const Program& program, size_t entry, std::vector<Value> args, const Budget& budget,
              PyThreadState*& ts) {
    struct Frame {
        const Function* fn;
        const int32_t* pc;
        size_t base;
    };
    const Function* fn = &program.functions[entry];
    if (args.size() != fn->nparams) bail("arity");

    // Slots above sp are always Undef, so a new frame's locals start empty.
    std::vector<Value> stack(std::max<size_t>(1024, fn->nlocals + fn->max_stack));
    for (size_t k = 0; k < args.size(); ++k) stack[k] = std::move(args[k]);
    Value* S = stack.data();
    size_t base = 0;
    size_t sp = fn->nlocals;
    Value* L = S;
    std::vector<Frame> frames;
    frames.reserve(64);
    frames.push_back({fn, nullptr, 0});

    const int32_t* code = fn->code.data();
    const int32_t* pc = code;
    const Value* K = fn->consts.data();
    uint32_t ticks = 0;

    for (;;) {
        if (++ticks == kCheckEvery) {
            ticks = 0;
            checkpoint(budget, ts);
        }
        switch (*pc++) {
            case CONST:
                S[sp++] = K[*pc++];
                break;
            case LOAD: {
                const Value& v = L[*pc++];
                if (v.tag == Tag::Undef) bail("variable read before assignment");
                S[sp++] = v;
                break;
            }
            case STORE:
                L[*pc++] = std::move(S[--sp]);
                break;
            case POP:
                S[--sp].reset();
                break;
            case ADD: case SUB: case MUL: case DIV: case MOD:
            case EQ: case NE: case LT: case LE: case GT: case GE: {
                Value result = binary(pc[-1], S[sp - 2], S[sp - 1]);
                S[--sp].reset();
                S[sp - 1] = std::move(result);
                break;
            }
            case BINOP_LL:
                S[sp++] = binary(pc[0], L[pc[1]], L[pc[2]]);
                pc += 3;
                break;
            case BINOP_LK:
                S[sp++] = binary(pc[0], L[pc[1]], K[pc[2]]);
                pc += 3;
                break;
            case CMP_JUMP: {
                bool r = test(pc[0], S[sp - 2], S[sp - 1]);
                const int32_t* target = code + pc[1];
                pc += 2;
                S[--sp].reset();
                S[--sp].reset();
                if (!r) pc = target;
                break;
            }
            case CMP_JUMP_LL: {
                bool r = test(pc[0], L[pc[1]], L[pc[2]]);
                pc = r ? pc + 4 : code + pc[3];
                break;
            }
            case CMP_JUMP_LK: {
                bool r = test(pc[0], L[pc[1]], K[pc[2]]);
                pc = r ? pc + 4 : code + pc[3];
                break;
            }
            case CMP_JUMP_SL: case CMP_JUMP_SK: {
                const Value& right = pc[-1] == CMP_JUMP_SL ? L[pc[1]] : K[pc[1]];
                bool r = test(pc[0], S[sp - 1], right);
                S[--sp].reset();
                pc = r ? pc + 3 : code + pc[2];
                break;
            }
            case ADD_LOCAL:
                add_into(L[pc[0]], K[pc[1]]);
                pc += 2;
                break;
            case ADD_LOCAL_S:
                add_into(L[*pc++], S[sp - 1]);
                S[--sp].reset();
                break;
            case BINOP_SL: case BINOP_SK: {
                const Value& right = pc[-1] == BINOP_SL ? L[pc[1]] : K[pc[1]];
                Value result = binary(pc[0], S[sp - 1], right);
                S[sp - 1] = std::move(result);
                pc += 2;
                break;
            }
            case NEG: {
                Value& a = S[sp - 1];
                if (a.tag == Tag::Int) {
                    if (a.i == std::numeric_limits<int64_t>::min()) bail("integer overflow");
                    a.i = -a.i;
                } else if (a.tag == Tag::Float) {
                    a.f = -a.f;
                } else {
                    bail("cannot negate");
                }
                break;
            }
            case NOT: {
                bool r = !truthy(S[sp - 1]);
                S[sp - 1] = Value::boolean(r);
                break;
            }
            case JUMP:
                pc = code + *pc;
                break;
            case JUMP_IF_FALSE: {
                const int32_t* target = code + *pc++;
                bool r = truthy(S[sp - 1]);
                S[--sp].reset();
                if (!r) pc = target;
                break;
            }
            case AND_JUMP: case OR_JUMP: {
                bool is_and = pc[-1] == AND_JUMP;
                const int32_t* target = code + *pc++;
                if (truthy(S[sp - 1]) != is_and) {
                    pc = target;
                } else {
                    S[--sp].reset();
                }
                break;
            }
            case CALL: {
                const Function* callee = &program.functions[size_t(pc[0])];
                size_t argc = size_t(pc[1]);
                pc += 2;
                if (argc != callee->nparams) bail("arity");
                if (frames.size() >= kMaxFrames) bail("call depth");
                frames.back().pc = pc;
                base = sp - argc;
                size_t need = base + callee->nlocals + callee->max_stack;
                if (need > stack.size()) {
                    stack.resize(std::max(need, stack.size() * 2));
                    S = stack.data();
                }
                L = S + base;
                sp = base + callee->nlocals;
                frames.push_back({callee, nullptr, base});
                fn = callee;
                code = fn->code.data();
                K = fn->consts.data();
                pc = code;
                break;
            }
            case BUILTIN: {
                int32_t id = pc[0];
                int32_t argc = pc[1];
                pc += 2;
                size_t first = sp - size_t(argc);
                Value result = call_builtin(id, S + first, argc);
                while (sp > first) S[--sp].reset();
                S[sp++] = std::move(result);
                break;
            }
            case INDEX: {
                Value result = index_of(S[sp - 2], S[sp - 1]);
                S[--sp].reset();
                S[sp - 1] = std::move(result);
                break;
            }
            case LIST: {
                size_t n = size_t(*pc++);
                size_t first = sp - n;
                std::vector<Value> out;
                out.reserve(n);
                for (size_t k = first; k < sp; ++k) out.push_back(std::move(S[k]));
                sp = first;
                S[sp++] = Value::of_list(std::move(out));
                break;
            }
            case RETURN: {
                Value result = std::move(S[--sp]);
                while (sp > base) S[--sp].reset();
                frames.pop_back();
                if (frames.empty()) return result;
                const Frame& caller = frames.back();
                fn = caller.fn;
                code = fn->code.data();
                K = fn->consts.data();
                pc = caller.pc;
                base = caller.base;
                L = S + base;
                S[sp++] = std::move(result);
                break;
            }
            case ITER_PREP: {
                Value& seq = L[pc[0]];
                Value& position = L[pc[1]];
                pc += 2;
                Value iterable = std::move(S[--sp]);
                if (iterable.tag == Tag::Str && !str(iterable).ascii) bail("non-ASCII string loop");
                if (iterable.tag != Tag::List && iterable.tag != Tag::Str) bail("value is not iterable");
                seq = std::move(iterable);
                position = Value::integer(0);
                break;
            }
            case ITER_NEXT: {
                const Value& seq = L[pc[0]];
                Value& position = L[pc[1]];
                Value& var = L[pc[2]];
                const int32_t* target = code + pc[3];
                pc += 4;
                if (position.i >= length_of(seq)) {
                    pc = target;
                } else {
                    var = index_of(seq, position);
                    ++position.i;
                }
                break;
            }
            case RANGE_PREP: {
                Value& current = L[pc[0]];
                Value& end = L[pc[1]];
                pc += 2;
                Value stop = std::move(S[--sp]);
                Value start = std::move(S[--sp]);
                if (start.tag != Tag::Int || stop.tag != Tag::Int) bail("range needs integers");
                current = start;
                end = stop;
                break;
            }
            case RANGE_NEXT: {
                Value& current = L[pc[0]];
                const int64_t end = L[pc[1]].i;
                Value& var = L[pc[2]];
                const int32_t* target = code + pc[3];
                pc += 4;
                if (current.i >= end) {
                    pc = target;
                } else {
                    var = Value::integer(current.i);
                    ++current.i;
                }
                break;
            }
            default:
                bail("bad opcode");
        }
    }
}

// Python <-> VM values -----------------------------------------------------------

Value to_value(PyObject* obj, int depth = 0) {
    if (depth > 64) bail("nesting too deep");
    if (obj == Py_None) return Value::null();
    if (PyBool_Check(obj)) return Value::boolean(obj == Py_True);
    if (PyLong_Check(obj)) {
        int overflow = 0;
        long long x = PyLong_AsLongLongAndOverflow(obj, &overflow);
        if (overflow) bail("integer does not fit 64 bits");
        return Value::integer(x);
    }
    if (PyFloat_Check(obj)) return Value::real(PyFloat_AS_DOUBLE(obj));
    if (PyUnicode_Check(obj)) {
        Py_ssize_t size;
        const char* data = PyUnicode_AsUTF8AndSize(obj, &size);
        if (data == nullptr) {
            PyErr_Clear();
            bail("string is not valid UTF-8");
        }
        return Value::string(std::string(data, size_t(size)));
    }
    if (PyList_Check(obj)) {
        Py_ssize_t n = PyList_GET_SIZE(obj);
        std::vector<Value> out;
        out.reserve(size_t(n));
        for (Py_ssize_t k = 0; k < n; ++k) out.push_back(to_value(PyList_GET_ITEM(obj, k), depth + 1));
        return Value::of_list(std::move(out));
    }
    bail("value type is not supported natively");
}

PyObject* from_value(const Value& v) {
    switch (v.tag) {
        case Tag::Null: Py_RETURN_NONE;
        case Tag::Bool: return PyBool_FromLong(v.b);
        case Tag::Int: return PyLong_FromLongLong(v.i);
        case Tag::Float: return PyFloat_FromDouble(v.f);
        case Tag::Str: return PyUnicode_DecodeUTF8(str(v).s.data(), Py_ssize_t(str(v).s.size()), "strict");
        case Tag::List: {
            const auto& xs = items(v);
            PyObject* list = PyList_New(Py_ssize_t(xs.size()));
            if (list == nullptr) return nullptr;
            for (size_t k = 0; k < xs.size(); ++k) {
                PyObject* item = from_value(xs[k]);
                if (item == nullptr) {
                    Py_DECREF(list);
                    return nullptr;
                }
                PyList_SET_ITEM(list, Py_ssize_t(k), item);
            }
            return list;
        }
        default:
            bail("undefined result");
    }
}

// Python bindings -------------------------------------------------------------------

PyObject* BailoutError = nullptr;

struct ProgramObject {
    PyObject_HEAD
    std::shared_ptr<const Program> program;
};

void Program_dealloc(PyObject* self) {
    reinterpret_cast<ProgramObject*>(self)->program.~shared_ptr();
    Py_TYPE(self)->tp_free(self);
}

PyObject* Program_run(PyObject* self, PyObject* args) {
    Py_ssize_t index;
    PyObject* call_args;
    double budget_seconds;
    if (!PyArg_ParseTuple(args, "nO!d", &index, &PyTuple_Type, &call_args, &budget_seconds)) return nullptr;
    std::shared_ptr<const Program> program = reinterpret_cast<ProgramObject*>(self)->program;
    if (index < 0 || size_t(index) >= program->functions.size()) {
        PyErr_SetString(PyExc_IndexError, "function index out of range");
        return nullptr;
    }

    std::vector<Value> values;
    try {
        Py_ssize_t n = PyTuple_GET_SIZE(call_args);
        values.reserve(size_t(n));
        for (Py_ssize_t k = 0; k < n; ++k) values.push_back(to_value(PyTuple_GET_ITEM(call_args, k)));
    } catch (const Bail& b) {
        PyErr_SetString(BailoutError, b.reason);
        return nullptr;
    }

    Budget budget{budget_seconds >= 0, {}};
    if (budget.limited) {
        budget.deadline = std::chrono::steady_clock::now() +
                          std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                              std::chrono::duration<double>(budget_seconds));
    }

    enum { OK, BAILED, TIMED_OUT, INTERRUPTED } outcome = OK;
    const char* reason = "";
    Value result;
    PyThreadState* ts = PyEval_SaveThread();
    try {
        result = execute(*program, size_t(index), std::move(values), budget, ts);
    } catch (const Bail& b) {
        outcome = BAILED;
        reason = b.reason;
    } catch (const std::bad_alloc&) {
        outcome = BAILED;
        reason = "out of memory";
    } catch (const Timeout&) {
        outcome = TIMED_OUT;
    } catch (const Interrupted&) {
        outcome = INTERRUPTED;
    }
    PyEval_RestoreThread(ts);

    switch (outcome) {
        case BAILED:
            PyErr_SetString(BailoutError, reason);
            return nullptr;
        case TIMED_OUT:
            PyErr_SetString(PyExc_TimeoutError, "timed out");
            return nullptr;
        case INTERRUPTED:
            return nullptr;  // PyErr_CheckSignals already set the exception
        default:
            break;
    }
    try {
        return from_value(result);
    } catch (const Bail& b) {
        PyErr_SetString(BailoutError, b.reason);
        return nullptr;
    }
}

PyMethodDef Program_methods[] = {
    {"run", Program_run, METH_VARARGS,
     "run(index, args, budget) -> value. budget is seconds, or a negative number for no limit."},
    {nullptr, nullptr, 0, nullptr},
};

PyTypeObject ProgramType = {PyVarObject_HEAD_INIT(nullptr, 0)};

bool read_int(PyObject* obj, int64_t* out) {
    if (!PyLong_Check(obj) || PyBool_Check(obj)) return false;
    int overflow = 0;
    *out = PyLong_AsLongLongAndOverflow(obj, &overflow);
    return !overflow && !(*out == -1 && PyErr_Occurred());
}

// Every operand is range-checked here so bad bytecode can't read out of bounds.
const char* verify(const Function& fn, size_t function_count) {
    const auto& code = fn.code;
    size_t pc = 0;
    while (pc < code.size()) {
        int32_t op = code[pc];
        if (op < 0 || op >= OP_COUNT) return "unknown opcode";
        if (pc + size_t(kOperands[op]) >= code.size()) return "truncated instruction";
        const int32_t* a = code.data() + pc + 1;
        auto slot = [&](int32_t s) { return s >= 0 && size_t(s) < fn.nlocals; };
        auto target = [&](int32_t t) { return t >= 0 && size_t(t) < code.size(); };
        auto constant = [&](int32_t k) { return k >= 0 && size_t(k) < fn.consts.size(); };
        switch (op) {
            case CONST: if (!constant(a[0])) return "bad constant"; break;
            case LOAD: case STORE: if (!slot(a[0])) return "bad slot"; break;
            case JUMP: case JUMP_IF_FALSE: case AND_JUMP: case OR_JUMP:
                if (!target(a[0])) return "bad jump";
                break;
            case CALL:
                if (a[0] < 0 || size_t(a[0]) >= function_count || a[1] < 0) return "bad call";
                break;
            case BUILTIN: if (a[0] < 0 || a[0] >= B_COUNT || a[1] < 0) return "bad builtin"; break;
            case LIST: if (a[0] < 0) return "bad list size"; break;
            case ITER_PREP: case RANGE_PREP: if (!slot(a[0]) || !slot(a[1])) return "bad slot"; break;
            case ITER_NEXT: case RANGE_NEXT:
                if (!slot(a[0]) || !slot(a[1]) || !slot(a[2]) || !target(a[3])) return "bad loop";
                break;
            case CMP_JUMP: if (a[0] < EQ || a[0] > GE || !target(a[1])) return "bad compare"; break;
            case ADD_LOCAL: if (!slot(a[0]) || !constant(a[1])) return "bad add"; break;
            case BINOP_LL:
                if (a[0] < ADD || a[0] > GE || !slot(a[1]) || !slot(a[2])) return "bad operands";
                break;
            case BINOP_LK:
                if (a[0] < ADD || a[0] > GE || !slot(a[1]) || !constant(a[2])) return "bad operands";
                break;
            case CMP_JUMP_LL:
                if (a[0] < EQ || a[0] > GE || !slot(a[1]) || !slot(a[2]) || !target(a[3])) return "bad compare";
                break;
            case CMP_JUMP_LK:
                if (a[0] < EQ || a[0] > GE || !slot(a[1]) || !constant(a[2]) || !target(a[3])) {
                    return "bad compare";
                }
                break;
            case CMP_JUMP_SL: if (a[0] < EQ || a[0] > GE || !slot(a[1]) || !target(a[2])) return "bad compare"; break;
            case CMP_JUMP_SK:
                if (a[0] < EQ || a[0] > GE || !constant(a[1]) || !target(a[2])) return "bad compare";
                break;
            case BINOP_SL: if (a[0] < ADD || a[0] > GE || !slot(a[1])) return "bad operands"; break;
            case BINOP_SK: if (a[0] < ADD || a[0] > GE || !constant(a[1])) return "bad operands"; break;
            case ADD_LOCAL_S: if (!slot(a[0])) return "bad slot"; break;
            default: break;
        }
        pc += 1 + size_t(kOperands[op]);
    }
    if (code.empty() || code.back() != RETURN) return "code must end with RETURN";
    return nullptr;
}

PyObject* native_build(PyObject*, PyObject* specs) {
    if (!PyList_Check(specs)) {
        PyErr_SetString(PyExc_TypeError, "build() expects a list of function specs");
        return nullptr;
    }
    auto program = std::make_shared<Program>();
    Py_ssize_t count = PyList_GET_SIZE(specs);
    program->functions.resize(size_t(count));
    for (Py_ssize_t k = 0; k < count; ++k) {
        PyObject* spec = PyList_GET_ITEM(specs, k);
        PyObject *name, *code, *consts;
        Py_ssize_t nparams, nlocals, max_stack;
        if (!PyArg_ParseTuple(spec, "UnnnO!O!", &name, &nparams, &nlocals, &max_stack, &PyList_Type, &code,
                              &PyList_Type, &consts)) {
            return nullptr;
        }
        if (nparams < 0 || nlocals < nparams || max_stack < 0) {
            PyErr_SetString(PyExc_ValueError, "bad local or stack size");
            return nullptr;
        }
        Function& fn = program->functions[size_t(k)];
        fn.name = PyUnicode_AsUTF8(name);
        fn.nparams = size_t(nparams);
        fn.nlocals = size_t(nlocals);
        fn.max_stack = size_t(max_stack);
        for (Py_ssize_t c = 0; c < PyList_GET_SIZE(code); ++c) {
            int64_t word;
            if (!read_int(PyList_GET_ITEM(code, c), &word) || word < INT32_MIN || word > INT32_MAX) {
                PyErr_SetString(PyExc_ValueError, "bytecode must be 32-bit integers");
                return nullptr;
            }
            fn.code.push_back(int32_t(word));
        }
        try {
            for (Py_ssize_t c = 0; c < PyList_GET_SIZE(consts); ++c) {
                Value v = to_value(PyList_GET_ITEM(consts, c));
                if (v.heap()) v.o->immortal = true;
                fn.consts.push_back(std::move(v));
            }
        } catch (const Bail& b) {
            PyErr_SetString(PyExc_ValueError, b.reason);
            return nullptr;
        }
    }
    for (const auto& fn : program->functions) {
        if (const char* problem = verify(fn, program->functions.size())) {
            PyErr_Format(PyExc_ValueError, "%s: %s", fn.name.c_str(), problem);
            return nullptr;
        }
    }
    auto* obj = PyObject_New(ProgramObject, &ProgramType);
    if (obj == nullptr) return nullptr;
    new (&obj->program) std::shared_ptr<const Program>(std::move(program));
    return reinterpret_cast<PyObject*>(obj);
}

PyMethodDef module_methods[] = {
    {"build", native_build, METH_O,
     "build(specs) -> Program. specs: [(name, nparams, nlocals, max_stack, code, consts)]."},
    {nullptr, nullptr, 0, nullptr},
};

PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT, "_native", "Agen native bytecode VM (C++).", -1, module_methods,
};

}  // namespace

PyMODINIT_FUNC PyInit__native(void) {
    ProgramType.tp_name = "agen._native.Program";
    ProgramType.tp_basicsize = sizeof(ProgramObject);
    ProgramType.tp_dealloc = Program_dealloc;
    ProgramType.tp_flags = Py_TPFLAGS_DEFAULT;
    ProgramType.tp_doc = "A compiled set of Agen functions.";
    ProgramType.tp_methods = Program_methods;
    if (PyType_Ready(&ProgramType) < 0) return nullptr;

    PyObject* module = PyModule_Create(&module_def);
    if (module == nullptr) return nullptr;
    BailoutError = PyErr_NewException("agen._native.Bailout", nullptr, nullptr);
    if (BailoutError == nullptr || PyModule_AddObjectRef(module, "Bailout", BailoutError) < 0 ||
        PyModule_AddIntConstant(module, "ABI", 2) < 0) {
        Py_DECREF(module);
        return nullptr;
    }
    return module;
}
