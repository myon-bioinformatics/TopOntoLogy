"""Measure the directed edges belonging to each supplied relation class."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from knowledgegraph import graph_mermaid, relation_cycles, topological_order

__version__ = "0.2.0"
TOOL_NAME = "topontology"

_DEGREE_KEYS = ("max_in", "max_out", "max_total", "sources", "sinks")


def edge_relation(edge):
    if "kind" in edge:
        return edge["kind"]
    if "relation" in edge:
        return edge["relation"]
    raise ValueError("edge is missing both 'kind' and 'relation'")


def node_sort_key(node):
    """Comparable key for mixed JSON ID types.

    Summaries and JSON keep original ID types, so ``1`` and ``"1"`` stay
    distinct vertices. Component seeds and other sorts use this key. Text and
    Mermaid convert IDs only for display, via ``node_text``.
    """
    return (type(node).__name__, str(node))


def node_text(node):
    """Display form of a node ID. ``1`` and ``"1"`` both become ``'1'``."""
    return node if isinstance(node, str) else str(node)


def _ends(edge):
    try:
        return edge["source"], edge["target"]
    except KeyError as error:
        raise ValueError(f"edge is missing {error.args[0]!r}") from error


def _tag(node):
    """Always-comparable stand-in so ONTO can sort mixed ID types."""
    return (type(node).__name__, node)


def _untag(token):
    return token[1]


def _tagged_edges(edges):
    tagged = []
    for edge in edges:
        item = dict(edge)
        item["source"], item["target"] = _tag(edge["source"]), _tag(edge["target"])
        tagged.append(item)
    return tagged


def _untag_path(path):
    if not path:
        return path
    return [_untag(node) for node in path]


def _mermaid_terms(nodes):
    """Unique string IDs for ONTO ``graph_mermaid``.

    String IDs are kept as-is so existing diagrams do not change. When two
    original IDs would share the same ``str()`` form, non-string IDs are
    written as ``{type}:{text}``.
    """
    counts = {}
    for node in nodes:
        text = node_text(node)
        counts[text] = counts.get(text, 0) + 1
    terms = {}
    for node in nodes:
        text = node_text(node)
        if counts[text] == 1 or isinstance(node, str):
            terms[node] = text
        else:
            terms[node] = f"{type(node).__name__}:{text}"
    return terms


def _mermaid_names(nodes, labels, terms):
    """Box labels for ONTO ``graph_mermaid``.

    Unique display strings are left unchanged. When two nodes would share a
    box label, the disambiguated term from ``_mermaid_terms`` is used so
    unnamed ``1`` and ``"1"`` stay distinguishable after ONTO renumbers IDs.
    """
    display = {node: node_text(labels.get(node, node)) for node in nodes}
    counts = {}
    for node in nodes:
        counts[display[node]] = counts.get(display[node], 0) + 1
    return {
        node: terms[node] if counts[display[node]] > 1 else display[node]
        for node in nodes
    }


def _degrees(edges):
    nodes = set()
    inbound, outbound = defaultdict(int), defaultdict(int)
    for edge in edges:
        source, target = _ends(edge)
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
        source, target = _ends(edge)
        adjacent[source].add(target)
        adjacent[target].add(source)
    remaining = set(adjacent)
    sizes = []
    while remaining:
        start = min(remaining, key=node_sort_key)
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
    rotations = [body[i:] + body[:i] for i in range(len(body))]
    best = min(rotations, key=lambda rot: [node_sort_key(node) for node in rot])
    return best + [best[0]] if closed else best


def _node_depth_map(edges):
    """Longest-path distance of each incident node, or a cycle when the class is not a DAG.

    ``topological_order`` is ONTO's graphlib.TopologicalSorter check. The returned cycle is
    rotated to a stable start so the same edges do not change the document.
    """
    if not edges:
        return {}, None
    found = topological_order(_tagged_edges(edges))
    if found["cycle"]:
        return None, _canonical_cycle(_untag_path(found["cycle"]))
    outgoing = defaultdict(list)
    for edge in edges:
        source, target = _ends(edge)
        outgoing[source].append(target)
    order = _untag_path(found["order"])
    dist = {node: 0 for node in order}
    for node in order:
        for target in outgoing[node]:
            dist[target] = max(dist[target], dist[node] + 1)
    return dist, None


def _depth(edges):
    """Longest directed path length in edges, or null when TopologicalSorter finds a cycle."""
    if not edges:
        return {"depth": 0, "cycle": None}
    dist, cycle = _node_depth_map(edges)
    if cycle is not None:
        return {"depth": None, "cycle": cycle}
    return {"depth": max(dist.values()), "cycle": None}


def _component_size_by_node(edges):
    adjacent = defaultdict(set)
    for edge in edges:
        source, target = _ends(edge)
        adjacent[source].add(target)
        adjacent[target].add(source)
    remaining = set(adjacent)
    sizes = {}
    while remaining:
        start = min(remaining, key=node_sort_key)
        stack, seen = [start], {start}
        remaining.remove(start)
        while stack:
            node = stack.pop()
            for neighbour in adjacent[node]:
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    seen.add(neighbour)
                    stack.append(neighbour)
        size = len(seen)
        for node in seen:
            sizes[node] = size
    return sizes


def _class_metrics(edges, cycle_limit):
    incoming = defaultdict(set)
    nodes = set()
    for edge in edges:
        source, target = _ends(edge)
        nodes.update((source, target))
        incoming[target].add(source)
    depth = _depth(edges)
    components = _weakly_connected_components(edges)
    edge_count = len(edges)
    node_count = len(nodes)
    return {
        "edges": edge_count,
        "incident_nodes": node_count,
        "multiple_sources": sorted(
            (node for node in incoming if len(incoming[node]) > 1),
            key=node_sort_key,
        ),
        "sample_cycles": [
            _untag_path(cycle) for cycle in relation_cycles(_tagged_edges(edges), limit=cycle_limit)
        ],
        "degree": _degrees(edges),
        "weakly_connected_components": components,
        "independent_cycles": edge_count - node_count + components["count"],
        "depth": depth["depth"],
        "order_cycle": depth["cycle"],
    }


def summarize(graph, classes, *, cycle_limit=5):
    groups = {category: [] for category in sorted(set(classes.values()))}
    unclassified = 0
    for edge in graph["edges"]:
        relation = edge_relation(edge)
        if relation in classes:
            groups[classes[relation]].append(edge)
        else:
            unclassified += 1
    results = {category: _class_metrics(edges, cycle_limit) for category, edges in groups.items()}
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
        "sample_cycles": len(right["sample_cycles"]) - len(left["sample_cycles"]),
        "directed_cycle": (1 if right["order_cycle"] else 0) - (1 if left["order_cycle"] else 0),
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


def _empty_metrics():
    return {
        "edges": 0,
        "incident_nodes": 0,
        "multiple_sources": [],
        "sample_cycles": [],
        "degree": {"max_in": 0, "max_out": 0, "max_total": 0, "sources": 0, "sinks": 0},
        "weakly_connected_components": {"count": 0, "sizes": []},
        "independent_cycles": 0,
        "depth": 0,
        "order_cycle": None,
    }


def compare_boundaries(left, right):
    """Class metrics after a merge, split, or second table. Missing classes count as empty."""
    names = sorted(set(left["classes"]) | set(right["classes"]))
    aligned_left = {
        "classes": {name: left["classes"].get(name) or _empty_metrics() for name in names},
        "unclassified_edges": left["unclassified_edges"],
    }
    aligned_right = {
        "classes": {name: right["classes"].get(name) or _empty_metrics() for name in names},
        "unclassified_edges": right["unclassified_edges"],
    }
    return compare_graphs(aligned_left, aligned_right)


def merged_classification(classes, sources, into):
    """Map every relation in either named class onto ``into``."""
    pair = tuple(sources)
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ValueError("merge needs two distinct classes")
    known = set(classes.values())
    for name in pair:
        if name not in known:
            raise ValueError(f"unknown class: {name}")
    return {relation: into if category in pair else category for relation, category in classes.items()}


def split_classification(classes, source, assignments):
    """Move named relations of ``source`` onto the classes in ``assignments``."""
    if source not in set(classes.values()):
        raise ValueError(f"unknown class: {source}")
    if not assignments:
        raise ValueError("split needs at least one assignment")
    updated = dict(classes)
    for relation, new_name in assignments.items():
        if relation not in classes:
            raise ValueError(f"unknown relation: {relation}")
        if classes[relation] != source:
            raise ValueError(f"relation {relation!r} is not in class {source}")
        if new_name == source:
            raise ValueError("split assignment must name a different class")
        updated[relation] = new_name
    return updated


def _sigma_bands(points):
    """Cumulative bands of points lying beyond 1σ, 2σ, and 3σ.

    ``points`` are ``(identity, number)`` pairs. Fewer than three points, or a
    zero population standard deviation, is ``not applicable`` rather than a
    division by zero. A point is beyond kσ only when its absolute deviation is
    strictly greater than kσ. Equal deviations are ordered by ``node_sort_key``.
    """
    count = len(points)
    if count < 3:
        return {"status": "not applicable", "reason": "fewer than 3 data points", "count": count}
    values = [value for _, value in points]
    mean = statistics.fmean(values)
    sigma = statistics.pstdev(values)
    if sigma == 0:
        return {
            "status": "not applicable",
            "reason": "zero variance",
            "count": count,
            "mean": mean,
            "sigma": 0.0,
        }
    bands = {"1": [], "2": [], "3": []}
    for identity, value in points:
        delta = value - mean
        score = delta / sigma
        magnitude = abs(score)
        item = {"id": identity, "value": value, "z": score}
        # Compare the ratio, not k*sigma, so an exact multiple of sigma is not beyond that band
        # when k*sigma is the shorter float.
        if magnitude > 1:
            bands["1"].append(item)
        if magnitude > 2:
            bands["2"].append(item)
        if magnitude > 3:
            bands["3"].append(item)
    for items in bands.values():
        items.sort(key=lambda item: (-abs(item["z"]), node_sort_key(item["id"])))
    return {"status": "ok", "count": count, "mean": mean, "sigma": sigma, "bands": bands}


def _not_applicable(reason):
    return {"status": "not applicable", "reason": reason, "count": 0}


def _node_scope(edges):
    """Degree, weak-component size, and depth of each incident node."""
    inbound, outbound = defaultdict(int), defaultdict(int)
    nodes = set()
    for edge in edges:
        source, target = _ends(edge)
        nodes.update((source, target))
        outbound[source] += 1
        inbound[target] += 1
    ordered = sorted(nodes, key=node_sort_key)
    component_size = _component_size_by_node(edges)
    depths, _cycle = _node_depth_map(edges)
    metrics = {
        "in_degree": _sigma_bands([(node, inbound[node]) for node in ordered]),
        "out_degree": _sigma_bands([(node, outbound[node]) for node in ordered]),
        "total_degree": _sigma_bands([(node, inbound[node] + outbound[node]) for node in ordered]),
        "component_size": _sigma_bands([(node, component_size[node]) for node in ordered]),
    }
    if depths is None:
        metrics["depth"] = _not_applicable("cycle")
    else:
        metrics["depth"] = _sigma_bands([(node, depths[node]) for node in ordered])
    return {"nodes": metrics}


def _across_classes(class_metrics):
    names = sorted(class_metrics)
    def series(selector):
        return _sigma_bands([(name, selector(class_metrics[name])) for name in names])

    depth_points = [
        (name, class_metrics[name]["depth"])
        for name in names
        if class_metrics[name]["depth"] is not None
    ]
    return {
        "edges": series(lambda row: row["edges"]),
        "component_count": series(lambda row: row["weakly_connected_components"]["count"]),
        "max_component_size": series(
            lambda row: max(row["weakly_connected_components"]["sizes"], default=0)
        ),
        "max_total_degree": series(lambda row: row["degree"]["max_total"]),
        "independent_cycles": series(lambda row: row["independent_cycles"]),
        "depth": _sigma_bands(depth_points),
    }


def anomaly_report(graph, classes, *, cycle_limit=5):
    """Sigma bands for nodes in each class and relation, and for the classes themselves."""
    by_class = {category: [] for category in sorted(set(classes.values()))}
    by_relation = {relation: [] for relation in sorted(classes)}
    for edge in graph["edges"]:
        relation = edge_relation(edge)
        if relation in classes:
            by_class[classes[relation]].append(edge)
            by_relation[relation].append(edge)
    class_metrics = {
        name: _class_metrics(edges, cycle_limit) for name, edges in by_class.items()
    }
    return {
        "classes": {name: _node_scope(edges) for name, edges in by_class.items()},
        "relations": {name: _node_scope(edges) for name, edges in by_relation.items()},
        "across_classes": _across_classes(class_metrics),
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
    present = sorted(
        {node for edge in edges for node in _ends(edge)},
        key=node_sort_key,
    )
    terms = _mermaid_terms(present)
    names = _mermaid_names(present, labels, terms)
    ordered = sorted(edges, key=lambda edge: (
        node_sort_key(edge["source"]), edge_relation(edge),
        node_sort_key(edge["target"]), edge.get("basis") or ""))
    diagram = graph_mermaid({
        "nodes": [
            {"id": terms[node], "name": names[node]}
            for node in present
        ],
        "edges": [
            {**edge, "source": terms[edge["source"]], "target": terms[edge["target"]]}
            for edge in ordered
        ],
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
    if "mapping" in record:
        lines.append(
            f"{label}mapping: {json.dumps(record['mapping'], ensure_ascii=False, sort_keys=True)}"
        )
    return lines


def _class_lines(title, row):
    lines = [f"{title}:"]
    lines.append(f"  edges: {row['edges']}")
    lines.append(f"  incident_nodes: {row['incident_nodes']}")
    sources = ", ".join(node_text(node) for node in row["multiple_sources"]) if row["multiple_sources"] else "(none)"
    lines.append(f"  multiple_sources: {sources}")
    if row["sample_cycles"]:
        cycles = "; ".join(" → ".join(node_text(node) for node in cycle) for cycle in row["sample_cycles"])
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
        cycle = " → ".join(node_text(node) for node in row["order_cycle"]) if row["order_cycle"] else "cycle"
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
    lines.append(_change(
        "sample_cycles", len(left["sample_cycles"]), len(right["sample_cycles"]), delta["sample_cycles"]))
    lines.append(_change(
        "directed_cycle",
        1 if left["order_cycle"] else 0,
        1 if right["order_cycle"] else 0,
        delta["directed_cycle"],
    ))
    left_depth = "undefined" if left["depth"] is None else left["depth"]
    right_depth = "undefined" if right["depth"] is None else right["depth"]
    lines.append(_change("depth", left_depth, right_depth, delta["depth"]))
    return lines


def _band_lines(title, report):
    lines = [f"  {title}: {report['status']}"]
    if report["status"] != "ok":
        lines.append(f"    reason: {report['reason']}")
        return lines
    lines.append(f"    count: {report['count']} mean: {report['mean']} sigma: {report['sigma']}")
    for level in ("1", "2", "3"):
        items = report["bands"][level]
        if items:
            rendered = ", ".join(
                f"{node_text(item['id'])}={item['value']} z={item['z']}" for item in items
            )
        else:
            rendered = "(none)"
        lines.append(f"    beyond {level}σ: {rendered}")
    return lines


def _anomaly_lines(anomalies):
    lines = ["anomalies:"]
    for scope in ("classes", "relations"):
        lines.append(f"  {scope}:")
        for name in sorted(anomalies[scope]):
            lines.append(f"    {name}:")
            for metric, report in anomalies[scope][name]["nodes"].items():
                lines.extend(_band_lines(metric, report))
    lines.append("  across_classes:")
    for metric in sorted(anomalies["across_classes"]):
        lines.extend(_band_lines(metric, anomalies["across_classes"][metric]))
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
    if "anomalies" in body:
        lines.extend(_anomaly_lines(body["anomalies"]))
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


def _env_text(name):
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def _resolve_input(value, env_name, what):
    if value is not None:
        return Path(value)
    raw = _env_text(env_name)
    if raw:
        return Path(raw)
    raise ValueError(f"pass the {what} or set {env_name}")


def _resolve_profile(value):
    if value is not None:
        return value
    raw = _env_text("TOPONTOLOGY_PROFILE")
    if raw:
        return raw
    raise ValueError("pass the profile or set TOPONTOLOGY_PROFILE")


def _require_class(report, name, flag):
    if name not in report["classes"]:
        known = ", ".join(sorted(report["classes"])) or "(none)"
        raise ValueError(f"unknown class for {flag}: {name} (known: {known})")


def _parse_assignment(text):
    relation, separator, new_name = text.partition("=")
    if not separator or not relation or not new_name:
        raise ValueError("--assign must be RELATION=CLASS")
    return relation, new_name


def _resolve_boundary(value):
    if value is None:
        return None
    if value == "":
        raw = _env_text("TOPONTOLOGY_BOUNDARY")
        if not raw:
            raise ValueError("pass a classification table to --boundary or set TOPONTOLOGY_BOUNDARY")
        return Path(raw)
    return Path(value)


def _resolve_format(args):
    if args.json and args.format not in (None, "json", "canonical"):
        raise ValueError("--json cannot be combined with a different --format")
    return args.format or "json"


def main(argv=None):
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description=__doc__)
    parser.add_argument("graph", nargs="?", type=Path)
    parser.add_argument("classification", nargs="?", type=Path)
    parser.add_argument("profile", nargs="?")
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
    parser.add_argument("--anomalies", action="store_true",
                        help="add 1σ, 2σ, and 3σ bands for degree, component size, and depth")
    parser.add_argument("--boundary", nargs="?", const="", default=None, metavar="TABLE",
                        help="compare with a second classification table; omit the path to use TOPONTOLOGY_BOUNDARY")
    parser.add_argument("--merge", action="append", default=[], metavar="CLASS",
                        help="two classes to merge; use with --into")
    parser.add_argument("--into", metavar="CLASS", help="class name produced by --merge")
    parser.add_argument("--split", metavar="CLASS", help="class to split with --assign")
    parser.add_argument("--assign", action="append", default=[], metavar="RELATION=CLASS",
                        help="move one relation of --split onto another class")
    parser.add_argument("-o", "--output", type=Path, help="write the document to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        fmt = _resolve_format(args)
        args.graph = _resolve_input(args.graph, "TOPONTOLOGY_GRAPH", "graph path")
        args.classification = _resolve_input(
            args.classification, "TOPONTOLOGY_CLASSIFICATION", "classification path")
        args.profile = _resolve_profile(args.profile)
        boundary = _resolve_boundary(args.boundary)
        if args.cycle_limit < 0:
            raise ValueError("--cycle-limit must be >= 0")
        if args.merge and args.split:
            raise ValueError("use either --merge or --split")
        if args.into and not args.merge:
            raise ValueError("--into requires --merge")
        if args.assign and not args.split:
            raise ValueError("--assign requires --split")
        if args.merge and len(args.merge) != 2:
            raise ValueError("merge needs two distinct classes")
        if args.merge and not args.into:
            raise ValueError("--merge requires --into")
        structural = bool(args.compare or args.compare_class or boundary or args.merge or args.split)
        if args.anomalies and structural:
            raise ValueError("--anomalies reads one classification")
        if sum(bool(item) for item in (args.compare, args.compare_class, boundary, args.merge, args.split)) > 1:
            raise ValueError("use only one of --compare, --compare-class, --boundary, --merge, or --split")
        if fmt == "mermaid" and structural:
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
        elif boundary or args.merge or args.split:
            if boundary:
                other_classes = _load_classes(boundary, args.profile)
                right_provenance = input_provenance(args.graph, boundary, args.profile)
            elif args.merge:
                other_classes = merged_classification(classes, args.merge, args.into)
                right_provenance = input_provenance(args.graph, args.classification, args.profile)
                right_provenance["mapping"] = {
                    "operation": "merge",
                    "classes": list(args.merge),
                    "into": args.into,
                }
            else:
                other_classes = split_classification(
                    classes, args.split, dict(_parse_assignment(item) for item in args.assign))
                right_provenance = input_provenance(args.graph, args.classification, args.profile)
                right_provenance["mapping"] = {
                    "operation": "split",
                    "class": args.split,
                    "assign": dict(_parse_assignment(item) for item in args.assign),
                }
            other_report = summarize(graph, other_classes, cycle_limit=args.cycle_limit)
            body = compare_boundaries(report, other_report)
            both = {"left": provenance, "right": right_provenance}
            text = dump_json(document(body, both)) if fmt in ("json", "canonical") else render_text(document(body, both))
        else:
            scoped = classes
            if args.class_name:
                _require_class(report, args.class_name, "--class")
                report = {
                    "classes": {args.class_name: report["classes"][args.class_name]},
                    "unclassified_edges": report["unclassified_edges"],
                }
                scoped = {
                    relation: category for relation, category in classes.items()
                    if category == args.class_name
                }
            if args.anomalies:
                report = {
                    **report,
                    "anomalies": anomaly_report(graph, scoped, cycle_limit=args.cycle_limit),
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
