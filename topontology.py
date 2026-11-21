"""Measure the directed edges belonging to each supplied relation class."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from knowledgegraph import graph_mermaid, relation_cycles, topological_order

__version__ = "0.1.0"
TOOL_NAME = "topontology"

_DEGREE_KEYS = ("max_in", "max_out", "max_total", "sources", "sinks")


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


def _canonical_cycle(cycle):
    """Rotate a directed cycle so the smallest rotation is reported first."""
    if not cycle:
        return cycle
    closed = cycle[0] == cycle[-1] and len(cycle) > 1
    body = list(cycle[:-1] if closed else cycle)
    if not body:
        return list(cycle)
    rotations = [body[i:] + body[:i] for i in range(len(body))]
    best = min(rotations)
    return best + [best[0]] if closed else best


def _depth(edges):
    """Longest directed path length in edges, or null when TopologicalSorter finds a cycle.

    ``topological_order`` is ONTO's graphlib.TopologicalSorter check. The returned cycle is
    rotated to a stable start so the same edges do not change the document.
    """
    if not edges:
        return {"depth": 0, "cycle": None}
    found = topological_order(edges)
    if found["cycle"]:
        return {"depth": None, "cycle": _canonical_cycle(found["cycle"])}
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
        components = _weakly_connected_components(edges)
        edge_count = len(edges)
        node_count = len(nodes)
        results[category] = {
            "edges": edge_count,
            "incident_nodes": node_count,
            "multiple_sources": sorted(node for node in incoming if len(incoming[node]) > 1),
            "sample_cycles": relation_cycles(edges, limit=cycle_limit),
            "degree": _degrees(edges),
            "weakly_connected_components": components,
            "independent_cycles": edge_count - node_count + components["count"],
            "depth": depth["depth"],
            "order_cycle": depth["cycle"],
        }
    return {"classes": results, "unclassified_edges": unclassified}


def _delta_number(left, right):
    if left is None or right is None:
        return None
    return right - left


def delta_metrics(left, right):
    """Right minus left. ``depth`` is null when either side has a cycle."""
    delta = {
        "edges": right["edges"] - left["edges"],
        "incident_nodes": right["incident_nodes"] - left["incident_nodes"],
        "independent_cycles": right["independent_cycles"] - left["independent_cycles"],
        "depth": _delta_number(left["depth"], right["depth"]),
        "weakly_connected_components": (
            right["weakly_connected_components"]["count"] - left["weakly_connected_components"]["count"]
        ),
        "degree": {key: right["degree"][key] - left["degree"][key] for key in _DEGREE_KEYS},
    }
    return delta


def compare_graphs(left, right):
    names = sorted(set(left["classes"]) | set(right["classes"]))
    return {
        "classes": {
            name: {
                "left": left["classes"][name],
                "right": right["classes"][name],
                "delta": delta_metrics(left["classes"][name], right["classes"][name]),
            }
            for name in names
        },
        "unclassified_edges": {
            "left": left["unclassified_edges"],
            "right": right["unclassified_edges"],
            "delta": right["unclassified_edges"] - left["unclassified_edges"],
        },
    }


def compare_classes(report, left_name, right_name):
    left = report["classes"][left_name]
    right = report["classes"][right_name]
    return {
        "comparison": {
            "left": {"class": left_name, "metrics": left},
            "right": {"class": right_name, "metrics": right},
            "delta": delta_metrics(left, right),
        }
    }


def file_record(path):
    return {"path": path.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def input_provenance(graph, classification, profile, *, class_name=None):
    record = {
        "graph": file_record(graph),
        "classification": file_record(classification),
        "profile": profile,
    }
    if class_name is not None:
        record["class"] = class_name
    return record


def document(body, provenance):
    return {"tool": {"name": TOOL_NAME, "version": __version__}, "provenance": provenance, **body}


def _node_labels(graph):
    labels = {}
    for node in graph.get("nodes") or []:
        if isinstance(node, str):
            labels.setdefault(node, node)
        elif isinstance(node, dict) and "id" in node:
            labels.setdefault(node["id"], node.get("name", node["id"]))
    return labels


def render_mermaid(graph, edges, provenance, class_name):
    """Class subgraph via ONTO ``graph_mermaid``, with provenance in Mermaid comments."""
    labels = _node_labels(graph)
    present = sorted({node for edge in edges for node in (edge["source"], edge["target"])})
    ordered = sorted(edges, key=lambda edge: (
        edge["source"], edge_relation(edge), edge["target"], edge.get("basis") or ""))
    diagram = graph_mermaid({
        "nodes": [{"id": node, "name": labels.get(node, node)} for node in present],
        "edges": ordered,
    })
    header = [
        f"{TOOL_NAME} {__version__}",
        f"graph {provenance['graph']['path']} sha256:{provenance['graph']['sha256']}",
        f"classification {provenance['classification']['path']} sha256:{provenance['classification']['sha256']}",
        f"profile {provenance['profile']}",
        f"class {class_name}",
    ]
    first, _, rest = diagram.partition("\n")
    comments = "\n".join(f"%% {line}" for line in header)
    return f"{first}\n{comments}\n{rest}"


def dump_json(body):
    return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _signed(value):
    if value is None:
        return "n/a"
    return f"{value:+d}"


def _provenance_lines(prefix, record):
    label = f"{prefix}_" if prefix else ""
    lines = [
        f"{label}graph: {record['graph']['path']}",
        f"{label}graph_sha256: {record['graph']['sha256']}",
        f"{label}classification: {record['classification']['path']}",
        f"{label}classification_sha256: {record['classification']['sha256']}",
        f"{label}profile: {record['profile']}",
    ]
    if "class" in record:
        lines.append(f"{label}class: {record['class']}")
    return lines


def _class_lines(title, row):
    lines = [f"{title}:"]
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
    sizes = ", ".join(str(size) for size in components["sizes"]) if components["sizes"] else "(none)"
    lines.append(f"  weakly_connected_components: {components['count']} ({sizes})")
    lines.append(f"  independent_cycles: {row['independent_cycles']}")
    if row["depth"] is None:
        cycle = " → ".join(row["order_cycle"]) if row["order_cycle"] else "cycle"
        lines.append(f"  depth: (undefined, {cycle})")
    else:
        lines.append(f"  depth: {row['depth']}")
    return lines


def _change(label, left, right, delta):
    return f"  {label}: {left} -> {right} ({_signed(delta)})"


def _comparison_lines(title, left, right, delta):
    lines = [f"{title}:"]
    lines.append(_change("edges", left["edges"], right["edges"], delta["edges"]))
    lines.append(_change("incident_nodes", left["incident_nodes"], right["incident_nodes"], delta["incident_nodes"]))
    lines.append(_change(
        "independent_cycles", left["independent_cycles"], right["independent_cycles"], delta["independent_cycles"]))
    left_components = left["weakly_connected_components"]["count"]
    right_components = right["weakly_connected_components"]["count"]
    lines.append(_change(
        "weakly_connected_components", left_components, right_components, delta["weakly_connected_components"]))
    degree = " ".join(
        f"{key} {left['degree'][key]} -> {right['degree'][key]} ({_signed(delta['degree'][key])})"
        for key in _DEGREE_KEYS
    )
    lines.append(f"  degree: {degree}")
    left_depth = "undefined" if left["depth"] is None else left["depth"]
    right_depth = "undefined" if right["depth"] is None else right["depth"]
    lines.append(_change("depth", left_depth, right_depth, delta["depth"]))
    return lines


def render_text(body):
    lines = [f"tool: {body['tool']['name']} {body['tool']['version']}"]
    provenance = body["provenance"]
    if "left" in provenance:
        lines.extend(_provenance_lines("left", provenance["left"]))
        lines.extend(_provenance_lines("right", provenance["right"]))
        if "comparison" in body:
            found = body["comparison"]
            lines.extend(_comparison_lines(
                f"{found['left']['class']} vs {found['right']['class']}",
                found["left"]["metrics"], found["right"]["metrics"], found["delta"]))
        else:
            unclassified = body["unclassified_edges"]
            lines.append(_change(
                "unclassified_edges", unclassified["left"], unclassified["right"], unclassified["delta"]).lstrip())
            for name in sorted(body["classes"]):
                row = body["classes"][name]
                lines.extend(_comparison_lines(name, row["left"], row["right"], row["delta"]))
    else:
        lines.extend(_provenance_lines("", provenance))
        lines.append(f"unclassified_edges: {body['unclassified_edges']}")
        for name in sorted(body["classes"]):
            lines.extend(_class_lines(name, body["classes"][name]))
    return "\n".join(lines) + "\n"


def _load_graph(path):
    graph = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict) or not isinstance(graph.get("edges"), list):
        raise ValueError("graph JSON must be an object with an edges array")
    return graph


def _load_classes(path, profile):
    table = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(table, dict) or profile not in table or not isinstance(table[profile], dict):
        raise ValueError(f"unknown profile: {profile}")
    return table[profile]


def _require_class(report, name, flag):
    if name not in report["classes"]:
        known = ", ".join(sorted(report["classes"])) or "(none)"
        raise ValueError(f"unknown class for {flag}: {name} (known: {known})")


def _resolve_format(args):
    if args.json and args.format not in (None, "json", "canonical"):
        raise ValueError("--json cannot be combined with a different --format")
    return args.format or "json"


def main(argv=None):
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("classification", type=Path)
    parser.add_argument("profile")
    parser.add_argument("--format", choices=("text", "json", "canonical", "mermaid"),
                        help="json and canonical write the same sorted JSON document (default: json); "
                             "text is a line summary; mermaid draws one class")
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("--cycle-limit", type=int, default=5, metavar="N",
                        help="maximum representative cycles to keep per class (default: 5)")
    parser.add_argument("--class", dest="class_name", help="class to draw, or the left side of --compare-class")
    parser.add_argument("--compare", type=Path, metavar="GRAPH",
                        help="compare this graph with another graph under the same classification and profile")
    parser.add_argument("--compare-class", metavar="CLASS",
                        help="compare --class with another class of the same graph")
    parser.add_argument("-o", "--output", type=Path, help="write the document to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        fmt = _resolve_format(args)
        if args.cycle_limit < 0:
            raise ValueError("--cycle-limit must be >= 0")
        if args.compare and args.compare_class:
            raise ValueError("use either --compare or --compare-class")
        if fmt == "mermaid" and (args.compare or args.compare_class):
            raise ValueError("--format mermaid reads one class of one graph")
        if fmt == "mermaid" and not args.class_name:
            raise ValueError("--format mermaid requires --class")
        if args.compare_class and not args.class_name:
            raise ValueError("--compare-class requires --class")
        classes = _load_classes(args.classification, args.profile)
        graph = _load_graph(args.graph)
        report = summarize(graph, classes, cycle_limit=args.cycle_limit)
        provenance = input_provenance(args.graph, args.classification, args.profile)
        if fmt == "mermaid":
            _require_class(report, args.class_name, "--class")
            chosen = [edge for edge in graph["edges"] if edge_relation(edge) in classes
                      and classes[edge_relation(edge)] == args.class_name]
            text = render_mermaid(graph, chosen, provenance, args.class_name)
        elif args.compare:
            other = _load_graph(args.compare)
            other_report = summarize(other, classes, cycle_limit=args.cycle_limit)
            body = compare_graphs(report, other_report)
            both = {
                "left": provenance,
                "right": input_provenance(args.compare, args.classification, args.profile),
            }
            text = dump_json(document(body, both)) if fmt in ("json", "canonical") else render_text(document(body, both))
        elif args.compare_class:
            _require_class(report, args.class_name, "--class")
            _require_class(report, args.compare_class, "--compare-class")
            body = compare_classes(report, args.class_name, args.compare_class)
            both = {
                "left": input_provenance(args.graph, args.classification, args.profile, class_name=args.class_name),
                "right": input_provenance(
                    args.graph, args.classification, args.profile, class_name=args.compare_class),
            }
            text = dump_json(document(body, both)) if fmt in ("json", "canonical") else render_text(document(body, both))
        else:
            if args.class_name:
                _require_class(report, args.class_name, "--class")
                report = {
                    "classes": {args.class_name: report["classes"][args.class_name]},
                    "unclassified_edges": report["unclassified_edges"],
                }
            body = document(report, provenance)
            text = render_text(body) if fmt == "text" else dump_json(body)
        if args.output is None:
            sys.stdout.write(text)
        else:
            args.output.write_text(text, encoding="utf-8")
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
