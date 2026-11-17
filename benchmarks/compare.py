"""Compare CPython, Agen on the C++ VM, and Agen on the Python interpreter.

Each workload is written once in Python and once in Agen. Every timing is the
best of several runs, measured inside the process (startup is excluded).

    python benchmarks/compare.py            # from the agen folder
    python benchmarks/compare.py --repeat 5 --skip-interpreter
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agen import Interpreter, RuntimeOptions, native, parse  # noqa: E402

AGEN = """
fn fib(n) {
    if n < 2 { return n }
    return fib(n - 1) + fib(n - 2)
}
fn is_prime(n) {
    if n < 2 { return false }
    let d = 2
    while d * d <= n {
        if n % d == 0 { return false }
        d = d + 1
    }
    return true
}
fn count_primes(limit) {
    let count = 0
    for i in range(limit) {
        if is_prime(i) { count = count + 1 }
    }
    return count
}
fn sum_squares(n) {
    let total = 0
    for i in range(n) { total = total + i * i % 7 }
    return total
}
fn build(n) {
    let out = ""
    for i in range(n) { out = out + "x" }
    return len(out)
}
"""


def fib(n):
    return n if n < 2 else fib(n - 1) + fib(n - 2)


def is_prime(n):
    if n < 2:
        return False
    d = 2
    while d * d <= n:
        if n % d == 0:
            return False
        d = d + 1
    return True


def count_primes(limit):
    count = 0
    for i in range(limit):
        if is_prime(i):
            count = count + 1
    return count


def sum_squares(n):
    total = 0
    for i in range(n):
        total = total + i * i % 7
    return total


def build(n):
    out = ""
    for _ in range(n):
        out = out + "x"
    return len(out)


WORKLOADS = [
    ("fib(27)", "fib", [27]),
    ("count_primes(200000)", "count_primes", [200000]),
    ("sum_squares(3000000)", "sum_squares", [3000000]),
    ("string build 200000", "build", [200000]),
]
PARALLEL = ("4 x count_primes(150000), 4 threads", "count_primes", [150000] * 4)


def best(fn, repeat: int) -> float:
    timings = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        timings.append(time.perf_counter() - started)
    return min(timings)


def agen_runner(native_enabled: bool):
    state = tempfile.mkdtemp()
    interp = Interpreter(RuntimeOptions(state_dir=Path(state), quiet=True, native=native_enabled))
    interp.run(parse(AGEN))
    return interp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--skip-interpreter", action="store_true",
                        help="skip Agen without the C++ VM (it is slow)")
    args = parser.parse_args()

    fast = agen_runner(True)
    slow = None if args.skip_interpreter else agen_runner(False)
    print(f"native backend: {native.status()}\n")
    header = f"{'workload':<40}{'CPython':>10}{'Agen C++':>11}{'vs CPython':>12}"
    if slow:
        header += f"{'Agen Python':>13}"
    print(header)

    for label, name, call_args in WORKLOADS:
        py = best(lambda: globals()[name](*call_args), args.repeat)
        fn = fast.globals.vars[name]
        ag = best(lambda: fast.call(fn, list(call_args)), args.repeat)
        line = f"{label:<40}{py:>9.3f}s{ag:>10.3f}s{py / ag:>11.1f}x"
        if slow:
            slow_fn = slow.globals.vars[name]
            line += f"{best(lambda: slow.call(slow_fn, list(call_args)), 1):>12.3f}s"
        print(line)

    label, name, items = PARALLEL
    with ThreadPoolExecutor(4) as pool:
        py = best(lambda: list(pool.map(count_primes, items)), args.repeat)
    pmap = fast.globals.vars["pmap"]
    fn = fast.globals.vars[name]
    ag = best(lambda: fast.call(pmap, [fn, list(items), 4]), args.repeat)
    print(f"{label:<40}{py:>9.3f}s{ag:>10.3f}s{py / ag:>11.1f}x")


if __name__ == "__main__":
    main()
