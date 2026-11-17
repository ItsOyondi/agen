import io
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from helpers import make_interp

from agen import parse
from agen.errors import AgenRuntimeError, LexError, ParseError


MATH = """
fn add(a, b) { return a + b }
class Counter {
    init() { self.n = 0 }
    fn inc() { self.n = self.n + 1; return self.n }
}
"""


class ImportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "mathx.agn").write_text(MATH, encoding="utf-8")
        lib = self.root / "lib"
        lib.mkdir()
        (lib / "extra.agn").write_text("fn twice(n) { return n * 2 }\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def execute(self, source: str):
        program = self.root / "main.agn"
        program.write_text(textwrap.dedent(source), encoding="utf-8")
        interp = make_interp(self.root / "state", source_path=program)
        buf = io.StringIO()
        with redirect_stdout(buf):
            interp.run(parse(program.read_text(encoding="utf-8")))
        return interp, buf.getvalue()

    def test_at_import_binds_functions_and_classes(self):
        interp, output = self.execute("""
            @at mathx @import add, Counter
            let total = add(2, 3)
            let counter = Counter()
            let n = counter.inc()
            @imported
        """)
        self.assertEqual(interp.globals.vars["total"], 5)
        self.assertEqual(interp.globals.vars["n"], 1)
        self.assertIn("@at mathx @import add, Counter", output)
        self.assertIn("imports in main.agn:", output)

    def test_import_binds_the_library_namespace(self):
        interp, _ = self.execute("""
            @import mathx
            let total = mathx.add(4, 6)
            let counter = mathx.Counter()
            let n = counter.inc()
        """)
        self.assertEqual(interp.globals.vars["total"], 10)
        self.assertEqual(interp.globals.vars["n"], 1)
        self.assertEqual(str(interp.globals.vars["mathx"]), "<library mathx>")

    def test_import_finds_a_library_folder_and_a_dotted_path(self):
        interp, _ = self.execute("""
            @import extra
            @at lib.extra @import twice
            let a = extra.twice(3)
            let b = twice(4)
        """)
        self.assertEqual(interp.globals.vars["a"], 6)
        self.assertEqual(interp.globals.vars["b"], 8)

    def test_a_library_can_import_another_library(self):
        (self.root / "use.agn").write_text("@at mathx @import add\nfn use(n) { return add(n, 1) }\n", encoding="utf-8")
        interp, _ = self.execute("@at use @import use\nlet out = use(10)\n")
        self.assertEqual(interp.globals.vars["out"], 11)

    def test_missing_library_and_missing_name(self):
        with self.assertRaises(AgenRuntimeError):
            self.execute("@import missing\n")
        with self.assertRaises(AgenRuntimeError):
            self.execute("@at mathx @import missing\n")

    def test_rejects_a_path_that_leaves_the_library_roots(self):
        with self.assertRaises(AgenRuntimeError):
            self.execute('@import "../secret"\n')

    def test_import_cycle_is_an_error(self):
        (self.root / "a.agn").write_text("@import b\n", encoding="utf-8")
        (self.root / "b.agn").write_text("@import a\n", encoding="utf-8")
        with self.assertRaises(AgenRuntimeError) as ctx:
            self.execute("@import a\n")
        self.assertIn("import cycle", str(ctx.exception))

    def test_directive_syntax(self):
        with self.assertRaises(LexError):
            parse("@export mathx\n")
        with self.assertRaises(ParseError):
            parse("@at mathx add\n")


if __name__ == "__main__":
    unittest.main()
