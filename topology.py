"""Measure the directed edges belonging to each supplied relation class."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from knowledgegraph import relation_cycles, topological_order


def edge_relation(edge):
    return edge["kind"] if "kind" in edge else edge["relation"]


def _degrees(edges):
    nodes = set()
    inbound, outbound = defaultdict(int), defaultdict(int)
    for edge in edges:
        source, target = edge["source"], edge["target"]
        nodes.update((source, target))
        outbound[source] += 1
        inbound[target] += 1
    if not nodes:
        return {"max_in": 0, "max_out": 0, "max_total": 0, "sources": 0, "sinks": 0}
    max_in = max(inbound[node] for node in nodes)
    max_out = max(outbound[node] for node in nodes)
    max_total = max(inbound[node] + outbound[node] for node in nodes)
    sources = sum(1 for node in nodes if outbound[node] and not inbound[node])
    sinks = sum(1 for node in nodes if inbound[node] and not outbound[node])
    return {"max_in": max_in, "max_out": max_out, "max_total": max_total,
            "sources": sources, "sinks": sinks}


def _weakly_connected_components(edges):
    adjacent = defaultdict(set)
    for edge in edges:
        adjacent[edge["source"]].add(edge["target"])
        adjacent[edge["target"]].add(edge["source"])
    remaining = set(adjacent)
    sizes = []
    while remaining:
        start = min(remaining)
        stack, seen = [start], {start}
        remaining.remove(start)
        while stack:
            node = stack.pop()
            for neighbour in adjacent[node]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    seen.add(neighbour)
                    stack.append(neighbour)
        sizes.append(len(seen))
    sizes.sort(reverse=True)
    return {"count": len(sizes), "sizes": sizes}


def _depth(edges):
    """Longest directed path length in edges, or null when the class has a cycle."""
    if not edges:
        return {"depth": 0, "cycle": None}
    found = topological_order(edges)
    if found["cycle"]:
        return {"depth": None, "cycle": found["cycle"]}
    outgoing = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
    dist = {node: 0 for node in found["order"]}
    for node in found["order"]:
        for target in outgoing[node]:
            dist[target] = max(dist[target], dist[node] + 1)
    return {"depth": max(dist.values(), default=0), "cycle": None}


def summarize(graph, classes, *, cycle_limit=5):
    groups = {category: [] for category in sorted(set(classes.values()))}
    unclassified = 0
    for edge in graph["edges"]:
        relation = edge_relation(edge)
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
        depth = _depth(edges)
        results[category] = {
            "edges": len(edges),
            "incident_nodes": len(nodes),
            "multiple_sources": sorted(node for node in incoming if len(incoming[node]) > 1),
            "sample_cycles": relation_cycles(edges, limit=cycle_limit),
            "degree": _degrees(edges),
            "weakly_connected_components": _weakly_connected_components(edges),
            "depth": depth["depth"],
            "order_cycle": depth["cycle"],
        }
    return {"classes": results, "unclassified_edges": unclassified}


def render(report, *, fmt):
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    lines = [f"unclassified_edges: {report['unclassified_edges']}"]
    for category, row in report["classes"].items():
        lines.append(f"{category}:")
        lines.append(f"  edges: {row['edges']}")
        lines.append(f"  incident_nodes: {row['incident_nodes']}")
        sources = ", ".join(row["multiple_sources"]) if row["multiple_sources"] else "(none)"
        lines.append(f"  multiple_sources: {sources}")
        if row["sample_cycles"]:
            cycles = "; ".join(" → ".join(cycle) for cycle in row["sample_cycles"])
        else:
            cycles = "(none)"
        lines.append(f"  sample_cycles: {cycles}")
        degree = row["degree"]
        lines.append(
            "  degree: "
            f"max_in={degree['max_in']} max_out={degree['max_out']} "
            f"max_total={degree['max_total']} sources={degree['sources']} sinks={degree['sinks']}"
        )
        components = row["weakly_connected_components"]
        sizes = ", ".join(str(n) for n in components["sizes"]) if components["sizes"] else "(none)"
        lines.append(f"  weakly_connected_components: {components['count']} ({sizes})")
        if row["depth"] is None:
            cycle = " → ".join(row["order_cycle"]) if row["order_cycle"] else "cycle"
            lines.append(f"  depth: (undefined, {cycle})")
        else:
            lines.append(f"  depth: {row['depth']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("classification", type=Path)
    parser.add_argument("profile")
    parser.add_argument("--format", choices=("text", "json"), default="json",
                        help="text renders a human document; json writes one JSON document (default: json)")
    parser.add_argument("--json", action="store_const", const="json", dest="format",
                        help="shorthand for --format json")
    parser.add_argument("--cycle-limit", type=int, default=5, metavar="N",
                        help="maximum representative cycles to keep per class (default: 5)")
    args = parser.parse_args(argv)
    try:
        classes = json.loads(args.classification.read_text(encoding="utf-8"))[args.profile]
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
        if not isinstance(graph, dict) or "edges" not in graph:
            raise ValueError("graph JSON must be an object with an edges array")
        if args.cycle_limit < 0:
            raise ValueError("--cycle-limit must be >= 0")
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
        print(error, file=sys.stderr)
        return 1
    report = summarize(graph, classes, cycle_limit=args.cycle_limit)
    sys.stdout.write(render(report, fmt=args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
