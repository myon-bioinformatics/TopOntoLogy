"""Measure the directed edges belonging to each supplied relation class."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import statistics
import sys
from collections import defaultdict, deque
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from knowledgegraph import graph_mermaid, relation_cycles, topological_order

__version__ = "0.6.0"
TOOL_NAME = "topontology"

_GRAPH_FORMATS = ("nodes-edges", "vendor-lock", "aoi-siblings", "py-imports")
DECLARED_RELATION = "declares"
IMPORT_RELATION = "imports"
UNMAPPED_RELATION = "unmapped"
IMPORT_MAP_SCHEMA = "import-map/1"
PY_IMPORTS_SCHEMA = "py-imports/1"
_DEFAULT_ROOT_REPO = "aoi"
_SKIP_DIRS = frozenset({".git", ".venv", ".test-deps", "__pycache__", "build"})

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


VENDOR_LOCK_SCHEMA = "vendor-lock/1"
_VENDOR_RELATION = "vendorsFrom"

# Profiles are audit, concepts, and cases. The other top-level objects are
# metadata. `properties` is reserved on /1 as well, so a later object under
# that key cannot be read as a relation-to-class map.
_RELATION_SCHEMA_PREFIX = "taxontoy-relations/"
_KNOWN_RELATION_SCHEMAS = {
    "taxontoy-relations/1": frozenset({"schema", "direction", "excluded", "properties"}),
    "taxontoy-relations/2": frozenset({"schema", "direction", "excluded", "properties"}),
}


def _optional_lock_string(entry, key, index):
    """Return a stripped string, or empty when the key is absent.

    A present value that is not a string is an error, even if the other
    of ``repository`` or ``source`` is already a usable string.
    """
    if key not in entry:
        return ""
    value = entry[key]
    if not isinstance(value, str):
        raise ValueError(f"vendor-lock files[{index}] {key} must be a string")
    return value.strip()


def graph_from_vendor_lock(document):
    """Read a vendor-lock/1 document as a directed graph.

    Fields read: ``schema``, ``files``, and on each file ``destination`` or
    ``path``, plus ``repository`` and ``source``. ``commit``, ``ref``,
    ``sha256``, and ``blob_sha`` are ignored. Each file is a node identified by
    ``destination`` when that is a non-empty string, otherwise by ``path``.
    The edge runs from that local node to the upstream it vendors from, with
    relation ``vendorsFrom``. The upstream id is ``repository:source`` when
    both are non-empty strings, ``repository`` when only that is set, and
    ``source`` when there is no repository. Any other schema is rejected.
    """
    if not isinstance(document, dict):
        raise ValueError("vendor-lock JSON must be an object")
    schema = document.get("schema")
    if schema != VENDOR_LOCK_SCHEMA:
        raise ValueError(f"unknown vendor-lock schema: {schema!r}")
    files = document.get("files")
    if not isinstance(files, list):
        raise ValueError("vendor-lock/1 requires a files array")
    nodes = {}
    edges = []

    def add_node(ident):
        nodes.setdefault(ident, {"id": ident})

    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ValueError(f"vendor-lock files[{index}] must be an object")
        local = entry.get("destination")
        if not isinstance(local, str) or not local.strip():
            local = entry.get("path")
        if not isinstance(local, str) or not local.strip():
            raise ValueError(f"vendor-lock files[{index}] needs destination or path")
        local = local.strip()
        repository = _optional_lock_string(entry, "repository", index)
        source = _optional_lock_string(entry, "source", index)
        if repository and source:
            upstream = f"{repository}:{source}"
        elif repository:
            upstream = repository
        elif source:
            upstream = source
        else:
            raise ValueError(f"vendor-lock files[{index}] needs repository or source")
        add_node(local)
        add_node(upstream)
        edges.append({"source": local, "relation": _VENDOR_RELATION, "target": upstream})
    edges.sort(key=lambda edge: (edge["source"], edge["target"]))
    return {"nodes": [nodes[key] for key in sorted(nodes)], "edges": edges}


def _edge_record(edge):
    source, target = _ends(edge)
    return {"source": source, "relation": edge_relation(edge), "target": target}


def _edge_sort_key(record):
    return (node_sort_key(record["source"]), str(record["relation"]), node_sort_key(record["target"]))


def _edge_counts(edges):
    counts = {}
    for edge in edges:
        record = _edge_record(edge)
        key = (record["source"], record["relation"], record["target"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def attribute_edges(left_edges, right_edges):
    """Multiset difference of edges as sorted added and removed triples."""
    left = _edge_counts(left_edges)
    right = _edge_counts(right_edges)
    added, removed = [], []
    for key in set(left) | set(right):
        record = {"source": key[0], "relation": key[1], "target": key[2]}
        delta = right.get(key, 0) - left.get(key, 0)
        if delta > 0:
            added.extend([dict(record) for _ in range(delta)])
        elif delta < 0:
            removed.extend([dict(record) for _ in range(-delta)])
    added.sort(key=_edge_sort_key)
    removed.sort(key=_edge_sort_key)
    return {"added": added, "removed": removed}


def _edges_in_class(graph, classes, category):
    chosen = []
    for edge in graph["edges"]:
        relation = edge_relation(edge)
        if relation in classes and classes[relation] == category:
            chosen.append(edge)
    return chosen


def _snapshot_nodes(graph):
    """Nodes incident to at least one edge. Isolated ``nodes`` entries are not seen."""
    found = set()
    for edge in graph["edges"]:
        source, target = _ends(edge)
        found.add(source)
        found.add(target)
    return found


def _snapshot_classes(graph, classes):
    """Relation classes of the edges that touch each incident node."""
    found = defaultdict(set)
    for edge in graph["edges"]:
        relation = edge_relation(edge)
        if relation not in classes:
            continue
        category = classes[relation]
        source, target = _ends(edge)
        found[source].add(category)
        found[target].add(category)
    return found


def node_churn(snapshots, classes):
    """Presence of each node across an ordered series.

    A node is present at a snapshot when it is an endpoint of an edge. The
    series is treated as starting from absence, so the first appearance is an
    enter. ``churn`` is the number of enter and leave transitions. ``disappeared``
    is the snapshot after the last presence when the node is absent at the end,
    and null when it is still present. ``repeated_churn`` keeps nodes whose
    churn is greater than 1 (they did not merely appear and stay), sorted by
    churn descending and then ``node_sort_key``.
    """
    labels = [item["label"] for item in snapshots]
    present = [_snapshot_nodes(item["graph"]) for item in snapshots]
    class_sets = [_snapshot_classes(item["graph"], classes) for item in snapshots]
    nodes = set()
    for group in present:
        nodes.update(group)
    rows = []
    for node in sorted(nodes, key=node_sort_key):
        seen = [node in group for group in present]
        first = next(index for index, flag in enumerate(seen) if flag)
        last = len(seen) - 1 - next(index for index, flag in enumerate(reversed(seen)) if flag)
        enters = 0
        leaves = 0
        previous = False
        for flag in seen:
            if flag and not previous:
                enters += 1
            elif previous and not flag:
                leaves += 1
            previous = flag
        categories = set()
        for mapping in class_sets:
            categories.update(mapping.get(node, ()))
        disappeared = None if seen[-1] else labels[last + 1]
        rows.append({
            "id": node,
            "first_seen": labels[first],
            "last_seen": labels[last],
            "disappeared": disappeared,
            "enters": enters,
            "leaves": leaves,
            "churn": enters + leaves,
            "classes": sorted(categories),
        })
    repeated = [row for row in rows if row["churn"] > 1]
    repeated.sort(key=lambda row: (-row["churn"], node_sort_key(row["id"])))
    return {"nodes": rows, "repeated_churn": repeated}


def series_report(snapshots, classes, *, cycle_limit=5):
    """Compare each consecutive snapshot pair and record node churn.

    ``snapshots`` is an ordered list of ``{"label", "path", "graph"}``. The
    graph-level block is E−V+C, weak components, and the other class metrics
    on every edge. Class blocks reuse ``compare_graphs``. Added and removed
    edges are the multiset difference that accounts for each numeric edge delta.
    ``nodes`` and ``repeated_churn`` come from ``node_churn``.
    """
    if len(snapshots) < 2:
        raise ValueError("series needs at least 2 snapshots")
    seen = set()
    for snapshot in snapshots:
        label = snapshot["label"]
        if label in seen:
            raise ValueError(f"duplicate snapshot label: {label}")
        seen.add(label)
    pairs = []
    for left, right in zip(snapshots, snapshots[1:]):
        left_report = summarize(left["graph"], classes, cycle_limit=cycle_limit)
        right_report = summarize(right["graph"], classes, cycle_limit=cycle_limit)
        compared = compare_graphs(left_report, right_report)
        classes_out = {}
        for name in sorted(compared["classes"]):
            row = dict(compared["classes"][name])
            row["edges_changed"] = attribute_edges(
                _edges_in_class(left["graph"], classes, name),
                _edges_in_class(right["graph"], classes, name),
            )
            classes_out[name] = row
        graph_left = _class_metrics(left["graph"]["edges"], cycle_limit)
        graph_right = _class_metrics(right["graph"]["edges"], cycle_limit)
        pairs.append({
            "left": left["label"],
            "right": right["label"],
            "graph": {
                "left": graph_left,
                "right": graph_right,
                "delta": delta_metrics(graph_left, graph_right),
                "edges_changed": attribute_edges(left["graph"]["edges"], right["graph"]["edges"]),
            },
            "classes": classes_out,
            "unclassified_edges": compared["unclassified_edges"],
        })
    churn = node_churn(snapshots, classes)
    return {"pairs": pairs, "nodes": churn["nodes"], "repeated_churn": churn["repeated_churn"]}


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


def _string_tuple(node):
    if not isinstance(node, (ast.Tuple, ast.List)):
        raise ValueError("SIBLINGS values must be tuples of strings")
    values = []
    for elt in node.elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            raise ValueError("SIBLINGS values must be tuples of strings")
        values.append(elt.value)
    return tuple(values)


def _dict_of_string_tuples(node):
    if not isinstance(node, ast.Dict):
        raise ValueError("SIBLINGS must be a dict")
    parsed = {}
    for key, item in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError("SIBLINGS key must be a string")
        parsed[key.value] = _string_tuple(item)
    return parsed


_MUTATING_ATTRS = frozenset({
    "update", "append", "extend", "insert", "pop", "remove", "clear",
    "setdefault", "add", "discard", "reverse", "sort",
})


def _names_siblings(node):
    """True when ``node`` is ``SIBLINGS`` or a subscript or attribute of it."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "SIBLINGS"


