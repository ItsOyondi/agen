import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from helpers import SRC, make_interp, parse, run_source

from agen.errors import AgenNameError, AgenTypeError, ServiceFailedError, StepFailedError  # noqa: E402
from agen.terraform import to_terraform


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_functions_loops_and_collections(self):
        interp = run_source(textwrap.dedent("""
            fn fib(n) {
                if n < 2 {
                    return n
                }
                return fib(n - 1) + fib(n - 2)
            }
            let out = []
            for i in range(6) {
                push(out, fib(i))
            }
            let m = {a: 1}
            m.b = 2
            m["c"] = m.a + m.b
            let label = "n=" + len(out)
        """), self.state)
        g = interp.globals.vars
        self.assertEqual(g["out"], [0, 1, 1, 2, 3, 5])
        self.assertEqual(g["m"], {"a": 1, "b": 2, "c": 3})
        self.assertEqual(g["label"], "n=6")

    def test_errors_are_classified(self):
        with self.assertRaises(AgenNameError) as ctx:
            run_source("let x = 1\nprint(missing)", self.state)
        self.assertEqual(ctx.exception.line, 2)
        with self.assertRaises(AgenTypeError):
            run_source('let x = 1 - "a"', self.state)


class ObjectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_methods_inheritance_and_super(self):
        interp = run_source(textwrap.dedent("""
            class Number {
                init(value) {
                    self.value = value
                }
                fn add(amount) {
                    return Number(self.value + amount)
                }
            }
            class RunningTotal extends Number {
                init(value) {
                    super(value)
                    self.adds = 0
                }
                fn add(amount) {
                    self.adds = self.adds + 1
                    self.value = super.add(amount).value
                    return self.value
                }
            }
            let five = Number(2).add(3)
            let total = RunningTotal(10)
            total.add(5)
            total.add(2)
            let sum = five.value
            let value = total.value
            let adds = total.adds
            let kind = type(five)
        """), self.state)
        g = interp.globals.vars
        self.assertEqual((g["sum"], g["value"], g["adds"], g["kind"]), (5, 17, 2, "Number"))
        self.assertEqual(g["five"], g["five"])

    def test_field_updates_roll_back_with_the_step(self):
        interp = run_source(textwrap.dedent("""
            class Counter {
                init(n) { self.n = n }
                fn inc() { self.n = self.n + 1 }
            }
            workflow job() {
                c = Counter(1)
                step bump with {retries: 1, backoff: 0} {
                    c.inc()
                    flaky("bump", 1)
                }
                return c.n
            }
            let result = job()
        """), self.state)
        self.assertEqual(interp.globals.vars["result"], 2)

    def test_instance_is_restored_from_the_journal(self):
        source = textwrap.dedent("""
            class Counter {
                init(n) { self.n = n }
                fn inc() { self.n = self.n + 1 }
            }
            workflow job() {
                step make {
                    c = Counter(1)
                    c.inc()
                }
                step again {
                    check()
                    c.inc()
                }
                return c.n
            }
            let result = job()
        """)
        def broken() -> None:
            raise AgenTypeError("nope")

        with self.assertRaises(StepFailedError):
            run_source(source, self.state, builtins={"check": broken})
        interp = run_source(source, self.state, builtins={"check": lambda: None})
        self.assertEqual(interp.globals.vars["result"], 3)
        self.assertEqual(interp.events.counts["step.replayed"], 1)


class FaultToleranceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_step_retries_transient_failures(self):
        interp = run_source(textwrap.dedent("""
            workflow w() {
                step s with {retries: 3, backoff: 0} {
                    flaky("api", 2)
                    x = 42
                }
                return x
            }
            let result = w()
        """), self.state)
        self.assertEqual(interp.globals.vars["result"], 42)
        self.assertEqual(interp.events.counts["step.retrying"], 2)

    def test_failed_attempts_are_rolled_back(self):
        interp = run_source(textwrap.dedent("""
            workflow w() {
                items = []
                step s with {retries: 2, backoff: 0} {
                    push(items, "attempt")
                    flaky("api", 2)
                }
                return items
            }
            let result = w()
        """), self.state)
        self.assertEqual(interp.globals.vars["result"], ["attempt"])

    def test_fallback_after_exhausted_retries(self):
        interp = run_source(textwrap.dedent("""
            workflow w() {
                step s with {retries: 1, backoff: 0} {
                    fail("provider down")
                } fallback {
                    x = "degraded"
                }
                return x
            }
            let result = w()
        """), self.state)
        self.assertEqual(interp.globals.vars["result"], "degraded")
        self.assertEqual(interp.events.counts["step.recovered"], 1)

    def test_programming_errors_are_not_retried(self):
        with self.assertRaises(StepFailedError):
            run_source(textwrap.dedent("""
                workflow w() {
                    step s with {retries: 5, backoff: 0} {
                        x = undefined_name
                    }
                }
                w()
            """), self.state)
        run = json.loads(next((Path(self.state) / "runs").glob("*.json")).read_text())
        self.assertEqual(run["steps"]["s"]["attempts"], 1)
        self.assertEqual(run["status"], "failed")

    def test_timeout_triggers_fallback(self):
        started = time.monotonic()
        interp = run_source(textwrap.dedent("""
            workflow w() {
                step slow with {timeout: 0.05, retries: 0} {
                    sleep(5)
                    x = 1
                } fallback {
                    x = 0
                }
                return x
            }
            let result = w()
        """), self.state)
        self.assertEqual(interp.globals.vars["result"], 0)
        self.assertLess(time.monotonic() - started, 2)

    def test_failed_workflow_resumes_without_redoing_completed_steps(self):
        calls = {"produce": 0}
        source = textwrap.dedent("""
            workflow job() {
                step one {
                    a = produce()
                }
                step two {
                    check()
                    b = a + 1
                }
                return b
            }
            let result = job()
        """)

        def produce():
            calls["produce"] += 1
            return 41

        def broken():
            raise AgenTypeError("downstream bug")

        with self.assertRaises(StepFailedError):
            run_source(source, self.state, builtins={"produce": produce, "check": broken})

        interp = run_source(source, self.state, builtins={"produce": produce, "check": lambda: None})
        self.assertEqual(interp.globals.vars["result"], 42)
        self.assertEqual(calls["produce"], 1)
        self.assertEqual(interp.events.counts["step.replayed"], 1)

        interp = run_source(source, self.state, builtins={"produce": produce, "check": lambda: None})
        self.assertEqual(interp.globals.vars["result"], 42)
        self.assertEqual(interp.events.counts["workflow.cached"], 1)

    def test_editing_the_program_starts_a_new_run(self):
        source = textwrap.dedent("""
            workflow capital() {
                step ask {
                    answer = place
                }
                return answer
            }
            let result = capital()
        """)
        run_source(source.replace("place", '"Nairobi"'), self.state)
        interp = run_source(source.replace("place", '"Lusaka"'), self.state)
        self.assertEqual(interp.globals.vars["result"], "Lusaka")
        self.assertEqual(interp.events.counts["workflow.cached"], 0)

    def test_steps_in_loops_get_stable_keys(self):
        run_source(textwrap.dedent("""
            workflow w(items) {
                out = []
                for i in items {
                    step handle {
                        push(out, i * 2)
                    }
                }
                return out
            }
            let result = w([1, 2, 3])
        """), self.state)
        run = json.loads(next((Path(self.state) / "runs").glob("*.json")).read_text())
        self.assertEqual(list(run["steps"]), ["handle", "handle#1", "handle#2"])
        self.assertEqual(run["result"], [2, 4, 6])

    def test_service_restarts_then_alerts(self):
        interp = run_source(textwrap.dedent("""
            service worker with {restarts: 3, backoff: 0} {
                flaky("svc", 2)
                ok = true
            }
        """), self.state)
        self.assertTrue(interp.globals.vars["ok"])
        self.assertEqual(interp.events.counts["service.restarting"], 2)

        with self.assertRaises(ServiceFailedError):
            run_source('service doomed with {restarts: 1, backoff: 0} {\n fail("boom")\n}', self.state)

    def test_step_outside_workflow_is_rejected(self):
        with self.assertRaises(Exception):
            run_source("step s {\n x = 1\n}", self.state)


class InfrastructureTests(unittest.TestCase):
    SOURCE = textwrap.dedent("""
        resource group rg {
            location = "eastus"
        }
        resource storage data {
            resource_group_name = rg
        }
        deploy dev {
            put(data, "k1", {n: 1})
            got = get(data, "k1")
        }
    """)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_deploy_reconciles_and_storage_roundtrips(self):
        interp = run_source(self.SOURCE, self.state)
        self.assertEqual(interp.globals.vars["got"], {"n": 1})
        self.assertEqual(interp.events.counts["resource.created"], 2)

        interp = make_interp(self.state, mode="plan")
        interp.run(parse(self.SOURCE))
        self.assertTrue(all(c.action == "noop" for c in interp.infra.plan("dev")))

        interp = make_interp(self.state, mode="plan")
        interp.run(parse('resource group rg {\n location = "westus"\n}'))
        actions = {c.name: c.action for c in interp.infra.plan("dev")}
        self.assertEqual(actions, {"rg": "update", "data": "delete"})

    def test_unsafe_storage_keys_are_rejected(self):
        with self.assertRaises(Exception):
            run_source(self.SOURCE.replace('"k1", {n: 1}', '"../escape", 1'), self.state)

    def test_terraform_export_resolves_references(self):
        interp = make_interp(self.state, mode="declare")
        interp.run(parse(self.SOURCE))
        doc, warnings = to_terraform(interp.infra.declared.values())
        account = doc["resource"]["azurerm_storage_account"]["data"]
        self.assertEqual(account["resource_group_name"], "${azurerm_resource_group.rg.name}")
        self.assertEqual(warnings, [])


class CrashRecoveryTests(unittest.TestCase):
    def test_process_crash_resumes_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "job.agn"
            script.write_text(textwrap.dedent("""
                workflow w() {
                    step first {
                        x = 1
                    }
                    step second {
                        y = x + 1
                    }
                    return y
                }
                print(w())
            """), encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(SRC)}
            cmd = [sys.executable, "-m", "agen", "run", str(script)]

            crashed = subprocess.run(cmd + ["--crash-after", "first"], capture_output=True, text=True, env=env)
            self.assertEqual(crashed.returncode, 75, crashed.stderr)
            self.assertEqual(crashed.stdout.strip(), "")

            resumed = subprocess.run(cmd, capture_output=True, text=True, env=env)
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            self.assertEqual(resumed.stdout.strip(), "2")
            self.assertIn("step first replayed from journal", resumed.stderr)


if __name__ == "__main__":
    unittest.main()
