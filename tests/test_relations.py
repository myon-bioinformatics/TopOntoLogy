"""taxontoy-relations/1 and /2 profiles, including the properties-key regression."""
import json
import tempfile
import unittest
from pathlib import Path

import _clear_roots
import topontology
from test_series import load_mutant
from test_topontology import ROOT, run_cli

V1 = ROOT / "tests/fixtures/relations-v1.json"
V1_PROPERTIES = ROOT / "tests/fixtures/relations-v1-properties.json"
V2 = ROOT / "tests/fixtures/relations-v2.json"
V3 = ROOT / "tests/fixtures/relations-v3.json"
CONCEPTS = {"narrower": "hierarchy", "similarTo": "similarity"}


class RelationTableTests(unittest.TestCase):
    def test_version_2_metadata_is_not_a_profile(self):
        # Shape of TaxOntOy otl-relations.json at the pinned /2 table: profiles
        # sit beside direction, excluded, and properties.
        self.assertEqual(topontology._load_classes(V2, "concepts"), CONCEPTS)
        self.assertEqual(topontology._load_classes(V2, "audit"), {"dependsOn": "dependency"})
        self.assertEqual(topontology._load_classes(V2, "cases"), {"candidate": "hypothesis"})
        for name in ("properties", "direction", "excluded", "schema"):
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(V2, name)
            self.assertEqual(str(raised.exception), f"unknown profile: {name}")

    def test_version_1_profiles_still_load(self):
        self.assertEqual(topontology._load_classes(V1, "concepts"), CONCEPTS)
        self.assertEqual(topontology._load_classes(V1, "audit"), {"dependsOn": "dependency"})
        for name in ("direction", "excluded", "schema", "properties"):
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(V1, name)
            self.assertEqual(str(raised.exception), f"unknown profile: {name}")

    def test_version_1_properties_string_map_is_a_profile(self):
        # A flat relation-to-class map is user data. Reserving the key must
        # not hide it. Declaration objects stay metadata.
        self.assertEqual(set(json.loads(V1_PROPERTIES.read_text(encoding="utf-8"))["properties"]), set(CONCEPTS))
        self.assertEqual(topontology._load_classes(V1_PROPERTIES, "concepts"), CONCEPTS)
        self.assertEqual(topontology._load_classes(V1_PROPERTIES, "properties"), CONCEPTS)

    def test_unknown_relation_schema_is_rejected(self):
        for name in ("concepts", "properties"):
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(V3, name)
            self.assertEqual(str(raised.exception), "unknown relation schema: taxontoy-relations/3")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.json"
            path.write_text(json.dumps({"schema": "other", "demo": CONCEPTS}), encoding="utf-8")
            self.assertEqual(topontology._load_classes(path, "demo"), CONCEPTS)

    def test_mixed_properties_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(json.dumps({
                "schema": "taxontoy-relations/2",
                "concepts": CONCEPTS,
                "properties": {
                    "narrower": "hierarchy",
                    "classes": {"hierarchy": {"acyclic": {}}},
                },
            }), encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                topontology._load_classes(path, "properties")
            self.assertEqual(
                str(raised.exception),
                "properties mixes reserved metadata with user-defined data",
            )
            code, out, err = run_cli("tests/fixtures/graph.json", str(path), "properties")
            self.assertEqual(code, 1)
            self.assertEqual(err, "properties mixes reserved metadata with user-defined data\n")
            self.assertNotIn("Traceback", err)
            self.assertEqual(out, "")

    def test_non_object_acyclic_is_user_data(self):
        table = {"properties": {"classes": {"hierarchy": {"acyclic": "note"}}}}
        self.assertFalse(topontology._class_declares_acyclic(table, "hierarchy"))
        table["properties"]["classes"]["hierarchy"]["acyclic"] = {}
        self.assertTrue(topontology._class_declares_acyclic(table, "hierarchy"))
        self.assertFalse(topontology._class_declares_acyclic(
            {"properties": CONCEPTS}, "hierarchy"))

    def test_unversioned_table_can_name_a_profile_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(json.dumps({"properties": CONCEPTS}), encoding="utf-8")
            self.assertEqual(topontology._load_classes(path, "properties"), CONCEPTS)

    def test_cli_rejects_properties_and_keeps_concepts(self):
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v2.json", "properties")
        self.assertEqual(code, 1)
        self.assertEqual(err, "unknown profile: properties\n")
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v2.json", "concepts",
            "--format", "json")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["classes"]["hierarchy"]["independent_cycles"], 1)
        self.assertEqual(body["classes"]["similarity"]["edges"], 1)
        self.assertEqual(len(body["collapsed_edges"]), 1)
        self.assertEqual(body["unclassified_edges"], 1)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v1.json", "concepts")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["classes"]["hierarchy"]["depth"], 2)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v1.json", "direction")
        self.assertEqual(code, 1)
        self.assertEqual(err, "unknown profile: direction\n")
        self.assertNotIn("Traceback", err)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v1-properties.json", "properties")
        self.assertEqual(code, 0, err)
        body = json.loads(out)
        self.assertEqual(body["classes"]["hierarchy"]["depth"], 2)
        self.assertEqual(len(body["collapsed_edges"]), 1)
        self.assertEqual(body["classes"]["similarity"]["edges"], 1)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v1-properties.json", "concepts")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["classes"]["hierarchy"]["depth"], 2)
        code, out, err = run_cli(
            "tests/fixtures/graph.json", "tests/fixtures/relations-v3.json", "concepts")
        self.assertEqual(code, 1)
        self.assertEqual(err, "unknown relation schema: taxontoy-relations/3\n")
        self.assertNotIn("Traceback", err)
        self.assertEqual(out, "")