def _call_mutates_siblings(node):
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _MUTATING_ATTRS:
        return False
    return _names_siblings(func.value)


def _statement_mutates_siblings(stmt):
    """True when ``stmt`` writes ``SIBLINGS`` in a form this reader does not apply."""
    for node in ast.walk(stmt):
        if isinstance(node, ast.AugAssign) and _names_siblings(node.target):
            return True
        if isinstance(node, ast.Delete) and any(_names_siblings(target) for target in node.targets):
            return True
        if isinstance(node, ast.Call) and _call_mutates_siblings(node):
            return True
        if isinstance(node, ast.Assign) and any(_names_siblings(target) for target in node.targets):
            return True
        if isinstance(node, ast.AnnAssign) and _names_siblings(node.target):
            return True
        if isinstance(node, ast.NamedExpr) and _names_siblings(node.target):
            return True
    return False


def siblings_from_source(text):
    """Read the top-level ``SIBLINGS`` dict from a Python file.

    A later top-level ``SIBLINGS["name"] = (...)`` replaces or adds that one
    entry. Any other write — augmented assignment, a mutating method, ``del``,
    or an assignment nested in ``if`` or another block — is an error. Reads are
    ignored. The tool does not execute the file.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise ValueError(f"could not parse SIBLINGS: {error.msg}") from error
    table = None
    for stmt in tree.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "SIBLINGS":
            if stmt.value is None:
                raise ValueError("SIBLINGS must be a dict")
            table = _dict_of_string_tuples(stmt.value)
            continue
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and target.id == "SIBLINGS":
                table = _dict_of_string_tuples(stmt.value)
                continue
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "SIBLINGS"
            ):
                key = target.slice
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    raise ValueError("SIBLINGS key must be a string")
                if table is None:
                    raise ValueError("SIBLINGS is missing")
                table[key.value] = _string_tuple(stmt.value)
                continue
        if _statement_mutates_siblings(stmt):
            raise ValueError("unsupported SIBLINGS mutation")
    if table is None:
        raise ValueError("SIBLINGS is missing")
    return table


def sibling_repo(path, root):
    """Repo that owns a ``SIBLINGS`` path. ``.`` is the launcher root."""
    if path == ".":
        return root
    return path.split("/", 1)[0]


def _dep_edge_sort(edge):
    return (
        str(edge["source"]),
        str(edge["relation"]),
        str(edge["target"]),
        str(edge.get("basis") or ""),
        str(edge.get("file") or ""),
        edge.get("line") or 0,
    )


def _graph_from_edges(edges):
    nodes = set()
    for edge in edges:
        nodes.add(edge["source"])
        nodes.add(edge["target"])
    ordered = sorted(edges, key=_dep_edge_sort)
    return {"nodes": [{"id": node} for node in sorted(nodes)], "edges": ordered}


def graph_from_siblings(table, *, root=_DEFAULT_ROOT_REPO):
    """One ``declares`` edge per ``SIBLINGS`` path.

    The target is the path string (``queryu/core``, ``.``). ``basis`` is the
    sibling repo: the first path component, or ``root`` when the path is ``.``.
    """
    if not isinstance(root, str) or not root.strip():
        raise ValueError("root name must be a non-empty string")
    root = root.strip()
    edges = []
    for child, paths in table.items():
        if not isinstance(child, str) or not child:
            raise ValueError("SIBLINGS key must be a non-empty string")
        for path in paths:
            if path != "." and (not path or path != path.strip() or path.startswith("/") or path.endswith("/")):
                raise ValueError(f"invalid sibling path: {path!r}")
            edges.append({
                "source": child,
                "relation": DECLARED_RELATION,
                "target": path,
                "basis": sibling_repo(path, root),
            })
    return _graph_from_edges(edges)


def declared_module_paths(table):
    """Module names contributed by a path that is itself the import name.

    ``pythdragoras`` placed on the import path is ``import pythdragoras``. A path with a
    slash (``pythdragoras/core``, ``.``) does not name the modules inside it.
    """
    found = {}
    for paths in table.values():
        for path in paths:
            if "/" not in path and path != ".":
                found.setdefault(path, path)
    return found


def load_import_map(document):
    """``import-map/1``: module name (or dotted prefix) to a ``SIBLINGS`` path.

    Longest prefix wins, and this map wins over ``declared_module_paths``.
    ``root`` names the repo for the path ``.`` (default ``aoi``).
    """
    if not isinstance(document, dict):
        raise ValueError("import map must be an object")
    schema = document.get("schema")
    if schema != IMPORT_MAP_SCHEMA:
        raise ValueError(f"unknown import-map schema: {schema!r}")
    root = document.get("root", _DEFAULT_ROOT_REPO)
    if not isinstance(root, str) or not root.strip():
        raise ValueError("import map root must be a non-empty string")
    modules = document.get("modules", {})
    if not isinstance(modules, dict):
        raise ValueError("import map modules must be an object")
    parsed = {}
    for key, value in modules.items():
        if not isinstance(key, str) or not key or key.startswith("."):
            raise ValueError(f"import map module must be a non-empty module name: {key!r}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"import map path for {key} must be a non-empty string")
        parsed[key] = value.strip()
    return {"root": root.strip(), "modules": parsed}


def _mapped_path(module, explicit, declared_modules):
    """Return the ``SIBLINGS`` path for ``module``, or None when nothing maps it.

    A relative import (``module`` starts with ``.``) is never mapped. An
    explicit prefix matches before a single-component path from the declared
    table. Unmapped names are kept by the caller.
    """
    if module.startswith("."):
        return None
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        key = ".".join(parts[:end])
        if key in explicit:
            return explicit[key]
    return declared_modules.get(parts[0])


def _import_names(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            if node.module:
                yield node.lineno, prefix + node.module
            else:
                for alias in node.names:
                    yield node.lineno, prefix + alias.name


def python_sources(directory):
    """``.py`` files under ``directory``, skipping tool and cache directories.

    A symlink whose target resolves outside ``directory`` is an error. The
    file is not read.
    """
    if not directory.is_dir():
        raise ValueError(f"source is not a directory: {directory}")
    root = directory.resolve()
    found = []
    for path in directory.rglob("*.py"):
        rel = path.relative_to(directory)
        if _SKIP_DIRS.intersection(rel.parts[:-1]):
            continue
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise ValueError(f"symlink escapes the source directory: {rel.as_posix()}")
        found.append((rel.as_posix(), path))
    found.sort()
    return found


def scan_imports(sources, table, import_map):
    """Import edges for each ``{"repo", "path"}`` source directory.

    Mapped imports use relation ``imports`` and the ``SIBLINGS`` path as the
    target. Every other import, including relative imports, is an ``unmapped``
    edge. Nothing is dropped.
    """
    explicit = import_map["modules"]
    declared_modules = declared_module_paths(table)
    edges = []
    files = []
    for source in sources:
        repo = source["repo"]
        for rel, path in python_sources(source["path"]):
            files.append({
                "repo": repo,
                "path": rel,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError as error:
                raise ValueError(f"{repo}:{rel}: {error.msg}") from error
            for line, module in _import_names(tree):
                mapped = _mapped_path(module, explicit, declared_modules)
                if mapped is None:
                    edges.append({
                        "source": repo,
                        "relation": UNMAPPED_RELATION,
                        "target": module,
                        "basis": rel,
                        "line": line,
                    })
                else:
                    edges.append({
                        "source": repo,
                        "relation": IMPORT_RELATION,
                        "target": mapped,
                        "basis": module,
                        "file": rel,
                        "line": line,
                    })
    files.sort(key=lambda item: (item["repo"], item["path"]))
    graph = _graph_from_edges(edges)
    graph["files"] = files
    return graph


def _manifest_path(base, value, what):
    """Resolve one manifest path and keep it inside the manifest directory.

    Absolute paths are allowed when ``resolve`` still lands inside ``base``.
    ``..`` segments and symlinks that leave ``base`` are rejected.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"py-imports/1 {what} must be a non-empty string")
    raw = Path(value.strip())
    path = raw if raw.is_absolute() else base / raw
    root = base.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"py-imports/1 {what} escapes the manifest directory")
    return path


