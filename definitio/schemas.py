"""One machine-readable parameter contract for HTTP, MCP and the core runtime."""
from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator

from .model import GISFault


def text(description, **limits):
    return {"type": "string", "minLength": 1, "maxLength": 256, "description": description, **limits}


def choice(values, description):
    return {"type": "string", "enum": list(values), "description": description}


FIELD = {"type": "object", "properties": {
    "name": text("Field name, preserved exactly", maxLength=128),
    "type": choice(["string", "integer", "number", "boolean"], "Portable scalar type; no implicit coercion"),
    "nullable": {"type": "boolean", "description": "Whether null is allowed"}},
    "required": ["name", "type", "nullable"], "additionalProperties": False}
FIELD_NAMES = {"type": "array", "items": text("Exact field name", maxLength=128),
               "maxItems": 64, "uniqueItems": True, "description": "Ordered exact field names; [] is explicit and distinct from omitted"}
CRS = {"anyOf": [text("Explicit CRS, e.g. EPSG:32649; x,y coordinate order", maxLength=16384),
                  {"type": "integer", "minimum": 1},
                  {"type": "object", "properties": {"wkt": text("Full WKT definition", maxLength=16384)},
                   "required": ["wkt"], "additionalProperties": False}]}


def operation_schema(verb):
    from .operations import SPECS
    from .completion import SPECS as COMPLETION_SPECS, schema as completion_schema
    if verb in COMPLETION_SPECS:
        return completion_schema(verb, text, choice, CRS)
    if verb not in SPECS:
        raise GISFault("NOT_SUPPORTED", "本地参考后端尚未实现该动词", verb=verb)
    required = list(SPECS[verb][3])
    props = {
        "backend": choice(["local-geos"], "Implementation route; does not claim a G1 backend mapping is verified"),
        "confirm": {"type": "boolean", "description": "Explicit write authorization; never inferred by the engine"},
    }
    refs = {"input", "dataset", "source", "clip_by", "overlay", "table", "join", "zones", "values"}
    for key in required:
        if key in refs:
            props[key] = text("Immutable dataset ID from workspace_list/dataset_import, or $previous in an explicit task chain")
        elif key == "target":
            props[key] = text("Exact NEW dataset name; existing names are rejected, never overwritten or renamed", maxLength=64)
        elif key == "inputs":
            props[key] = {"type": "array", "items": text("Dataset ID"), "minItems": 1, "maxItems": 16}
        elif key in ("fields", "group_by"):
            props[key] = copy.deepcopy(FIELD_NAMES)
        elif key in ("crs", "target_crs"):
            props[key] = copy.deepcopy(CRS)
        else:
            props[key] = text(key)
    if verb == "gis_create_dataset":
        props["fields"] = {"type": "array", "items": FIELD, "maxItems": 64}
    if verb == "gis_add_field":
        field = copy.deepcopy(FIELD)
        field["properties"]["default"] = {"type": ["string", "number", "boolean", "null"], "description": "Explicit default; must match declared type"}
        field["required"].append("default")
        props["field"] = field
    if verb == "gis_calculate_field":
        props["field"] = text("Existing numeric field to calculate on a new copy", maxLength=128)
        props["expression"] = text("Safe arithmetic only: field names, numeric literals and + - * / %. No calls or code.", maxLength=256)
    if verb == "gis_buffer":
        props.update(distance={"type": "number", "description": "Finite signed distance in metres. Projected CRS only; zero-distance provisional domain is unsupported."},
                     unit=choice(["m"], "Metres, converted using the dataset CRS unit factor"),
                     dissolve={"type": "boolean", "description": "If true merge buffers, dropping business fields; otherwise retain each record's attributes"})
    if verb == "gis_convert_format":
        props["format"] = choice(["geojson"], "GeoJSON-compatible snapshot; CRS is explicitly retained")
    if verb == "gis_overlay":
        props["operation"] = choice(["intersection", "difference"], "Polygon-only overlay; input-side attributes retained")
    if verb == "gis_validate_geometry":
        props["repair"] = {"const": False, "type": "boolean", "description": "Read-only validation. Repair is not implemented."}
    if verb == "gis_field_statistics":
        props["statistics"] = {"type": "array", "items": choice(["count", "sum", "min", "max", "mean"], "Statistic"),
                               "minItems": 1, "maxItems": 5, "uniqueItems": True}
        props["fields"]["minItems"] = 1
        props["output"] = choice(["result"], "Read-only result; large results are retained as paged artifacts")
        props["null_policy"] = choice(["exclude"], "Explicit exclusion; null_count is still reported")
        required.append("null_policy")
    if verb == "gis_spatial_filter":
        props["predicate"] = choice(["intersects", "within", "contains", "disjoint"], "GEOS predicate; reference_wkt is in the input dataset CRS")
        props["reference_wkt"] = text("Reference geometry WKT in the dataset CRS", maxLength=65536)
    if verb == "gis_measure":
        props.update(operation=choice(["area", "length", "distance"], "Projected planar measurement; area m², length/distance m"),
                     a_wkt=text("2D nonempty geometry WKT in the explicitly provided CRS", maxLength=65536),
                     b_wkt=text("Required for distance; same explicit CRS", maxLength=65536))
    if verb == "gis_describe_algorithm":
        props["algorithm"] = text("Exact gis_* name from gis_list_algorithms")
    if verb == "gis_delete_field":
        props["fields"]["minItems"] = 1
        props["target_echo"] = text("Exact echo of target", maxLength=64)
        props["preflight_id"] = text("Single-use ID from destructive_preflight")
    if verb == "gis_import_data":
        props["source_uri"] = text("data:application/geo+json;base64, snapshot with definitio CRS/schema/source_license", maxLength=8 * 1024 * 1024)
        props["target_name"] = text("New logical dataset name", maxLength=64)
        props["format"] = choice(["geojson"], "Declared format")
        props["kind"] = choice(["vector"], "Local import domain")
    if verb == "gis_spatial_join":
        props["predicate"] = choice(["intersects", "contains", "within", "nearest"], "input geometry OP join geometry; geographic nearest rejected")
        props["summary"] = choice(["first", "all"], "Left outer join; first ties without stable business keys are rejected")
    if verb in ("gis_slope", "gis_aspect", "gis_hillshade"):
        props["z_factor"] = {"type": "number", "exclusiveMinimum": 0, "maximum": 1e9, "description": "Explicit elevation-to-horizontal-unit multiplier"}
    if verb == "gis_slope":
        props["unit"] = choice(["degree"], "Slope unit; explicitly required")
    if verb == "gis_aspect":
        props["convention"] = choice(["north_zero_clockwise"], "Compass degrees")
        props["flat_policy"] = choice(["nodata"], "Flat pixels have no aspect; distinct from north=0")
    if verb == "gis_hillshade":
        props["azimuth"] = {"type": "number", "minimum": 0, "exclusiveMaximum": 360}
        props["altitude"] = {"type": "number", "exclusiveMinimum": 0, "maximum": 90}
    if verb in ("gis_raster_calc", "gis_slope", "gis_aspect", "gis_hillshade", "gis_reclassify"):
        props["nodata_policy"] = choice(["propagate"], "Explicit null mask; terrain masks any 3x3 containing NoData, including the border")
    if verb == "gis_raster_calc":
        props["inputs"]["maxItems"] = 26
        props["expression"] = text("Safe arithmetic on A..Z in ordered inputs; + - * / % only; no implicit resampling")
    if verb == "gis_reclassify":
        rule = {"type": "object", "properties": {k: {"type": "number"} for k in ("min", "max", "value")},
                "required": ["min", "max", "min_inclusive", "max_inclusive", "value"], "additionalProperties": False}
        rule["properties"].update(min_inclusive={"type": "boolean"}, max_inclusive={"type": "boolean"})
        props["rules"] = {"type": "array", "items": rule, "minItems": 1, "maxItems": 128}
        props["unmapped"] = choice(["keep", "nodata", "fail"], "Explicit action for uncovered valid pixels")
    if verb == "gis_zonal_statistics":
        props["statistics"] = {"type": "array", "items": choice(["count", "sum", "mean", "min", "max"], "Statistic on valid pixel centers"), "minItems": 1, "uniqueItems": True}
        props["output"] = choice(["table"], "One row per zone; zones with no valid values remain")
        props["nodata_policy"] = choice(["exclude"], "Exclude masked values and report counts per zone")
        props["pixel_rule"] = choice(["center"], "GDAL rasterization pixel-center rule")
        props["weighting"] = choice(["none"], "No fractional boundary weighting")
    if verb == "gis_pointcloud_info":
        props.pop("confirm", None)
    if verb == "gis_run_processing":
        props["algorithm"] = text("Published local seed algorithm ID; named-verb bypass/defer/unknown IDs are rejected before persistence")
        props["params"] = {"type":"object", "properties":{"INPUT":text("Immutable vector dataset ID"),"ALL_PARTS":{"type":"boolean"},"OUTPUT":text("New logical dataset name",maxLength=64)},
                           "required":["INPUT","ALL_PARTS","OUTPUT"],"additionalProperties":False}
    if verb == "gis_pointcloud_process":
        from .pointcloud import ACTIONS
        props["action"] = choice(ACTIONS, "Closed PDAL action list; boundary produces a 2D vector under the local contract addendum")
        positive = {"type": "number", "exclusiveMinimum": 0}
        number3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
        action_params = {
            "clip": {"bounds": {"type": "array", "items": {"type": "number"}, "minItems": 6, "maxItems": 6}},
            "reproject": {"target_crs": CRS, "scale": {**number3, "items": positive}, "offset": number3},
            "thin_radius": {"radius": positive},
            "thin_decimate": {"step": {"type": "integer", "minimum": 1}, "offset": {"type": "integer", "minimum": 0}},
            "merge": {"inputs": {"type": "array", "items": text("Additional immutable pointcloud dataset ID"), "minItems": 1, "maxItems": 8}},
            "convert": {"format": choice(["las", "laz"], "Output encoding; all dimensions must survive")},
            "export_raster": {"resolution": positive, "attribute": text("Exact point dimension")},
            "export_raster_tin": {"resolution": positive, "max_triangle_edge_length": positive},
            "density": {"resolution": positive}, "boundary": {"resolution": positive, "threshold": {"type": "integer", "minimum": 1}}}
        props["params"] = {"type": "object", "description": "Exact action-specific keys; no arbitrary PDAL options/pipelines", "maxProperties": 3}
    if verb == "gis_list_algorithms":
        props.update(family=choice(["discovery", "data", "attributes", "crs", "geometry", "query", "raster", "pointcloud", "services", "map"], "Verb family"),
                     implemented_only={"type": "boolean", "default": False},
                     query=text("Substring of verb name or label", maxLength=128))
    schema = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    if verb == "gis_measure":
        schema["allOf"] = [{"if": {"properties": {"operation": {"const": "distance"}}, "required": ["operation"]},
                            "then": {"required": ["b_wkt"]}}]
    if verb == "gis_pointcloud_process":
        schema["allOf"] = [{"if": {"properties": {"action": {"const": action}}, "required": ["action"]},
                            "then": {"properties": {"params": {"type": "object", "properties": definitions,
                                     "required": list(definitions), "additionalProperties": False}}}}
                           for action, definitions in action_params.items()]
    return schema


def validate(schema, value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise GISFault("INVALID_ARGUMENT", "请求必须是有限、可序列化的 JSON", reason=str(exc)) from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        raise GISFault("INVALID_ARGUMENT", "参数不符合工具公开的 JSON Schema",
                       violations=[{"path": "/" + "/".join(str(k) for k in e.path), "message": e.message[:500]} for e in errors[:8]])
