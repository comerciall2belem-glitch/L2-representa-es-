"""Testes isolados de regras fiscais sem acessar banco de produção."""
import ast
from pathlib import Path
import unittest

SOURCE = ast.parse(Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8"))
FUNCTIONS = {node.name: node for node in SOURCE.body if isinstance(node, ast.FunctionDef)}
namespace = {}
exec(compile(ast.Module(body=[FUNCTIONS["valid_cnpj"], FUNCTIONS["normalize_uf"]], type_ignores=[]), "<fiscal>", "exec"), namespace)

class FiscalValidationTests(unittest.TestCase):
    def test_cnpj_valid(self):
        self.assertTrue(namespace["valid_cnpj"]("04.252.011/0001-10"))

    def test_cnpj_invalid(self):
        for value in ("", "00.000.000/0000-00", "04.252.011/0001-11", "123", None):
            with self.subTest(value=value):
                self.assertFalse(namespace["valid_cnpj"](value))

    def test_uf(self):
        for raw, expected in (("PA", "PA"), ("Pará", "PA"), ("AP", "AP"), ("Amapá", "AP"), ("SP", None), ("", None)):
            with self.subTest(raw=raw):
                self.assertEqual(namespace["normalize_uf"](raw), expected)

if __name__ == "__main__":
    unittest.main()