def graph_from_py_imports_document(document, base):
    """Read a ``py-imports/1`` manifest and scan the local directories it names.

    Paths are relative to the manifest's directory unless absolute. The tool
    does not fetch or clone them.
    """
    if not isinstance(document, dict) or document.get("schema") != PY_IMPORTS_SCHEMA:
        schema = document.get("schema") if isinstance(document, dict) else None
        raise ValueError(f"unknown py-imports schema: {schema!r}")
    declared_path = _manifest_path(base, document.get("declared"), "declared")
    table = siblings_from_source(declared_path.read_text(encoding="utf-8"))
    import_map = {"root": _DEFAULT_ROOT_REPO, "modules": {}}
    if "import_map" in document:
        map_path = _manifest_path(base, document.get("import_map"), "import_map")
        import_map = load_import_map(json.loads(map_path.read_text(encoding="utf-8")))
    entries = document.get("sources")
    if not isinstance(entries, list) or not entries:
        raise ValueError("py-imports/1 requires a sources array")
    sources = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"py-imports sources[{index}] must be an object")
        repo = entry.get("repo")
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError(f"py-imports sources[{index}] repo must be a non-empty string")
        repo = repo.strip()
        if repo in seen:
            raise ValueError(f"duplicate source repo: {repo}")
        seen.add(repo)
        sources.append({
            "repo": repo,
            "path": _manifest_path(base, entry.get("path"), f"sources[{index}] path"),
        })
    sources.sort(key=lambda item: item["repo"])
    return scan_imports(sources, table, import_map)


def _edges_by_target(edges, relation):
    grouped = defaultdict(lambda: defaultdict(list))
    for edge in edges:
        if edge_relation(edge) != relation:
            continue
        source, target = _ends(edge)
        grouped[source][target].append(edge)
    return grouped


def _used_record(repo, target, edges, root):
    return {
        "source": repo,
        "target": target,
        "repo": sibling_repo(target, root),
        "modules": sorted({edge["basis"] for edge in edges}),
        "files": sorted({edge["file"] for edge in edges}),
    }


def _unused_record(repo, target, root):
    return {"source": repo, "target": target, "repo": sibling_repo(target, root)}


