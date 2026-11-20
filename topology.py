"""Measure the directed edges belonging to each supplied relation class."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from knowledgegraph import relation_cycles


def summarize(graph, classes):
    groups = {category: [] for category in sorted(set(classes.values()))}
    unclassified = 0
    for edge in graph["edges"]:
        relation = edge["kind"] if "kind" in edge else edge["relation"]
        if relation in classes:
            groups[classes[relation]].append(edge)
        else:
            unclassified += 1
    results = {}
    for category, edges in groups.items():
        incoming = defaultdict(set)
        nodes = set()
        for edge in edges:
            source, target = edge["source"], edge["target"]
            nodes.update((source, target))
            incoming[target].add(source)
        results[category] = {
            "edges": len(edges),
            "incident_nodes": len(nodes),
            "multiple_sources": sorted(node for node in incoming if len(incoming[node]) > 1),
            "sample_cycles": relation_cycles(edges),
        }
    return {"classes": results, "unclassified_edges": unclassified}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("classification", type=Path)
    parser.add_argument("profile")
    args = parser.parse_args()
    classes = json.loads(args.classification.read_text(encoding="utf-8"))[args.profile]
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    print(json.dumps(summarize(graph, classes), ensure_ascii=False, indent=2))
