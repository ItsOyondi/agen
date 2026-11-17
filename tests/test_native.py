"""The C++ backend must be indistinguishable from the Python interpreter, only faster."""

import tempfile
import textwrap
import time
import unittest

from helpers import run_source

from agen import native
from agen.errors import AgenError, AgenTimeoutError, StepFailedError

PROGRAMS = {
    "recursion": """
        fn fib(n) {
            if n < 2 { return n }
            return fib(n - 1) + fib(n - 2)
        }
        let out = fib(20)
    """,
    "loops_and_lists": """
        fn evens(limit) {
            let out = []
            for i in range(limit) {
                if i % 2 == 0 { out = out + [i] }
            }
            return out
        }
        fn total(items) {
            let sum = 0
            for x in items { sum = sum + x }
            return sum
        }
        let out = [evens(10), total(evens(100)), total([1.5, 2.25])]
    """,
    "python_modulo_and_division": """
        fn ops(a, b) { return [a % b, a / b, a - b, a * b] }
        let out = [ops(-7, 3), ops(7, -3), ops(7.5, -2), ops(-7.5, 2), ops(6, 3)]
    """,
    "logic_returns_operands": """
        fn pick(a, b) { return [a and b, a or b, not a] }
        let out = [pick(0, 5), pick(3, 0), pick("", "x"), pick([1], null)]
    """,
    "equality_rules": """
        fn eq(a, b) { return [a == b, a != b] }
        let out = [eq(1, 1.0), eq(true, 1), eq([1], [true]), eq("a", "a"), eq(null, false), eq([1, [2]], [1, [2]])]
    """,
    "strings": """
        fn shout(word, times) {
            let out = ""
            for i in range(times) { out = out + word + i + "-" }
            return out + true + null
        }
        fn chars(s) {
            let out = []
            for c in s { out = out + [c] }
            return [out, len(s), s[-1], s < "b"]
        }
        let out = [shout("go", 3), chars("abc")]
    """,
    "builtins": """
        fn convert(x) { return [int(x), float(x), str(int(x)), round(x)] }
        let out = [convert(2.5), convert(3.5), convert(-2.7), convert(7), round(2.675, 2), str(1.5)]
    """,
    "overflow_falls_back_to_python": """
        fn factorial(n) {
            if n < 2 { return 1 }
            return n * factorial(n - 1)
        }
        let out = [factorial(20), factorial(30)]
    """,
    "unicode_falls_back": """
        fn first(s) { return [s[0], len(s)] }
        let out = first("héllo")
    """,
    "global_constants_stay_in_python": """
        let rate = 3
        fn scale(x) { return x * rate }
        let out = scale(4)
    """,
    "in_place_append_keeps_shared_values_intact": """
        fn alias() {
            let a = "x"
            let b = a
            a = a + "y"
            let xs = [1]
            let ys = xs
            xs = xs + [2]
            let zs = [0]
            zs = zs + zs
            let grow = []
            for v in [1, 2, 3] {
                grow = grow + [v]
            }
            let snapshot = [1, 2]
            for v in snapshot {
                snapshot = snapshot + [v * 10]
            }
            let s = ""
            for i in range(5) { s = s + i + "," + true }
            return [a, b, xs, ys, zs, grow, snapshot, s]
        }
        let out = alias()
    """,
    "while_and_shadowing": """
        fn countdown(n) {
            let seen = []
            while n > 0 {
                seen = seen + [n]
                n = n - 1
            }
            return seen
        }
        let out = countdown(5)
    """,
}

ERRORS = {
    "division_by_zero": "fn f(x) { return 1 / x }\nlet out = f(0)",
    "type_error": 'fn f(x) { return x - "a" }\nlet out = f(1)',
    "index_error": "fn f(xs) { return xs[5] }\nlet out = f([1, 2])",
    "arity": "fn f(a, b) { return a }\nlet out = f(1)",
    "range_type": 'fn f() { for i in range("3") { } }\nlet out = f()',
}


class NativeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def run_both(self, source, **kwargs):
        source = textwrap.dedent(source)
        results = []
        for enabled in (True, False):
            with tempfile.TemporaryDirectory() as state:
                try:
                    interp = run_source(source, state, native=enabled, **kwargs)
                    results.append(("ok", interp.globals.vars.get("out"), interp))
                except AgenError as exc:
                    results.append(("error", (type(exc).__name__, str(exc), exc.line), None))
        return results


@unittest.skipUnless(native.available(), "C++ extension not built")
class DifferentialTests(NativeTestCase):
    def test_programs_match_python(self):
        for name, source in PROGRAMS.items():
            with self.subTest(name):
                fast, slow = self.run_both(source)
                self.assertEqual(fast[:2], slow[:2])
                self.assertEqual([type(v) for v in _flatten(fast[1])], [type(v) for v in _flatten(slow[1])])

    def test_errors_match_python(self):
        for name, source in ERRORS.items():
            with self.subTest(name):
                fast, slow = self.run_both(source)
                self.assertEqual(fast[0], "error")
                self.assertEqual(fast[:2], slow[:2])

    def test_eligible_functions_run_natively(self):
        (_, out, interp), _ = self.run_both(PROGRAMS["recursion"])
        self.assertEqual(out, 6765)
        self.assertEqual(interp.native.calls, 1, "the top-level call runs in C++; recursion stays inside the VM")
        self.assertEqual(interp.native.fallbacks, 0)

    def test_overflow_bails_out_once_and_python_finishes(self):
        (_, out, interp), _ = self.run_both(PROGRAMS["overflow_falls_back_to_python"])
        self.assertEqual(out[1], 265252859812191058636308480000000)
        self.assertEqual(interp.native.fallbacks, 1)

    def test_rebinding_a_callee_is_respected(self):
        (_, out, _), (_, slow_out, _) = self.run_both("""
            fn helper(x) { return x + 1 }
            fn use(x) { return helper(x) }
            let a = use(1)
            fn helper(x) { return x + 100 }
            let out = [a, use(1)]
        """)
        self.assertEqual(out, [2, 101])
        self.assertEqual(out, slow_out)

    def test_native_is_much_faster(self):
        source = """
            fn count(limit) {
                let total = 0
                for i in range(limit) {
                    if i % 3 == 0 or i % 5 == 0 { total = total + i }
                }
                return total
            }
            let out = count(300000)
        """
        timings = []
        for enabled in (True, False):
            started = time.perf_counter()
            run_source(textwrap.dedent(source), self._tmp.name, native=enabled)
            timings.append(time.perf_counter() - started)
        self.assertLess(timings[0] * 10, timings[1])

    def test_step_timeout_stops_native_code(self):
        source = textwrap.dedent("""
            fn spin(n) {
                let i = 0
                while i < n { i = i + 1 }
                return i
            }
            workflow job() {
                step slow with {timeout: 0.2, retries: 0} {
                    x = spin(100000000000)
                }
            }
            job()
        """)
        started = time.monotonic()
        with self.assertRaises(StepFailedError) as ctx:
            run_source(source, self._tmp.name)
        self.assertIsInstance(ctx.exception.__cause__, AgenTimeoutError)
        self.assertLess(time.monotonic() - started, 3)

    def test_pmap_runs_native_functions_on_several_cores(self):
        source = textwrap.dedent("""
            fn work(n) {
                let total = 0
                for i in range(n) { total = total + i % 7 }
                return total
            }
            let one = work(3000000)
            let out = pmap(work, [3000000, 3000000, 3000000, 3000000])
        """)
        started = time.perf_counter()
        interp = run_source(source, self._tmp.name)
        self.assertEqual(interp.globals.vars["out"], [interp.globals.vars["one"]] * 4)
        self.assertGreater(interp.native.calls, 4)
        self.assertLess(time.perf_counter() - started, 10)


class FallbackWithoutExtensionTests(NativeTestCase):
    def test_disabled_backend_runs_everything_in_python(self):
        with tempfile.TemporaryDirectory() as state:
            interp = run_source(PROGRAMS["recursion"], state, native=False)
        self.assertIsNone(interp.native)
        self.assertEqual(interp.globals.vars["out"], 6765)


def _flatten(value):
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


if __name__ == "__main__":
    unittest.main()