def _unmapped_rows(by_target):
    rows = []
    for module, edges in by_target.items():
        for edge in edges:
            rows.append({"module": module, "path": edge["basis"], "line": edge["line"]})
    rows.sort(key=lambda row: (row["path"], row["line"], row["module"]))
    return rows


def compare_declared_imports(declared_edges, actual_edges, scanned, *, root=_DEFAULT_ROOT_REPO):
    """Per scanned repo: matching, declared-but-unused, used-but-undeclared, unmapped.

    The comparable edge is ``(source, SIBLINGS path)``. ``declares`` comes from
    the declared graph and ``imports`` from the actual graph. ``unmapped``
    edges stay in their own list. Repos that ``SIBLINGS`` names but that were
    not scanned are ``not_scanned`` rather than unused. ONTO's knowledgegraph
    helpers do not provide this set difference.
    """
    declared = _edges_by_target(declared_edges, DECLARED_RELATION)
    imported = _edges_by_target(actual_edges, IMPORT_RELATION)
    unmapped = _edges_by_target(actual_edges, UNMAPPED_RELATION)
    scanned_list = sorted(scanned)
    repos = {}
    for repo in scanned_list:
        declared_targets = set(declared.get(repo, {}))
        imported_targets = set(imported.get(repo, {}))
        matching = sorted(declared_targets & imported_targets)
        unused = sorted(declared_targets - imported_targets)
        undeclared = sorted(imported_targets - declared_targets)
        repos[repo] = {
            "matching": [_used_record(repo, target, imported[repo][target], root) for target in matching],
            "declared_but_unused": [_unused_record(repo, target, root) for target in unused],
            "used_but_undeclared": [
                _used_record(repo, target, imported[repo][target], root) for target in undeclared
            ],
            "unmapped": _unmapped_rows(unmapped.get(repo, {})),
        }
    return {
        "scanned": scanned_list,
        "not_scanned": sorted(set(declared) - set(scanned_list)),
        "repos": repos,
    }


def dependency_report(declared_path, sources, import_map_path=None):
    """Compare one ``SIBLINGS`` file with imports under the given source roots."""
    table = siblings_from_source(declared_path.read_text(encoding="utf-8"))
    if import_map_path is None:
        import_map = {"root": _DEFAULT_ROOT_REPO, "modules": {}}
    else:
        import_map = load_import_map(json.loads(import_map_path.read_text(encoding="utf-8")))
    declared_graph = graph_from_siblings(table, root=import_map["root"])
    scanned = scan_imports(sources, table, import_map)
    body = compare_declared_imports(
        declared_graph["edges"],
        scanned["edges"],
        [item["repo"] for item in sources],
        root=import_map["root"],
    )
    return body, scanned["files"]


def _load_graph(path, graph_format="nodes-edges"):
    if graph_format == "aoi-siblings":
        return graph_from_siblings(siblings_from_source(path.read_text(encoding="utf-8")))
    document = json.loads(path.read_text(encoding="utf-8"))
    if graph_format == "vendor-lock":
        return graph_from_vendor_lock(document)
    if graph_format == "py-imports":
        return graph_from_py_imports_document(document, path.parent)
    if graph_format != "nodes-edges":
        raise ValueError(f"unknown graph format: {graph_format}")
    if not isinstance(document, dict) or not isinstance(document.get("edges"), list):
        raise ValueError("graph JSON must be an object with an edges array")
    return document


def _load_classes(path, profile):
    """Return the relation-to-class map named ``profile``.

    A table without ``schema`` is a map of profile name to that map. ``/1`` and
    ``/2`` keep ``audit``, ``concepts``, and ``cases`` in that shape, and put
    ``direction``, ``excluded``, and ``properties`` beside them. Those metadata
    keys are not profiles, even when ``properties`` is itself a map of strings.
    Any other ``taxontoy-relations/N`` schema is rejected.
    """
    table = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(table, dict):
        raise ValueError(f"unknown profile: {profile}")
    schema = table.get("schema")
    if schema in _KNOWN_RELATION_SCHEMAS:
        reserved = _KNOWN_RELATION_SCHEMAS[schema]
    elif isinstance(schema, str) and schema.startswith(_RELATION_SCHEMA_PREFIX):
        raise ValueError(f"unknown relation schema: {schema}")
    else:
        reserved = ()
    found = table.get(profile)
    if profile in reserved or not isinstance(found, dict):
        raise ValueError(f"unknown profile: {profile}")
    return found


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


def _parse_snapshot_token(token):
    text = token.strip()
    if not text:
        return None
    label, separator, path = text.partition("=")
    label, path = label.strip(), path.strip()
    if not separator or not label or not path:
        raise ValueError("snapshot must be LABEL=PATH")
    return label, Path(path)


def _snapshots_from_env(text):
    snapshots = []
    for piece in text.replace(",", "\n").splitlines():
        item = _parse_snapshot_token(piece)
        if item is not None:
            snapshots.append(item)
    return snapshots


def _parse_lock_token(token):
    text = token.strip()
    if not text:
        return None
    repository, separator, path = text.partition("=")
    repository, path = repository.strip(), path.strip()
    if not separator or not repository or not path:
        raise ValueError("lock must be REPOSITORY=PATH")
    return repository, Path(path)


def _locks_from_env(text):
    locks = []
    for piece in text.replace(",", "\n").splitlines():
        item = _parse_lock_token(piece)
        if item is not None:
            locks.append(item)
    return locks


def _resolve_locks(raw_args):
    if raw_args:
        parsed = []
        for token in raw_args:
            item = _parse_lock_token(token)
            if item is not None:
                parsed.append(item)
        if not parsed:
            raise ValueError("lock must be REPOSITORY=PATH")
        return parsed
    env = _env_text("TOPONTOLOGY_LOCKS")
    if not env:
        return []
    return _locks_from_env(env)


def _resolve_snapshots(raw_args):
    if raw_args:
        parsed = []
        for token in raw_args:
            item = _parse_snapshot_token(token)
            if item is not None:
                parsed.append(item)
        if not parsed:
            raise ValueError("snapshot must be LABEL=PATH")
        return parsed
    env = _env_text("TOPONTOLOGY_SNAPSHOTS")
    if not env:
        return []
    return _snapshots_from_env(env)


def _vendor_copies(locks):
    """Flatten vendor-lock documents into copy records.

    Each lock is ``{"repository", "document"}``. The copy id is
    ``owner:destination``, which is the id another lock uses when it vendors
    that file (``repository:source`` from ``graph_from_vendor_lock``).
    """
    if not locks:
        raise ValueError("impact needs at least 1 lock")
    seen = set()
    copies = []
    for lock in locks:
        repository = lock["repository"]
        if repository in seen:
            raise ValueError(f"duplicate lock repository: {repository}")
        seen.add(repository)
        for edge in graph_from_vendor_lock(lock["document"])["edges"]:
            copies.append({
                "repository": repository,
                "path": edge["source"],
                "upstream": edge["target"],
                "id": f"{repository}:{edge['source']}",
            })
    return copies


def _vendor_forward(copies):
    """Map each node to the upstream nodes it vendors from."""
    forward = defaultdict(set)
    for copy in copies:
        forward.setdefault(copy["upstream"], set())
        forward[copy["id"]].add(copy["upstream"])
    return forward


