"""Connect declared graph properties to the Lean witness predicates.

The oracle uses finite relation algebra, independently of the production
witness searches. Existing ONTO and OnTautoLogy sibling checkouts are required;
no installed third-party Python package or Lean executable is needed.
"""

import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import topontology as top


def relations(nodes):
    pairs = list(itertools.product(nodes, repeat=2))
    return [frozenset(pair for index, pair in enumerate(pairs) if mask & (1 << index))
            for mask in range(2 ** len(pairs))]


def edges(pairs, relation):
    return [{"source": source, "target": target, "kind": relation}
            for source, target in sorted(pairs)]


def compose(left, right):
    return {(a, c) for a, b in left for middle, c in right if b == middle}


def endpoints(pairs):
    return {node for pair in pairs for node in pair}


def evaluate(graph, classes, declarations):
    return {(row["scope"], row["name"], row["property"]): row
            for row in top.evaluate_properties(graph, classes, {"properties": declarations})}


class PropertyWitnessBridge(unittest.TestCase):
    def test_all_three_node_graphs_match_symmetry_and_reflexive_closure(self):
        nodes = ("A", "B", "C")
        diagonal = {(node, node) for node in nodes}
        declarations = {"relations": {"r": {"symmetric": {}, "transitive": {}}}}
        for index, pairs in enumerate(relations(nodes)):
            with self.subTest(graph=index):
                report = evaluate({"edges": edges(pairs, "r")}, {"r": "shape"}, declarations)
                symmetric = report[("relation", "r", "symmetric")]
                expected = pairs == {(b, a) for a, b in pairs}
                self.assertEqual(symmetric["holds"], expected)
                self.assertEqual(symmetric["status"], "holds" if expected else "fails")
                if expected:
                    self.assertIsNone(symmetric["witness"])
                else:
                    witness = symmetric["witness"]
                    a, b = witness["nodes"]
                    self.assertIn((a, b), pairs)
                    self.assertNotIn((b, a), pairs)
                    self.assertEqual(witness["edges"], [{"source": a, "target": b, "relation": "r"}])

                closure = pairs | diagonal
                expected = compose(closure, closure) <= closure
                transitive = report[("relation", "r", "transitive")]
                self.assertEqual(transitive["holds"], expected)
                self.assertEqual(transitive["status"], "holds" if expected else "fails")
                if expected:
                    self.assertIsNone(transitive["witness"])
                else:
                    witness = transitive["witness"]
                    a, b, c = witness["nodes"]
                    self.assertIn((a, b), pairs)
                    self.assertIn((b, c), pairs)
                    self.assertNotEqual(a, c)
                    self.assertNotIn((a, c), pairs)
                    self.assertEqual(witness["missing"], {"source": a, "target": c})
                    self.assertEqual(witness["edges"], [
                        {"source": a, "target": b, "relation": "r"},
                        {"source": b, "target": c, "relation": "r"},
                    ])

    def test_all_two_node_relation_pairs_match_inverse_and_endpoint_disjointness(self):
        cases = relations(("A", "B"))
        declarations = {"relations": {"r": {
            "inverse_of": {"relation": "s"}, "disjoint_with": [{"relation": "s"}],
        }}}
        for left_index, left in enumerate(cases):
            for right_index, right in enumerate(cases):
                with self.subTest(left=left_index, right=right_index):
                    report = evaluate({"edges": edges(left, "r") + edges(right, "s")},
                                      {"r": "left", "s": "right"}, declarations)
                    inverse = report[("relation", "r", "inverse_of")]
                    expected = left == {(b, a) for a, b in right}
                    self.assertEqual(inverse["holds"], expected)
                    if expected:
                        self.assertIsNone(inverse["witness"])
                    else:
                        witness = inverse["witness"]
                        self.assertEqual(len(witness["edges"]), 1)
                        edge = witness["edges"][0]
                        a, b = edge["source"], edge["target"]
                        self.assertEqual(witness["nodes"], [a, b])
                        own, other = (left, right) if edge["relation"] == "r" else (right, left)
                        self.assertIn(edge["relation"], {"r", "s"})
                        self.assertIn((a, b), own)
                        self.assertNotIn((b, a), other)

                    disjoint = report[("relation", "r", "disjoint_with")]
                    shared = endpoints(left) & endpoints(right)
                    self.assertEqual(disjoint["holds"], not shared)
                    if not shared:
                        self.assertIsNone(disjoint["witness"])
                    else:
                        witness = disjoint["witness"]
                        self.assertEqual(len(witness["nodes"]), 1)
                        node = witness["nodes"][0]
                        self.assertIn(node, shared)
                        self.assertEqual([edge["relation"] for edge in witness["edges"]], ["r", "s"])
                        for edge, pairs in zip(witness["edges"], (left, right)):
                            pair = edge["source"], edge["target"]
                            self.assertIn(pair, pairs)
                            self.assertIn(node, pair)

    def test_class_paths_mix_relations_but_ignore_outside_chords(self):
        classes = {"r": "selected", "s": "selected", "outside": "other"}
        declarations = {"classes": {"selected": {"transitive": {}}},
                        "relations": {"r": {"transitive": {}}, "s": {"transitive": {}}}}
        graph = {"edges": edges({("A", "B")}, "r") + edges({("B", "C")}, "s")
                 + edges({("A", "C")}, "outside")}
        report = evaluate(graph, classes, declarations)
        self.assertTrue(report[("relation", "r", "transitive")]["holds"])
        self.assertTrue(report[("relation", "s", "transitive")]["holds"])
        failure = report[("class", "selected", "transitive")]
        self.assertFalse(failure["holds"])
        self.assertEqual(failure["witness"]["missing"], {"source": "A", "target": "C"})
        self.assertEqual([edge["relation"] for edge in failure["witness"]["edges"]], ["r", "s"])
        graph["edges"] += edges({("A", "C")}, "r")
        self.assertTrue(evaluate(graph, classes, declarations)[("class", "selected", "transitive")]["holds"])

    def test_class_symmetry_is_checked_on_the_selected_union(self):
        classes = {"r": "selected", "s": "selected"}
        declarations = {"classes": {"selected": {"symmetric": {}}},
                        "relations": {"r": {"symmetric": {}}, "s": {"symmetric": {}}}}
        graph = {"edges": edges({("A", "B")}, "r") + edges({("B", "A")}, "s")}
        report = evaluate(graph, classes, declarations)
        self.assertTrue(report[("class", "selected", "symmetric")]["holds"])
        self.assertFalse(report[("relation", "r", "symmetric")]["holds"])
        self.assertFalse(report[("relation", "s", "symmetric")]["holds"])
        # The Lean implication also holds: individually symmetric relations
        # remain symmetric when pooled into the selected class.
        graph = {"edges": edges({("A", "B"), ("B", "A")}, "r")
                 + edges({("B", "C"), ("C", "B")}, "s")}
        self.assertTrue(all(row["holds"] for row in evaluate(graph, classes, declarations).values()))

    def test_reciprocal_pair_does_not_require_self_loops(self):
        pairs = {("A", "B"), ("B", "A")}
        self.assertFalse(compose(pairs, pairs) <= pairs)
        report = evaluate({"edges": edges(pairs, "r")}, {"r": "shape"},
                          {"relations": {"r": {"transitive": {}}}})
        self.assertTrue(report[("relation", "r", "transitive")]["holds"])

    def test_duplicates_order_and_isolated_nodes_do_not_change_properties(self):
        original = edges({("A", "B"), ("B", "C")}, "r") + edges({("C", "A")}, "s")
        declarations = {"relations": {"r": {"symmetric": {}, "transitive": {},
                        "inverse_of": {"relation": "s"}, "disjoint_with": [{"relation": "s"}]}}}
        classes = {"r": "shape", "s": "shape"}
        baseline = evaluate({"edges": original}, classes, declarations)
        changed = {"edges": list(reversed(original)) + original,
                   "nodes": [{"id": "unrelated"}]}
        self.assertEqual(evaluate(changed, classes, declarations), baseline)

    def test_unsupported_endpoints_stay_unverified_and_keep_their_types(self):
        graph = {"edges": [{"source": 1, "target": "1", "kind": "r"}]}
        report = evaluate(graph, {"r": "shape"}, {"relations": {"r": {
            "acyclic": {}, "transitive": {}, "symmetric": {},
        }}})
        for prop in ("acyclic", "transitive"):
            result = report[("relation", "r", prop)]
            self.assertEqual(result["status"], "not applicable")
            self.assertIsNone(result["holds"])
            self.assertIsNone(result["witness"])
        symmetry = report[("relation", "r", "symmetric")]
        self.assertFalse(symmetry["holds"])
        self.assertEqual(symmetry["witness"]["nodes"], [1, "1"])


if __name__ == "__main__":
    unittest.main()
