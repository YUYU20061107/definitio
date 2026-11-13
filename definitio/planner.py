"""Transparent local intent grammar. Never claims to be an LLM planner."""
from __future__ import annotations

import re

from .model import GISFault, require
from .store import identifier


def plan_goal(goal, dataset, secondary=None):
    require(isinstance(goal, str) and 0 < len(goal.strip()) <= 1000, message="请输入 1–1000 字的任务目标")
    # Data URIs contain ';' which is also a step separator; protect them while splitting.
    uris = re.findall(r"data:application/geo\+json;base64,[A-Za-z0-9+/=]+", goal)
    protected = goal
    for n, uri in enumerate(uris):
        protected = protected.replace(uri, f"__DEFINITIO_URI_{n}__")
    pieces = [x.strip(" ，,。；;") for x in re.split(r"然后|接着|再(?:将|把)?|[;；]", protected) if x.strip(" ，,。；;")]
    require(1 <= len(pieces) <= 6, message="一个任务最多 6 步；请使用“然后”连接")
    steps, input_ref = [], dataset["id"]
    suffix = identifier("t")[-6:]
    names = {f["name"] for f in dataset["fields"]}
    for i, text in enumerate(pieces, 1):
        for n, uri in enumerate(uris):
            text = text.replace(f"__DEFINITIO_URI_{n}__", uri)
        p, verb = {"backend": "local-geos"}, None
        target = f"{dataset['name'][:38]}_结果_{suffix}_{i}"
        if "缓冲" in text or re.search(r"\bbuffer\b", text, re.I):
            distance = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:米|m\b)", text, re.I)
            require(distance is not None, message="缓冲距离须显式给出，如“缓冲 50 米”")
            verb = "gis_buffer"
            p.update(input=input_ref, target=target, distance=float(distance.group(1)), unit="m", dissolve=False)
        elif "融合" in text or re.search(r"\bdissolve\b", text, re.I):
            field = re.search(r"(?:按|以)\s*[`\"']?([A-Za-z_][A-Za-z0-9_]*)", text)
            if field:
                key = field.group(1)
                require(key in names, "SCHEMA_MISMATCH", "任务中的分组字段不存在", field=key, available=sorted(names))
                fields = [key]
            elif any(x in text for x in ("全部", "全体", "不分组")):
                fields = []
            else:
                raise GISFault("INVALID_ARGUMENT", "请明确分组字段，如“按 group_code 融合”；全体融合请写“融合全部”")
            verb = "gis_dissolve"
            p.update(input=input_ref, target=target, fields=fields)
            names = set(fields)
        elif "重投影" in text or re.search(r"\breproject\b", text, re.I):
            code = re.search(r"EPSG\s*[:：]?\s*(\d+)", text, re.I)
            require(code is not None, message="请显式给出目标 CRS，如“重投影到 EPSG:4326”")
            verb = "gis_reproject"
            p.update(input=input_ref, target=target, target_crs=f"EPSG:{code.group(1)}")
        elif "裁剪" in text or re.search(r"\bclip\b", text, re.I):
            require(isinstance(secondary, str) and bool(secondary), message="裁剪须在工具中选择边界数据，或传 secondary_dataset")
            verb = "gis_clip"
            p.update(input=input_ref, target=target, clip_by=secondary)
        elif "空间连接" in text or re.search(r"\bspatial\s+join\b|\bjoin\b", text, re.I):
            require(isinstance(secondary, str) and bool(secondary), message="空间连接须在工具中选择连接数据，或传 secondary_dataset")
            predicate = ("nearest" if "最近" in text else "contains" if "包含" in text
                         else "within" if "位于" in text else "intersects")
            verb = "gis_spatial_join"
            p.update(input=input_ref, join=secondary, target=target, predicate=predicate,
                     summary="all" if "全部" in text else "first")
        elif "导入" in text or re.search(r"\bimport\b", text, re.I):
            require(i == 1, message="导入只允许作为任务首步：数据 URI 无法经 $previous 传递；请先导入再用后续步骤处理")
            uri = re.search(r"data:application/geo\+json;base64,[A-Za-z0-9+/=]+", text)
            require(uri is not None, message="导入须给出内联 data:application/geo+json;base64,… 数据 URI（含 definitio CRS/schema/source_license）")
            verb = "gis_import_data"
            p.update(source_uri=uri.group(0), target_name=target, format="geojson", kind="vector")
        elif "检查" in text or "有效性" in text or re.search(r"\bvalidate\b", text, re.I):
            verb = "gis_validate_geometry"
            p.update(dataset=input_ref, repair=False)
        elif "统计" in text:
            fields = [name for name in sorted(names) if re.search(rf"\b{re.escape(name)}\b", text)]
            require(bool(fields), message="请给出统计字段，如“统计 population”")
            verb = "gis_field_statistics"
            p.update(dataset=input_ref, fields=fields, group_by=[], statistics=["count", "sum", "min", "max", "mean"],
                     output="result", null_policy="exclude")
        elif "复制" in text or "导出" in text:
            verb = "gis_copy_dataset"
            p.update(source=input_ref, target=target)
        else:
            raise GISFault("INVALID_ARGUMENT", "本地规划器无法确定这一步，请改用工具表单或明确动词", segment=text,
                           examples=["按 group_code 融合", "缓冲 50 米", "重投影到 EPSG:4326", "检查几何",
                                     "空间连接 <数据句柄>", "导入 data:application/geo+json;base64,…"])
        from .vector_extra import write_target
        require(i == len(pieces) or write_target(verb, p) is not None, "INVALID_ARGUMENT",
                "只读步骤（检查/统计，治理级 R0）不产生 target 数据集，后续步骤无法用 $previous 引用；请把统计或检查放在任务最后一步",
                constraint="readonly_step_must_be_last", level="R0", step=i)
        steps.append({"verb": verb, "parameters": p})
        input_ref = "$previous"
    return steps