def _graph_cycle(forward):
    """The cycle ``graphlib`` reports, or None when the vendorsFrom graph is a DAG."""
    sorter = TopologicalSorter({
        node: sorted(preds, key=node_sort_key) for node, preds in forward.items()
    })
    try:
        sorter.prepare()
    except CycleError as error:
        return list(error.args[1])
    return None


def _walk_roots(forward):
    """Roots reachable from each node, and nodes that can reach a cycle.

    A gray node is on the stack, so the edge back to it is a cycle. The roots
    of a cyclic node omit whatever sits behind that back edge.
    """
    color = {}
    memo = {}
    cyclic = set()

    def walk(node):
        if color.get(node) == 2:
            return memo[node]
        if color.get(node) == 1:
            cyclic.add(node)
            return frozenset(), True
        color[node] = 1
        ups = forward[node]
        if not ups:
            found, saw = frozenset([node]), False
        else:
            acc, saw = set(), False
            for item in ups:
                item_roots, child_saw = walk(item)
                acc |= item_roots
                if child_saw:
                    saw = True
            found = frozenset(acc)
        if saw:
            cyclic.add(node)
        color[node] = 2
        memo[node] = (found, saw)
        return memo[node]

    for node in forward:
        walk(node)
    return {node: memo[node][0] for node in memo}, cyclic


def _violations(copies, roots, cyclic):
    seen = set()
    violations = []
    for copy in copies:
        ident = copy["id"]
        if ident in seen:
            continue
        seen.add(ident)
        found = roots[ident]
        on_cycle = ident in cyclic
        if on_cycle or len(found) != 1:
            violations.append({
                "repository": copy["repository"],
                "path": copy["path"],
                "roots": sorted(found, key=node_sort_key),
                "cycle": on_cycle,
            })
    violations.sort(key=lambda item: (item["repository"], item["path"]))
    return {
        "name": "single-canonical-source",
        "holds": not violations,
        "violations": violations,
    }


def _policy(copies):
    """Each copy should trace to one acyclic root.

    A relay does not by itself break the policy: the bytes can still have a
    single root. Two roots, or a cycle, do. ``graphlib`` names one cycle;
    the walk marks every node that can reach a cycle, including that one.
    """
    forward = _vendor_forward(copies)
    cycle = _graph_cycle(forward)
    roots, cyclic = _walk_roots(forward)
    if cycle is not None:
        cyclic.update(cycle)
    return _violations(copies, roots, cyclic)


def _downstream(source, children, copy_owner):
    """Shortest hop from ``source`` to each repo that vendors it, and the relays.

    ``children`` maps an upstream id to the copies that declare ``vendorsFrom``
    toward it, so the walk is the reverse of the stored edge. Distances use
    ``collections.deque``. ONTO's knowledgegraph helpers do not expose this
    reverse reachability; ``graphlib`` is used separately to judge cycles.
    """
    depth = {}
    dist = {source: 0}
    seen = {source}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for copy in children.get(node, ()):
            hop = dist[node] + 1
            repo = copy["repository"]
            if repo not in depth:
                depth[repo] = hop
            child = copy["id"]
            if child in seen:
                continue
            seen.add(child)
            dist[child] = hop
            queue.append(child)
    relays = set()
    for node in seen:
        owner = copy_owner.get(node)
        if owner is None or owner not in depth:
            continue
        for copy in children.get(node, ()):
            if copy["repository"] != owner:
                relays.add(owner)
                break
    return depth, relays


def _source_record(ident, depth, relays):
    repository, separator, source = ident.partition(":")
    if not separator:
        repository, source = ident, ""
    reached = [
        {"repository": repo, "depth": depth[repo]}
        for repo in sorted(depth, key=lambda repo: (depth[repo], repo))
    ]
    return {
        "id": ident,
        "repository": repository,
        "source": source,
        "downstream_repos": len(reached),
        "reached": reached,
        "relays": sorted(relays),
    }


def _sources(copies):
    children = defaultdict(list)
    for copy in copies:
        children[copy["upstream"]].append(copy)
    copy_owner = {copy["id"]: copy["repository"] for copy in copies}
    rows = [
        _source_record(source, *_downstream(source, children, copy_owner))
        for source in children
    ]
    rows.sort(key=lambda row: (-row["downstream_repos"], node_sort_key(row["id"])))
    return rows


def impact_report(locks):
    """Reverse walk of a fleet of vendor-lock/1 documents.

    ``locks`` is a list of ``{"repository", "document"}``. For each upstream
    ``repository:source``, report how many downstream repositories a change
    reaches, the shortest hop to each, and which of those repositories also
    pass the file onward (relays).
    """
    copies = _vendor_copies(locks)
    return {"sources": _sources(copies), "policy": _policy(copies)}


def _series_text(body):
    lines = [f"tool: {body['tool']['name']} {body['tool']['version']}"]
    lines.append(f"classification: {body['provenance']['classification']['path']}")
    lines.append(f"classification_sha256: {body['provenance']['classification']['sha256']}")
    lines.append(f"profile: {body['provenance']['profile']}")
    for snapshot in body["provenance"]["snapshots"]:
        lines.append(f"snapshot {snapshot['label']}: {snapshot['path']} sha256:{snapshot['sha256']}")
    for pair in body["pairs"]:
        lines.append(f"pair {pair['left']} -> {pair['right']}:")
        delta = pair["graph"]["delta"]
        lines.append(
            f"  independent_cycles: {delta['independent_cycles']:+d} "
            f"weakly_connected_components: {delta['weakly_connected_components']:+d}"
        )
        changed = pair["graph"]["edges_changed"]
        added = ", ".join(
            f"{node_text(item['source'])} {item['relation']} {node_text(item['target'])}"
            for item in changed["added"]
        ) or "(none)"
        removed = ", ".join(
            f"{node_text(item['source'])} {item['relation']} {node_text(item['target'])}"
            for item in changed["removed"]
        ) or "(none)"
        lines.append(f"  added: {added}")
        lines.append(f"  removed: {removed}")
        for name in sorted(pair["classes"]):
            row = pair["classes"][name]
            lines.append(
                f"  {name}: edges {row['delta']['edges']:+d} "
                f"independent_cycles {row['delta']['independent_cycles']:+d}"
            )
    lines.extend(_churn_lines(body))
    return "\n".join(lines) + "\n"


def _churn_lines(body):
    lines = ["nodes:"]
    if not body["nodes"]:
        lines.append("  (none)")
    for row in body["nodes"]:
        disappeared = "(none)" if row["disappeared"] is None else row["disappeared"]
        categories = ", ".join(row["classes"]) if row["classes"] else "(none)"
        lines.append(
            f"  {node_text(row['id'])} first_seen={row['first_seen']} "
            f"last_seen={row['last_seen']} disappeared={disappeared} "
            f"enters={row['enters']} leaves={row['leaves']} churn={row['churn']} "
            f"classes={categories}"
        )
    lines.append("repeated_churn:")
    if not body["repeated_churn"]:
        lines.append("  (none)")
    for row in body["repeated_churn"]:
        lines.append(f"  {node_text(row['id'])} churn={row['churn']}")
    return lines


