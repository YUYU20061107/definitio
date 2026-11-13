"""R5: planner intent branches for spatial_join/import_data and explicit R0 constraint."""
import base64
import json

import pytest
from shapely.geometry import box, mapping

from definitio.model import GISFault
from definitio.planner import plan_goal
from definitio.runtime import Runtime


@pytest.fixture
def runtime(tmp_path):
    return Runtime(tmp_path / "workspace.sqlite3")


def dataset(runtime, name="src"):
    return runtime.import_dataset(name, {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(0, 0, 10, 10)), "properties": {"population": 100}}]},
        "EPSG:32649")


def test_spatial_join_intent_branch(runtime):
    d = dataset(runtime)
    other = dataset(runtime, "zones")
    steps = plan_goal("空间连接 " + other["id"], d, other["id"])
    assert steps[0]["verb"] == "gis_spatial_join"
    p = steps[0]["parameters"]
    assert p["input"] == d["id"] and p["join"] == other["id"]
    assert p["predicate"] == "intersects" and p["summary"] == "first"
    assert p["target"]


def test_spatial_join_nearest_all_keywords(runtime):
    d = dataset(runtime)
    other = dataset(runtime, "zones")
    steps = plan_goal("最近空间连接 全部 " + other["id"], d, other["id"])
    assert steps[0]["parameters"]["predicate"] == "nearest"
    assert steps[0]["parameters"]["summary"] == "all"


def test_spatial_join_requires_secondary(runtime):
    d = dataset(runtime)
    with pytest.raises(GISFault):
        plan_goal("空间连接", d)


def test_spatial_join_chain_executes(runtime):
    d = dataset(runtime)
    other = dataset(runtime, "zones")
    task = runtime.declare("空间连接 " + other["id"], dataset=d["id"], secondary=other["id"])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")


def import_uri():
    doc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(box(1, 1, 2, 2)), "properties": {"population": 5}}],
        "definitio": {"crs": "EPSG:32649",
                      "schema": [{"name": "population", "type": "integer", "nullable": False}],
                      "source_license": {"allow_import": True, "evidence_ref": "ev-1"}}}
    return "data:application/geo+json;base64," + base64.b64encode(json.dumps(doc).encode()).decode()


def test_import_intent_branch_first_step(runtime):
    d = dataset(runtime)
    uri = import_uri()
    steps = plan_goal("导入 " + uri + " 然后统计 population", d)
    assert steps[0]["verb"] == "gis_import_data"
    assert steps[0]["parameters"]["source_uri"] == uri
    assert steps[0]["parameters"]["target_name"]
    assert steps[0]["parameters"]["format"] == "geojson" and steps[0]["parameters"]["kind"] == "vector"
    assert steps[1]["verb"] == "gis_field_statistics"
    assert steps[1]["parameters"]["dataset"] == "$previous"


def test_import_chain_executes(runtime):
    d = dataset(runtime)
    task = runtime.declare("导入 " + import_uri() + " 然后统计 population", dataset=d["id"])
    task = runtime.execute(task["id"], task["plan_digest"], True)
    assert task["task_state"] == "COMMITTED", task.get("error")
    assert task["steps"][1]["result"]["groups"][0]["fields"]["population"]["sum"] == 5


def test_import_midchain_is_refused_with_reason(runtime):
    d = dataset(runtime)
    with pytest.raises(GISFault) as exc:
        plan_goal("缓冲 10 米 然后导入 " + import_uri(), d)
    assert "首步" in exc.value.message


def test_statistics_constraint_message_explains_r0_no_target(runtime):
    d = dataset(runtime)
    with pytest.raises(GISFault) as exc:
        plan_goal("统计 population 然后缓冲 10 米", d)
    assert exc.value.code == "INVALID_ARGUMENT"
    assert "R0" in exc.value.message and "target" in exc.value.message
    assert exc.value.details.get("constraint") == "readonly_step_must_be_last"


def test_validation_constraint_message_also_explains_r0(runtime):
    d = dataset(runtime)
    with pytest.raises(GISFault) as exc:
        plan_goal("检查几何 然后缓冲 10 米", d)
    assert "R0" in exc.value.message


def test_plan_length_cap_unchanged(runtime):
    d = dataset(runtime)
    with pytest.raises(GISFault):
        plan_goal("缓冲 1 米 然后缓冲 2 米 然后缓冲 3 米 然后缓冲 4 米 然后缓冲 5 米 然后缓冲 6 米 然后缓冲 7 米", d)


def test_existing_intents_unchanged(runtime):
    d = dataset(runtime)
    steps = plan_goal("按 population 融合 然后统计 population", d)
    assert [s["verb"] for s in steps] == ["gis_dissolve", "gis_field_statistics"]