class PropertiesMutationTests(unittest.TestCase):
    def test_treating_properties_as_a_profile_is_killed(self):
        mutant = load_mutant(
            "if profile in reserved or not isinstance(found, dict):",
            "if not isinstance(found, dict):",
        )
        with self.assertRaises(ValueError):
            topontology._load_classes(V2, "properties")
        leaked = mutant._load_classes(V2, "properties")
        self.assertEqual(set(leaked), {"classes", "relations"})
        self.assertIn("hierarchy", leaked["classes"])
        self.assertNotIn("narrower", leaked)
        self.assertEqual(mutant._load_classes(V2, "direction")["narrower"], "forward")
        self.assertEqual(mutant._load_classes(V1, "concepts"), CONCEPTS)

    def test_unreserved_version_1_properties_is_killed(self):
        mutant = load_mutant(
            '"taxontoy-relations/1": frozenset({"schema", "direction", "excluded", "properties"}),',
            '"taxontoy-relations/1": frozenset({"schema", "direction", "excluded"}),',
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.json"
            path.write_text(json.dumps({
                "schema": "taxontoy-relations/1",
                "concepts": CONCEPTS,
                "properties": {"classes": {"hierarchy": {"acyclic": {}}}},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                topontology._load_classes(path, "properties")
            leaked = mutant._load_classes(path, "properties")
        self.assertIn("classes", leaked)
        self.assertEqual(topontology._load_classes(V1_PROPERTIES, "properties"), CONCEPTS)

    def test_accepting_an_unknown_relation_schema_is_killed(self):
        mutant = load_mutant(
            'raise ValueError(f"unknown relation schema: {schema}")',
            "reserved = ()",
        )
        with self.assertRaises(ValueError) as raised:
            topontology._load_classes(V3, "concepts")
        self.assertIn("taxontoy-relations/3", str(raised.exception))
        self.assertEqual(mutant._load_classes(V3, "concepts"), CONCEPTS)

    def test_treating_a_string_acyclic_as_a_declaration_is_killed(self):
        mutant = load_mutant(
            'return isinstance(body.get("acyclic"), dict)',
            'return "acyclic" in body',
        )
        table = {"properties": {"classes": {"hierarchy": {"acyclic": "note"}}}}
        self.assertFalse(topontology._class_declares_acyclic(table, "hierarchy"))
        self.assertTrue(mutant._class_declares_acyclic(table, "hierarchy"))

    def test_swallowing_a_properties_profile_is_killed(self):
        mutant = load_mutant(
            '        if role == "profile":\n            return found\n',
            '        if role == "metadata":\n            return found\n',
        )
        self.assertEqual(topontology._load_classes(V1_PROPERTIES, "properties"), CONCEPTS)
        with self.assertRaises(ValueError):
            mutant._load_classes(V1_PROPERTIES, "properties")
        self.assertIn("classes", mutant._load_classes(V2, "properties"))


if __name__ == "__main__":
    unittest.main()