def _separator_key(name):
    """Fold a relation name the way TaxOntOy detects a separator alias.

    '_' , '-' , and spaces break tokens. An interior capital starts a new token.
    Comparison is case-insensitive, so ``depends_on`` and ``dependsOn`` share a key.
    """
    parts, token = [], []
    for char in name:
        if char in "_- ":
            if token:
                parts.append("".join(token).casefold())
                token = []
            continue
        if char.isupper() and token:
            parts.append("".join(token).casefold())
            token = [char]
        else:
            token.append(char)
    if token:
        parts.append("".join(token).casefold())
    return tuple(parts)


def _alias_of(name, classes):
    """Classified relations whose separator key equals ``name``."""
    key = _separator_key(name)
    return sorted(relation for relation in classes if _separator_key(relation) == key)


def _excluded_names(table, profile, classes):
    """Sorted excluded relation names for ``profile``.

    A ``taxontoy-relations/1`` or ``/2`` table stores them under ``excluded``.
    An unversioned table has no excluded list. A name that is also classified
    is an error, matching the TaxOntOy rule that the two sets are disjoint.
    """
    schema = table.get("schema")
    if schema in _KNOWN_RELATION_SCHEMAS:
        excluded = table.get("excluded")
        if not isinstance(excluded, dict):
            raise ValueError("excluded must be an object")
        if profile not in excluded:
            raise ValueError(f"excluded list missing for profile: {profile}")
        names = excluded[profile]
    elif isinstance(schema, str) and schema.startswith(_RELATION_SCHEMA_PREFIX):
        raise ValueError(f"excluded list is not defined for relation schema: {schema}")
    else:
        return []
    if not isinstance(names, list):
        raise ValueError(f"excluded.{profile} must be an array")
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name or name != name.strip():
            raise ValueError(f"excluded.{profile} names must be non-empty strings")
        if name in seen:
            raise ValueError(f"excluded.{profile} has a duplicate name: {name}")
        if name in classes:
            raise ValueError(f"{profile}: {name} is both classified and excluded")
        seen.add(name)
    return sorted(seen)


def _class_declares_acyclic(table, category):
    """True when ``properties.classes.<category>`` declares ``acyclic``."""
    props = table.get("properties")
    if not isinstance(props, dict):
        return False
    bodies = props.get("classes")
    if not isinstance(bodies, dict):
        return False
    body = bodies.get(category)
    if not isinstance(body, dict):
        return False
    return "acyclic" in body


def _exclusion_view(with_metrics, without_metrics, delta, *, acyclic_declared):
    """Flags for a with-excluded versus without-excluded comparison.

    ``delta`` is without minus with, the same direction as ``compare_graphs``.
    Excluding the relation removed cycles when the independent-cycle count fell.
    A directed cycle disappearing lowers that count as well. It split components
    when the weak-component count rose. ``exclusion_passes_acyclicity`` is set
    only when the class declares ``acyclic``, the graph that includes the
    relation has a directed cycle, and the graph that excludes it does not.
    """
    removed_cycles = delta["independent_cycles"] < 0
    removed_directed_cycle = delta["directed_cycle"] < 0
    split_components = delta["weakly_connected_components"] > 0
    passes = False
    if acyclic_declared and with_metrics["order_cycle"] is not None and without_metrics["order_cycle"] is None:
        passes = True
    return {
        "acyclic_declared": acyclic_declared,
        "with": with_metrics,
        "without": without_metrics,
        "delta": delta,
        "removed_cycles": removed_cycles,
        "removed_directed_cycle": removed_directed_cycle,
        "split_components": split_components,
        "exclusion_passes_acyclicity": passes,
    }


def _folded_metrics(graph, classes, assignments, category, cycle_limit, acyclic_declared):
    """Compare one class with ``assignments`` folded in, against the published classes.

    ``compare_graphs`` supplies the delta (without minus with).
    """
    folded = dict(classes)
    folded.update(assignments)
    with_report = summarize(graph, folded, cycle_limit=cycle_limit)
    without_report = summarize(graph, classes, cycle_limit=cycle_limit)
    compared = compare_graphs(with_report, without_report)
    row = compared["classes"][category]
    return _exclusion_view(row["left"], row["right"], row["delta"], acyclic_declared=acyclic_declared)


def _relation_edge_count(graph, name):
    return sum(1 for edge in graph["edges"] if edge_relation(edge) == name)


def _unclassified_delta(graph, classes, names):
    classified = set(classes)
    folded = classified | set(names)
    without = sum(1 for edge in graph["edges"] if edge_relation(edge) not in classified)
    included = sum(1 for edge in graph["edges"] if edge_relation(edge) not in folded)
    return {"with": included, "without": without, "delta": without - included}


def excluded_impact(graph, classes, table, profile, *, cycle_limit=5):
    """Structural effect of the profile's excluded relations.

    For each class, every excluded relation is folded into that class and the
    result is compared with the published class through ``compare_graphs``.
    Each excluded relation is also folded alone, once per class, so a cycle or
    a split can be attributed to one name. ``depends_on`` is an alias of
    ``dependsOn`` when their separator keys match; ``acyclicity_flags`` lists
    the relation/class pairs whose exclusion is what lets a declared-acyclic
    class pass.
    """
    if not isinstance(table, dict):
        raise ValueError("classification JSON must be an object")
    names = _excluded_names(table, profile, classes)
    categories = sorted(set(classes.values()))
    declared = {category: _class_declares_acyclic(table, category) for category in categories}
    class_rows = {}
    for category in categories:
        assignments = {name: category for name in names}
        class_rows[category] = _folded_metrics(
            graph, classes, assignments, category, cycle_limit, declared[category])
    relations = []
    flags = []
    for name in names:
        impacts = []
        for category in categories:
            view = _folded_metrics(
                graph, classes, {name: category}, category, cycle_limit, declared[category])
            impacts.append({"class": category, **view})
            if view["exclusion_passes_acyclicity"]:
                flags.append({"class": category, "relation": name})
        relations.append({
            "alias_of": _alias_of(name, classes),
            "edges": _relation_edge_count(graph, name),
            "impacts": impacts,
            "relation": name,
        })
    flags.sort(key=lambda item: (item["relation"], item["class"]))
    return {
        "acyclicity_flags": flags,
        "classes": class_rows,
        "excluded": names,
        "relations": relations,
        "unclassified_edges": _unclassified_delta(graph, classes, names),
    }


