"""Semantic object headers: identity, version, structure, CRS and axis order, units, lineage, scope, persistence.

Axis order is parsed from the CRS definition's own AXIS nodes. It is never inferred
from the EPSG code, and it is stored separately from the engine's x,y storage order.
"""
from __future__ import annotations

HEADER_FORMAT = "definitio-semantic-object-v1"
OBJECT_TYPES = {"vector": "vector_dataset", "raster": "raster_dataset", "pointcloud": "pointcloud_dataset"}
CRS_PROJECTION_KEYS = ("code", "definition_hash", "projected", "unit", "metres_per_unit",
                       "coordinate_order", "axis_policy", "axis_order")


def crs_projection(crs):
    """The single canonical CRS projection shared by preview, descriptor and artifact/export.

    The full WKT definition stays available through the metadata URI; every published
    surface projects the same identity keys, including axis_order and axis_policy.
    """
    return {key: crs[key] for key in CRS_PROJECTION_KEYS}


def _split_top_level(text):
    parts, depth, quoted, start = [], 0, False, 0
    for index, char in enumerate(text):
        if char == '"':
            quoted = not quoted
        elif not quoted:
            if char in "[(":
                depth += 1
            elif char in "])":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
    parts.append(text[start:].strip())
    return parts


def _balanced(text, open_index):
    depth, quoted = 0, False
    for index in range(open_index, len(text)):
        char = text[index]
        if char == '"':
            quoted = not quoted
        elif not quoted:
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return text[open_index + 1:index], index + 1
    raise ValueError("unbalanced WKT bracket")


def parse_axis_nodes(wkt):
    """Read AXIS[...] nodes from the definition itself, ordered by their declared ORDER."""
    nodes, cursor = [], 0
    while True:
        index = wkt.find("AXIS[", cursor)
        if index < 0:
            break
        body, cursor = _balanced(wkt, index + 4)
        parts = _split_top_level(body)
        order, unit = len(nodes) + 1, ""
        for part in parts[2:]:
            if part.startswith("ORDER["):
                order = int(part[len("ORDER["):-1])
            elif "UNIT[" in part:
                unit = part.split("UNIT[", 1)[1].split(",", 1)[0].strip('"')
        nodes.append({"name": parts[0].strip('"'), "direction": parts[1] if len(parts) > 1 else "unknown",
                      "order": order, "unit": unit})
    return sorted(nodes, key=lambda node: node["order"])


def axis_order(crs):
    declared = parse_axis_nodes(crs["wkt"])
    sequence = ",".join(node["direction"] for node in declared)
    return {"declared": declared, "sequence": sequence, "storage_order": "x,y",
            "derivation": "parsed_from_crs_wkt_axis_nodes",
            "differs_from_storage": sequence not in ("", "east,north")}


def operation_units(verb, parameters, crs):
    units = {"linear": {"unit": crs["unit"], "metres_per_unit": crs["metres_per_unit"]},
             "area": {"unit": "m²", "derivation": "linear_unit_squared"},
             "operation": None}
    factor = crs["metres_per_unit"]
    if verb == "gis_buffer":
        distance = parameters.get("distance")
        units["operation"] = {"distance": {
            "value": distance, "unit": parameters.get("unit", "m"), "crs_unit": crs["unit"],
            "metres_per_unit": factor,
            "value_in_crs_units": distance / factor if distance is not None and factor else None,
            "method": "divide_by_metres_per_unit"}}
    elif verb == "gis_measure":
        operation = parameters.get("operation")
        units["operation"] = {"measure": {"operation": operation,
                                          "reported_unit": "m²" if operation == "area" else "m",
                                          "method": "projected_planar"}}
    elif verb == "gis_field_statistics":
        units["operation"] = {"statistics": {"fields": parameters.get("fields", []),
                                             "requested": parameters.get("statistics", []),
                                             "unit": "declared_by_input_field; no implicit unit coercion",
                                             "null_policy": parameters.get("null_policy")}}
    return units


def result_header(*, verb, vid, parameters, result, inputs, task_id, step_id, evidence_ref,
                  result_index, workspace, authorization=None):
    """Header for a read-only analysis result, which has no dataset row of its own."""
    from .model import crs_info, digest, now
    crs = inputs[0]["crs"] if inputs else (crs_info(parameters["crs"]) if "crs" in parameters else None)
    return {
        "format": HEADER_FORMAT,
        "identity": {"object_id": evidence_ref, "object_type": "analysis_result", "name": f"{verb}:{step_id}",
                     "version": {"version_id": evidence_ref, "fingerprint": digest(result), "created_at": now()}},
        "structure": {"result_keys": sorted(result)[:64]},
        "crs": crs,
        "axis_order": axis_order(crs) if crs else None,
        "units": operation_units(verb, parameters, crs) if crs else {
            "linear": None, "area": None, "operation": None,
            "note": "result carries no CRS; units are declared by the operation itself"},
        "lineage": {"operation": {"verb": verb, "vid": vid, "parameters": parameters,
                                  "task_id": task_id, "step_id": step_id},
                    "inputs": [{"object_id": d["id"], "name": d.get("logical_name", d["name"]),
                                "version_id": d["id"], "fingerprint": d["fingerprint"]} for d in inputs]},
        "scope": {"workspace": workspace, "authorization": authorization},
        "persistence": {"store": "sqlite", "table": "tasks", "record_id": task_id,
                        "artifact_uri": f"definitio://tasks/{task_id}/results/{result_index}",
                        "metadata_uri": f"definitio://evidence/{evidence_ref}", "file_artifact": None},
    }


def object_header(document, *, operation=None, inputs=(), scope=None, persistence=None):
    kind = document.get("kind", "vector")
    crs = document["crs"]
    if "resource" in document:
        object_type = "managed_resource"
        structure = {"resource_kind": kind, "extent": document.get("bounds")}
    elif kind == "raster":
        object_type = OBJECT_TYPES[kind]
        structure = {"grid": document.get("grid")}
    elif kind == "pointcloud":
        object_type = OBJECT_TYPES[kind]
        structure = {"point_count": document.get("point_count"),
                     "point_dimensions": document.get("point_dimensions")}
    else:
        object_type = OBJECT_TYPES["vector"]
        structure = {"fields": document.get("fields", []), "feature_count": document.get("feature_count", 0),
                     "geometry_types": document.get("geometry_types", []), "bounds": document.get("bounds")}
    operation = dict(operation) if operation else None
    return {
        "format": HEADER_FORMAT,
        "identity": {"object_id": document["id"], "object_type": object_type,
                     "name": document.get("logical_name", document["name"]),
                     "version": {"version_id": document["id"], "fingerprint": document["fingerprint"],
                                 "created_at": document["created_at"]}},
        "structure": structure,
        "crs": crs,
        "axis_order": axis_order(crs),
        "units": operation_units((operation or {}).get("verb", ""), (operation or {}).get("parameters", {}), crs),
        "lineage": {"operation": operation,
                    "inputs": [{"object_id": d["id"], "name": d.get("logical_name", d["name"]),
                                "version_id": d["id"], "fingerprint": d["fingerprint"]} for d in inputs]},
        "scope": scope or {"workspace": None, "authorization": None},
        "persistence": persistence or {"store": "sqlite", "table": "datasets", "record_id": document["id"],
                                       "artifact_uri": f"definitio://datasets/{document['id']}",
                                       "metadata_uri": f"definitio://datasets/{document['id']}/metadata",
                                       "file_artifact": None},
    }