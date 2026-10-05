"""Test pure helpers without initializing PostgreSQL or fetching prices."""
import ast
from pathlib import Path
import re
import unicodedata
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = {"canonical_unit", "offer_sort_key", "sort_offers", "candidate_unit_price"}
tree = ast.parse((ROOT / "backend/app.py").read_text())
module = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in HELPERS], type_ignores=[])
scope = {"re": re, "unicodedata": unicodedata}
exec(compile(module, "comparison_helpers", "exec"), scope)


class ComparisonTests(unittest.TestCase):
    def test_unit_price_has_priority_over_pack_price(self):
        offers, unit = scope["sort_offers"]([
            {"price": 2, "per_unit": 8, "unit": "kg"},
            {"price": 3, "per_unit": 6, "unit": "kg"},
        ])
        self.assertEqual(unit, "kg")
        self.assertEqual(offers[0]["price"], 3)

    def test_missing_unit_data_falls_back_to_pack_price(self):
        offers, unit = scope["sort_offers"]([{"price": 3}, {"price": 2}])
        self.assertIsNone(unit)
        self.assertEqual(offers[0]["price"], 2)

    def test_convert_grams_and_millilitres(self):
        self.assertEqual(scope["candidate_unit_price"]({"name": "Product 500 g", "price": 2}), (4, "kg"))
        self.assertEqual(scope["candidate_unit_price"]({"name": "Product 250 ml", "price": 1}), (4, "L"))

    def test_unit_normalization(self):
        self.assertEqual(scope["canonical_unit"]("litros"), "L")
        self.assertEqual(scope["canonical_unit"]("unidades"), "u")


if __name__ == "__main__":
    unittest.main()