def series_main(argv):
    parser = argparse.ArgumentParser(prog=f"{TOOL_NAME} series", description="Compare an ordered list of graph snapshots.")
    parser.add_argument("classification", nargs="?", type=Path)
    parser.add_argument("profile", nargs="?")
    parser.add_argument("--snapshot", action="append", default=[], metavar="LABEL=PATH",
                        help="one snapshot; repeat in time order. Overrides TOPONTOLOGY_SNAPSHOTS")
    parser.add_argument("--graph-format", choices=_GRAPH_FORMATS, default="nodes-edges")
    parser.add_argument("--format", choices=("text", "json", "canonical"), default=None)
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("--cycle-limit", type=int, default=5, metavar="N")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    fmt = _resolve_format(args)
    if args.cycle_limit < 0:
        raise ValueError("--cycle-limit must be >= 0")
    classification = _resolve_input(args.classification, "TOPONTOLOGY_CLASSIFICATION", "classification path")
    profile = _resolve_profile(args.profile)
    classes = _load_classes(classification, profile)
    snapshots = []
    for label, path in _resolve_snapshots(args.snapshot):
        snapshots.append({
            "label": label,
            "path": path,
            "graph": _load_graph(path, args.graph_format),
        })
    body = series_report(snapshots, classes, cycle_limit=args.cycle_limit)
    provenance = {
        "snapshots": [
            {"label": item["label"], "path": item["path"].as_posix(), "sha256": file_record(item["path"])["sha256"]}
            for item in snapshots
        ],
        "classification": file_record(classification),
        "profile": profile,
    }
    document_body = document(body, provenance)
    text = _series_text(document_body) if fmt == "text" else dump_json(document_body)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


def _impact_text(body):
    lines = [f"tool: {body['tool']['name']} {body['tool']['version']}"]
    for lock in body["provenance"]["locks"]:
        lines.append(f"lock {lock['repository']}: {lock['path']} sha256:{lock['sha256']}")
    if not body["sources"]:
        lines.append("sources: (none)")
    for source in body["sources"]:
        relays = ", ".join(source["relays"]) if source["relays"] else "(none)"
        lines.append(
            f"source {source['id']} downstream_repos={source['downstream_repos']} relays={relays}"
        )
        by_depth = defaultdict(list)
        for item in source["reached"]:
            by_depth[item["depth"]].append(item["repository"])
        for depth in sorted(by_depth):
            lines.append(f"  depth {depth}: {', '.join(by_depth[depth])}")
    policy = body["policy"]
    if policy["holds"]:
        lines.append("policy: single-canonical-source holds")
    else:
        lines.append(f"policy: single-canonical-source violations={len(policy['violations'])}")
        for item in policy["violations"]:
            roots = ", ".join(item["roots"]) if item["roots"] else "(none)"
            cycle = "yes" if item["cycle"] else "no"
            lines.append(
                f"  violation {item['repository']} {item['path']} roots={roots} cycle={cycle}"
            )
    return "\n".join(lines) + "\n"


def impact_main(argv):
    parser = argparse.ArgumentParser(
        prog=f"{TOOL_NAME} impact",
        description="Report how far each vendored file reaches downstream.",
    )
    parser.add_argument("--lock", action="append", default=[], metavar="REPOSITORY=PATH",
                        help="vendor-lock/1 owned by REPOSITORY. Overrides TOPONTOLOGY_LOCKS")
    parser.add_argument("--format", choices=("text", "json", "canonical"), default=None)
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    fmt = _resolve_format(args)
    loaded = []
    for repository, path in _resolve_locks(args.lock):
        loaded.append({
            "repository": repository,
            "path": path,
            "document": json.loads(path.read_text(encoding="utf-8")),
        })
    body = impact_report(loaded)
    provenance = {
        "locks": sorted(
            (
                {
                    "repository": item["repository"],
                    "path": item["path"].as_posix(),
                    "sha256": file_record(item["path"])["sha256"],
                }
                for item in loaded
            ),
            key=lambda item: (item["repository"], item["path"]),
        ),
    }
    document_body = document(body, provenance)
    text = _impact_text(document_body) if fmt == "text" else dump_json(document_body)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


def _resolve_optional(value, env_name):
    if value is not None:
        return Path(value)
    raw = _env_text(env_name)
    if raw:
        return Path(raw)
    return None


def _parse_source_token(token):
    text = token.strip()
    if not text:
        return None
    repo, separator, path = text.partition("=")
    repo, path = repo.strip(), path.strip()
    if not separator or not repo or not path:
        raise ValueError("source must be REPO=PATH")
    return repo, Path(path)


def _sources_from_env(text):
    sources = []
    for piece in text.replace(",", "\n").splitlines():
        item = _parse_source_token(piece)
        if item is not None:
            sources.append(item)
    return sources


def _resolve_sources(raw_args):
    if raw_args:
        parsed = []
        for token in raw_args:
            item = _parse_source_token(token)
            if item is not None:
                parsed.append(item)
        if not parsed:
            raise ValueError("source must be REPO=PATH")
        return parsed
    env = _env_text("TOPONTOLOGY_SOURCES")
    if not env:
        return []
    return _sources_from_env(env)


def _load_sources(pairs):
    if not pairs:
        raise ValueError("pass at least one source or set TOPONTOLOGY_SOURCES")
    loaded = []
    seen = set()
    for repo, path in pairs:
        if repo in seen:
            raise ValueError(f"duplicate source repo: {repo}")
        seen.add(repo)
        if not path.is_dir():
            raise ValueError(f"source is not a directory: {path}")
        loaded.append({"repo": repo, "path": path})
    loaded.sort(key=lambda item: item["repo"])
    return loaded


def _edge_list(edges):
    if not edges:
        return "(none)"
    parts = []
    for edge in edges:
        text = f"{edge['target']} -> {edge['repo']}"
        if edge.get("modules"):
            text += " modules=" + ",".join(edge["modules"])
        if edge.get("files"):
            text += " files=" + ",".join(edge["files"])
        parts.append(text)
    return "; ".join(parts)


def _unmapped_list(rows):
    if not rows:
        return "(none)"
    return ", ".join(f"{row['path']}:{row['line']}:{row['module']}" for row in rows)


def _deps_text(body):
    lines = [f"tool: {body['tool']['name']} {body['tool']['version']}"]
    declared = body["provenance"]["declared"]
    lines.append(f"declared: {declared['path']}")
    lines.append(f"declared_sha256: {declared['sha256']}")
    if "import_map" in body["provenance"]:
        record = body["provenance"]["import_map"]
        lines.append(f"import_map: {record['path']}")
        lines.append(f"import_map_sha256: {record['sha256']}")
    for source in body["provenance"]["sources"]:
        lines.append(f"source {source['repo']}: {source['path']}")
        for item in source["files"]:
            lines.append(f"  file {item['path']} sha256:{item['sha256']}")
    scanned = ", ".join(body["scanned"]) if body["scanned"] else "(none)"
    missing = ", ".join(body["not_scanned"]) if body["not_scanned"] else "(none)"
    lines.append(f"scanned: {scanned}")
    lines.append(f"not_scanned: {missing}")
    if not body["repos"]:
        lines.append("repos: (none)")
    for repo in body["repos"]:
        row = body["repos"][repo]
        lines.append(f"repo {repo}:")
        lines.append(f"  matching: {_edge_list(row['matching'])}")
        lines.append(f"  declared_but_unused: {_edge_list(row['declared_but_unused'])}")
        lines.append(f"  used_but_undeclared: {_edge_list(row['used_but_undeclared'])}")
        lines.append(f"  unmapped: {_unmapped_list(row['unmapped'])}")
    return "\n".join(lines) + "\n"


