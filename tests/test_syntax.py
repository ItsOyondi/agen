import unittest

from helpers import parse  # noqa: F401  (sets up sys.path)

from agen import nodes as n
from agen.errors import LexError, ParseError
from agen.lexer import TokenType, tokenize


class LexerTests(unittest.TestCase):
    def test_tokens_and_escapes(self):
        tokens = tokenize('let x = 1.5 # comment\nprint("a\\n\\"b\\"")')
        kinds = [t.type for t in tokens]
        self.assertEqual(kinds[:5], [TokenType.KEYWORD, TokenType.IDENT, TokenType.OP,
                                     TokenType.NUMBER, TokenType.NEWLINE])
        self.assertEqual(tokens[3].value, 1.5)
        self.assertEqual([t.value for t in tokens if t.type is TokenType.STRING], ['a\n"b"'])

    def test_newlines_ignored_inside_parens_and_brackets(self):
        tokens = tokenize("f(1,\n 2)\n[3,\n4]")
        newlines = [t for t in tokens if t.type is TokenType.NEWLINE]
        self.assertEqual(len(newlines), 2)  # after f(...) and the trailing one

    def test_unterminated_string(self):
        with self.assertRaises(LexError):
            tokenize('"oops')


class ParserTests(unittest.TestCase):
    def test_step_with_options_and_fallback_on_next_line(self):
        program = parse(
            "workflow w() {\n"
            "  step s with {retries: 3, timeout: 2} {\n    x = 1\n  }\n"
            "  fallback {\n    x = 0\n  }\n"
            "}\n"
        )
        step = program[0].body[0]
        self.assertIsInstance(step, n.Step)
        self.assertEqual(step.name, "s")
        self.assertIsInstance(step.options, n.MapLit)
        self.assertEqual(len(step.fallback), 1)

    def test_resource_agent_deploy(self):
        program = parse(
            'resource storage data {\n  location = "eastus"\n  tier = "Standard"\n}\n'
            'agent bot { instructions = "hi" }\n'
            'deploy prod with {provider: "local"} {\n  print(1)\n}\n'
        )
        res, agent, deploy = program
        self.assertEqual((res.kind, res.name, [k for k, _ in res.config]), ("storage", "data", ["location", "tier"]))
        self.assertEqual(agent.name, "bot")
        self.assertIsInstance(deploy, n.Deploy)

    def test_else_if_chain(self):
        program = parse("if a {\n} else if b {\n} else {\n}\n")
        self.assertIsInstance(program[0].else_body[0], n.If)

    def test_precedence(self):
        expr = parse("x = 1 + 2 * 3")[0].value
        self.assertEqual(expr.op, "+")
        self.assertEqual(expr.right.op, "*")

    def test_class_with_inheritance(self):
        program = parse(
            "class Number {\n"
            "  init(value) {\n    self.value = value\n  }\n"
            "  fn add(amount) {\n    return self.value + amount\n  }\n"
            "}\n"
            "class Total extends Number {\n"
            "  fn add(amount) {\n    return super.add(amount)\n  }\n"
            "}\n"
        )
        base, child = program
        self.assertEqual((base.name, base.parent, [m.name for m in base.methods]),
                         ("Number", None, ["init", "add"]))
        self.assertEqual(child.parent, "Number")
        self.assertIsInstance(child.methods[0].body[0].value.callee, n.Member)

    def test_duplicate_method_is_rejected(self):
        with self.assertRaises(ParseError):
            parse("class A {\n fn f() {}\n fn f() {}\n}")

    def test_errors_report_line(self):
        with self.assertRaises(ParseError) as ctx:
            parse("let x = 1\n1 + = 2")
        self.assertEqual(ctx.exception.line, 2)
        with self.assertRaises(ParseError):
            parse("f() = 3")


if __name__ == "__main__":
    unittest.main()
