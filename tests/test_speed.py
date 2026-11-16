import json
import os
import tempfile
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from helpers import run_source

from agen import net
from agen.errors import AgenRuntimeError, AgenTypeError, StepFailedError

DELAY = 0.3


class FakeTypeSafe(BaseHTTPRequestHandler):
    """Answers /v1/systemone after DELAY seconds; records requests and connections."""

    protocol_version = "HTTP/1.1"
    requests: list[dict] = []
    connections: set[int] = set()
    lock = threading.Lock()

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        with self.lock:
            FakeTypeSafe.requests.append({"path": self.path, "auth": self.headers["Authorization"], "body": body})
            FakeTypeSafe.connections.add(id(self.connection))
        time.sleep(DELAY)
        answers = {}
        for name, q in body["questions"].items():
            if q["type"] == "choice":
                label = next(iter(q["criteria"]))
                answers[name] = {"type": "choice", "choice": label, "confidence": 0.9,
                                 "probabilities": {label: 0.9}}
            elif q["type"] == "score":
                answers[name] = {"type": "score", "score": 1.0, "confidence": 0.8,
                                 "legend": {"0": "low"}, "probabilities": {"0": 1.0}}
            else:
                answers[name] = {"type": "noul", "noul": 0.75}
        payload = json.dumps({"model": body["model"], "usage": {}, "answers": answers}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class SpeedTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class ParallelTests(SpeedTestCase):
    def test_parallel_block_runs_branches_concurrently_and_merges_results(self):
        started = time.monotonic()
        interp = run_source(textwrap.dedent("""
            parallel {
                a = slow(1)
                b = slow(2)
                c = slow(3)
                d = slow(4)
            }
            let total = a + b + c + d
        """), self.state, builtins={"slow": lambda x: (time.sleep(DELAY), x * 10)[1]})
        elapsed = time.monotonic() - started
        self.assertEqual(interp.globals.vars["total"], 100)
        self.assertLess(elapsed, DELAY * 3)

    def test_parallel_branches_cannot_assign_the_same_name(self):
        with self.assertRaises(AgenRuntimeError) as ctx:
            run_source("parallel {\n x = 1\n x = 2\n}", self.state)
        self.assertIn("both assign 'x'", str(ctx.exception))

    def test_pmap_keeps_order_and_overlaps_waits(self):
        started = time.monotonic()
        interp = run_source(textwrap.dedent("""
            fn work(n) {
                sleep(0.3)
                return n * n
            }
            let squares = pmap(work, range(6))
        """), self.state)
        self.assertEqual(interp.globals.vars["squares"], [0, 1, 4, 9, 16, 25])
        self.assertLess(time.monotonic() - started, DELAY * 3)

    def test_pmap_workers_limit_concurrency(self):
        active, peak, lock = [0], [0], threading.Lock()

        def tracked(x):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return x

        run_source("let out = pmap(tracked, range(8), 2)", self.state, builtins={"tracked": tracked})
        self.assertLessEqual(peak[0], 2)

    def test_first_branch_error_is_raised_after_all_branches_finish(self):
        finished = []
        with self.assertRaises(AgenRuntimeError) as ctx:
            run_source(textwrap.dedent("""
                fn work(n) {
                    if n == 1 {
                        fail("boom " + n)
                    }
                    mark(n)
                }
                pmap(work, range(4))
            """), self.state, builtins={"mark": finished.append})
        self.assertIn("boom 1", str(ctx.exception))
        self.assertEqual(sorted(finished), [0, 2, 3])

    def test_steps_inside_parallel_branches_replay_with_stable_keys(self):
        program = textwrap.dedent("""
            fn fetch(n) {
                step load {
                    value = count(n) * 2
                }
                if n == 2 {
                    step check {
                        fail_once()
                    }
                }
                return value
            }
            workflow batch() {
                return pmap(fetch, [1, 2, 3])
            }
            let out = batch()
        """)
        calls, failures = [], [0]

        def fail_once():
            failures[0] += 1
            if failures[0] == 1:
                raise AgenRuntimeError("outage", retryable=False)

        builtins = {"count": lambda n: (calls.append(n), n)[1], "fail_once": fail_once}
        with self.assertRaises(StepFailedError):
            run_source(program, self.state, builtins=builtins)
        self.assertEqual(sorted(calls), [1, 2, 3])

        interp = run_source(program, self.state, builtins=builtins)
        self.assertEqual(interp.globals.vars["out"], [2, 4, 6])
        self.assertEqual(sorted(calls), [1, 2, 3], "completed branch steps must replay, not rerun")
        journal = json.loads(next(Path(self.state, "runs").glob("*.json")).read_text())
        self.assertEqual(sorted(journal["steps"]),
                         ["pmap0.0.load", "pmap0.1.check", "pmap0.1.load", "pmap0.2.load"])

    def test_parallel_option_errors(self):
        with self.assertRaises(AgenTypeError):
            run_source("parallel with {workers: 0} {\n x = 1\n}", self.state)
        with self.assertRaises(AgenTypeError):
            run_source("parallel with {speed: 9} {\n x = 1\n}", self.state)


class JevTests(SpeedTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTypeSafe)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = mock.patch.dict(os.environ, {
            "TYPESAFE_API_KEY": "test-key",
            "TYPESAFE_BASE_URL": f"http://127.0.0.1:{cls.server.server_port}",
        })
        cls.env.start()
        cls.proxies = mock.patch.object(net, "_proxies", {})
        cls.proxies.start()

    @classmethod
    def tearDownClass(cls):
        cls.proxies.stop()
        cls.env.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        super().setUp()
        FakeTypeSafe.requests = []
        FakeTypeSafe.connections = set()

    def tearDown(self):
        net.close_connections()
        super().tearDown()

    QUESTIONS = textwrap.dedent("""
        let questions = {
            department: choice("Which team should handle this", {
                technical: "Bugs or integration problems",
                billing: "Payment issues"
            }),
            frustration: score("How frustrated", ["Calm", "Frustrated", "Angry"]),
            urgent: noul("The message is urgent")
        }
    """)

    def test_jev_sends_typed_questions_and_returns_answers(self):
        interp = run_source(self.QUESTIONS + textwrap.dedent("""
            let a = jev("Stripe integration keeps failing", questions)
            let summary = a.department.choice + " " + a.frustration.score + " " + a.urgent.noul
        """), self.state)
        self.assertEqual(interp.globals.vars["summary"], "technical 1.0 0.75")
        sent = FakeTypeSafe.requests[0]
        self.assertEqual(sent["path"], "/v1/systemone")
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertEqual(sent["body"]["model"], "jev-latest")
        self.assertEqual(sent["body"]["questions"]["frustration"],
                         {"type": "score", "instructions": "How frustrated",
                          "criteria": ["Calm", "Frustrated", "Angry"]})

    def test_jev_all_runs_requests_in_parallel(self):
        started = time.monotonic()
        interp = run_source(self.QUESTIONS + textwrap.dedent("""
            let results = jev_all(["t1", "t2", "t3", "t4", "t5"], questions)
        """), self.state)
        elapsed = time.monotonic() - started
        self.assertEqual(len(interp.globals.vars["results"]), 5)
        self.assertEqual(len(FakeTypeSafe.requests), 5)
        self.assertLess(elapsed, DELAY * 3)

    def test_cache_skips_repeated_requests(self):
        run_source(self.QUESTIONS + textwrap.dedent("""
            let first = jev("same text", questions, {cache: true})
            let second = jev("same text", questions, {cache: true})
        """), self.state)
        self.assertEqual(len(FakeTypeSafe.requests), 1)

    def test_sequential_requests_reuse_one_connection(self):
        run_source(self.QUESTIONS + textwrap.dedent("""
            for t in ["a", "b", "c"] {
                jev(t, questions)
            }
        """), self.state)
        self.assertEqual(len(FakeTypeSafe.requests), 3)
        self.assertEqual(len(FakeTypeSafe.connections), 1)

    def test_invalid_questions_fail_fast(self):
        with self.assertRaises(AgenTypeError):
            run_source('let q = choice("pick", {})', self.state)
        with self.assertRaises(AgenTypeError):
            run_source('let a = jev("x", {bad: "not a question"})', self.state)

    def test_missing_api_key_is_not_retryable(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            with self.assertRaises(AgenRuntimeError) as ctx:
                run_source(self.QUESTIONS + 'let a = jev("x", questions)', self.state)
        self.assertFalse(ctx.exception.retryable)
        self.assertIn("TYPESAFE_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