def deps_main(argv):
    parser = argparse.ArgumentParser(
        prog=f"{TOOL_NAME} deps",
        description="Compare declared SIBLINGS paths with Python imports in local source directories.",
    )
    parser.add_argument("--declared", type=Path,
                        help="Python file that assigns SIBLINGS. Overrides TOPONTOLOGY_DECLARED")
    parser.add_argument("--import-map", type=Path,
                        help="import-map/1 JSON. Overrides TOPONTOLOGY_IMPORT_MAP")
    parser.add_argument("--source", action="append", default=[], metavar="REPO=PATH",
                        help="local source directory for one repo. Overrides TOPONTOLOGY_SOURCES")
    parser.add_argument("--format", choices=("text", "json", "canonical"), default=None)
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    fmt = _resolve_format(args)
    declared = _resolve_input(args.declared, "TOPONTOLOGY_DECLARED", "declared file")
    import_map_path = _resolve_optional(args.import_map, "TOPONTOLOGY_IMPORT_MAP")
    sources = _load_sources(_resolve_sources(args.source))
    body, files = dependency_report(declared, sources, import_map_path)
    provenance = {
        "declared": file_record(declared),
        "sources": [
            {
                "repo": item["repo"],
                "path": item["path"].as_posix(),
                "files": [
                    {"path": row["path"], "sha256": row["sha256"]}
                    for row in files
                    if row["repo"] == item["repo"]
                ],
            }
            for item in sources
        ],
    }
    if import_map_path is not None:
        provenance["import_map"] = file_record(import_map_path)
    document_body = document(body, provenance)
    text = _deps_text(document_body) if fmt == "text" else dump_json(document_body)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


def _yes(flag):
    return "yes" if flag else "no"


def _flag_text(row):
    return (
        f"removed_cycles={_yes(row['removed_cycles'])} "
        f"removed_directed_cycle={_yes(row['removed_directed_cycle'])} "
        f"split_components={_yes(row['split_components'])} "
        f"acyclic_declared={_yes(row['acyclic_declared'])} "
        f"exclusion_passes_acyclicity={_yes(row['exclusion_passes_acyclicity'])}"
    )


def _impact_brief(delta):
    return (
        f"independent_cycles={_signed(delta['independent_cycles'])} "
        f"weakly_connected_components={_signed(delta['weakly_connected_components'])} "
        f"depth={_signed(delta['depth'])} "
        f"directed_cycle={_signed(delta['directed_cycle'])}"
    )


def _excluded_text(body):
    lines = [f"tool: {body['tool']['name']} {body['tool']['version']}"]
    lines.extend(_provenance_lines("", body["provenance"]))
    names = body["excluded"]
    lines.append("excluded: " + (", ".join(names) if names else "(none)"))
    flags = body["acyclicity_flags"]
    if flags:
        rendered = ", ".join(f"{item['relation']} {item['class']}" for item in flags)
    else:
        rendered = "(none)"
    lines.append(f"acyclicity_flags: {rendered}")
    unclassified = body["unclassified_edges"]
    lines.append(_change(
        "unclassified_edges", unclassified["with"], unclassified["without"], unclassified["delta"]).lstrip())
    if not body["classes"]:
        lines.append("classes: (none)")
    for name in sorted(body["classes"]):
        row = body["classes"][name]
        lines.extend(_comparison_lines(name, row["with"], row["without"], row["delta"]))
        lines.append(f"  flags: {_flag_text(row)}")
    if not body["relations"]:
        lines.append("relations: (none)")
    for relation in body["relations"]:
        alias = ", ".join(relation["alias_of"]) if relation["alias_of"] else "(none)"
        lines.append(
            f"relation {relation['relation']} edges={relation['edges']} alias_of={alias}"
        )
        if not relation["impacts"]:
            lines.append("  (no class)")
        for impact in relation["impacts"]:
            lines.append(f"  {impact['class']}: {_flag_text(impact)} {_impact_brief(impact['delta'])}")
    return "\n".join(lines) + "\n"


def excluded_main(argv):
    parser = argparse.ArgumentParser(
        prog=f"{TOOL_NAME} excluded",
        description="Compare each class with and without the table's excluded relations.",
    )
    parser.add_argument("graph", nargs="?", type=Path)
    parser.add_argument("classification", nargs="?", type=Path)
    parser.add_argument("profile", nargs="?")
    parser.add_argument("--graph-format", choices=_GRAPH_FORMATS, default="nodes-edges")
    parser.add_argument("--format", choices=("text", "json", "canonical"), default=None)
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("--cycle-limit", type=int, default=5, metavar="N")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    fmt = _resolve_format(args)
    if args.cycle_limit < 0:
        raise ValueError("--cycle-limit must be >= 0")
    graph_path = _resolve_input(args.graph, "TOPONTOLOGY_GRAPH", "graph path")
    classification = _resolve_input(args.classification, "TOPONTOLOGY_CLASSIFICATION", "classification path")
    profile = _resolve_profile(args.profile)
    classes = _load_classes(classification, profile)
    table = json.loads(classification.read_text(encoding="utf-8"))
    graph = _load_graph(graph_path, args.graph_format)
    body = excluded_impact(graph, classes, table, profile, cycle_limit=args.cycle_limit)
    document_body = document(body, input_provenance(graph_path, classification, profile))
    text = _excluded_text(document_body) if fmt == "text" else dump_json(document_body)
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    else:
        argv = list(argv)
    if argv[:1] == ["excluded"]:
        try:
            return excluded_main(argv[1:])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
            print(error, file=sys.stderr)
            return 1
    if argv[:1] == ["series"]:
        try:
            return series_main(argv[1:])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
            print(error, file=sys.stderr)
            return 1
    if argv[:1] == ["deps"]:
        try:
            return deps_main(argv[1:])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
            print(error, file=sys.stderr)
            return 1
    if argv[:1] == ["impact"]:
        try:
            return impact_main(argv[1:])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as error:
            print(error, file=sys.stderr)
            return 1
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
    parser.add_argument("--graph-format", choices=_GRAPH_FORMATS, default="nodes-edges",
                        help="nodes-edges reads a graph JSON; vendor-lock reads a vendor-lock/1 file; "
                             "aoi-siblings reads SIBLINGS from a Python file; "
                             "py-imports reads a py-imports/1 manifest of local source directories")
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
        graph = _load_graph(args.graph, args.graph_format)
        report = summarize(graph, classes, cycle_limit=args.cycle_limit)
        provenance = input_provenance(args.graph, args.classification, args.profile)
        if fmt == "mermaid":
            _require_class(report, args.class_name, "--class")
            chosen = [edge for edge in graph["edges"] if edge_relation(edge) in classes
                      and classes[edge_relation(edge)] == args.class_name]
            text = render_mermaid(graph, chosen, provenance, args.class_name)
        elif args.compare:
            other = _load_graph(args.compare, args.graph_format)
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
