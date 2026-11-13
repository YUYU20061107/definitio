"""SQLite-owned immutable snapshots; no client-controlled filesystem paths."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .model import GISFault, canonical, logical_fingerprint, snapshot_fingerprint, name_ok, now, summarize, require, digest


def identifier(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                    document TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dataset_heads (
                    name TEXT PRIMARY KEY, dataset_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS local_preflights (
                    id TEXT PRIMARY KEY, binding TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS file_artifacts (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, name TEXT UNIQUE NOT NULL,
                    task_id TEXT, step_id TEXT, dataset_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                    sha256_dataset TEXT NOT NULL, bytes INTEGER NOT NULL, document TEXT NOT NULL,
                    created_at TEXT NOT NULL);
            """)
            # Run the head migration once. Historical versions must never become heads again.
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='head_migration_v1'").fetchone():
                db.execute("INSERT INTO dataset_heads SELECT name,id FROM datasets")
                db.execute("CREATE TABLE head_migration_v1 (done INTEGER)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def dataset(self, reference, db=None):
        if db is None:
            with self.connect() as conn:
                return self.dataset(reference, conn)
        row = db.execute("SELECT document FROM datasets WHERE id=?", (reference,)).fetchone()
        logical_name = None
        if not row:
            row = db.execute("SELECT d.document FROM datasets d JOIN dataset_heads h ON d.id=h.dataset_id WHERE h.name=?", (reference,)).fetchone()
            logical_name = reference
        if not row:
            raise GISFault("NOT_FOUND", "数据集不存在", reference=reference)
        document = json.loads(row[0])
        if logical_name is not None:
            document["logical_name"] = logical_name
        return document

    def datasets(self):
        with self.connect() as db:
            rows = db.execute("SELECT h.name,d.document FROM datasets d JOIN dataset_heads h ON h.dataset_id=d.id ORDER BY d.created_at DESC").fetchall()
        return [{**{k: v for k, v in json.loads(row[1]).items() if k not in ("geojson", "raster", "pointcloud", "resource")}, "name": row[0]} for row in rows]

    def _attach_header(self, document, header_context):
        from .semantics import object_header
        context = header_context or {}
        scope = context.get("scope") or {"workspace": str(Path(self.path).parent), "authorization": None}
        document["semantic"] = object_header(document, operation=context.get("operation"),
                                             inputs=context.get("inputs", ()), scope=scope,
                                             persistence=context.get("persistence"))
        return document

    def insert_resource(self, name, kind, payload, crs, source, db=None, expected=None, header_context=None):
        from .completion import resource_summary
        name_ok(name)
        if db is None:
            with self.lock, self.connect() as conn:
                return self.insert_resource(name, kind, payload, crs, source, conn, expected, header_context)
        document = {"id": identifier("ds"), "name": name, "resource": payload, "created_at": now(), "source": source,
                    **resource_summary(kind, payload, crs)}
        self._attach_header(document, header_context)
        if expected is not None:
            row = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (name,)).fetchone()
            require(row and row[0] == expected, "TARGET_STATE_UNKNOWN", "地图逻辑版本已变化")
            db.execute("UPDATE dataset_heads SET dataset_id=? WHERE name=?", (document["id"], name))
        else:
            require(not db.execute("SELECT 1 FROM dataset_heads WHERE name=?", (name,)).fetchone(), "ALREADY_EXISTS", "资源名称已占用")
            db.execute("INSERT INTO dataset_heads VALUES (?,?)", (name, document["id"]))
        db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (document["id"], document["id"], canonical(document), document["created_at"]))
        return document

    def deletion_impact(self, dataset_id, db=None, exclude_task=None):
        if db is None:
            with self.connect() as conn:
                return self.deletion_impact(dataset_id, conn, exclude_task)
        # Scan actual persisted references, including historical map/catalog/task
        # versions. Unknown dependencies cause rejection, never cascading deletion.
        dependencies = []
        for table in ("datasets", "tasks"):
            for row in db.execute(f"SELECT id,document FROM {table}"):
                if row["id"] in (dataset_id, exclude_task):
                    continue
                if dataset_id in row["document"]:
                    dependencies.append({"kind": table, "id": row["id"]})
        return {"dataset": dataset_id, "dependent_objects": dependencies, "cascade": False}

    def destroy_dataset(self, dataset_id, db, task_id):
        impact = self.deletion_impact(dataset_id, db, task_id)
        require(not impact["dependent_objects"], "DEPENDENCY_BLOCKED", "对象已被任务、目录或地图引用；禁止级联销毁", impact=impact)
        db.execute("DELETE FROM dataset_heads WHERE dataset_id=?", (dataset_id,))
        count = db.execute("DELETE FROM datasets WHERE id=?", (dataset_id,)).rowcount
        require(count == 1 and not db.execute("SELECT 1 FROM datasets WHERE id=?", (dataset_id,)).fetchone(), "TARGET_STATE_UNKNOWN", "销毁读回失败")
        return impact

    def insert_dataset(self, name, geojson, crs, schema, source, db=None, header_context=None):
        name_ok(name)
        if db is None:
            with self.lock, self.connect() as conn:
                return self.insert_dataset(name, geojson, crs, schema, source, conn, header_context)
        document = {"id": identifier("ds"), "name": name, "geojson": geojson, "created_at": now(),
                    "source": source, **summarize(geojson, crs, schema)}
        self._attach_header(document, header_context)
        try:
            db.execute("INSERT INTO dataset_heads VALUES (?,?)", (name, document["id"]))
            db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (document["id"], document["id"], canonical(document), document["created_at"]))
        except sqlite3.IntegrityError as exc:
            raise GISFault("ALREADY_EXISTS", "目标名称已存在；不会覆盖或自动改名", target=name) from exc
        return document

    def advance_head(self, name, expected, proposal, source, db, header_context=None):
        row = db.execute("SELECT dataset_id FROM dataset_heads WHERE name=?", (name,)).fetchone()
        require(row and row[0] == expected, "TARGET_STATE_UNKNOWN", "追加目标版本已变化，须重新规划")
        document = {"id": identifier("ds"), "name": name, "geojson": proposal["geojson"], "created_at": now(),
                    "source": source, **summarize(proposal["geojson"], proposal["crs"], proposal["schema"])}
        self._attach_header(document, header_context)
        db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (document["id"], document["id"], canonical(document), document["created_at"]))
        db.execute("UPDATE dataset_heads SET dataset_id=? WHERE name=?", (document["id"], name))
        return document

    def rename_head(self, name, expected, new_name, db):
        name_ok(new_name)
        require(not db.execute("SELECT 1 FROM dataset_heads WHERE name=?", (new_name,)).fetchone(), "ALREADY_EXISTS", "新名称已占用")
        changed = db.execute("UPDATE dataset_heads SET name=? WHERE name=? AND dataset_id=?", (new_name, name, expected)).rowcount
        require(changed == 1, "TARGET_STATE_UNKNOWN", "逻辑名称已变化，须重新规划")

    def verify_snapshot(self, dataset):
        actual = snapshot_fingerprint(dataset)
        if actual != dataset["fingerprint"]:
            raise GISFault("TARGET_STATE_UNKNOWN", "数据快照与记录指纹不一致，已停止任务", dataset=dataset["id"])
        return actual

    def insert_raster(self, name, raster, crs, source, db=None, header_context=None):
        from .raster import summary
        name_ok(name)
        if db is None:
            with self.lock, self.connect() as conn:
                return self.insert_raster(name, raster, crs, source, conn, header_context)
        document = {"id": identifier("ds"), "name": name, "raster": raster, "created_at": now(),
                    "source": source, **summary(raster, crs)}
        self._attach_header(document, header_context)
        try:
            db.execute("INSERT INTO dataset_heads VALUES (?,?)", (name, document["id"]))
            db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (document["id"], document["id"], canonical(document), document["created_at"]))
        except sqlite3.IntegrityError as exc:
            raise GISFault("ALREADY_EXISTS", "目标名称已存在；不会覆盖", target=name) from exc
        return document

    def insert_pointcloud(self, name, pointcloud, crs, source, db=None, header_context=None):
        from .pointcloud import summary
        name_ok(name)
        if db is None:
            with self.lock, self.connect() as conn:
                return self.insert_pointcloud(name, pointcloud, crs, source, conn, header_context)
        document = {"id": identifier("ds"), "name": name, "pointcloud": pointcloud, "created_at": now(),
                    "source": source, **summary(pointcloud, crs)}
        self._attach_header(document, header_context)
        try:
            db.execute("INSERT INTO dataset_heads VALUES (?,?)", (name, document["id"]))
            db.execute("INSERT INTO datasets VALUES (?,?,?,?)", (document["id"], document["id"], canonical(document), document["created_at"]))
        except sqlite3.IntegrityError as exc:
            raise GISFault("ALREADY_EXISTS", "目标名称已存在；不会覆盖", target=name) from exc
        return document

    def save_file_artifact(self, record, db=None):
        if db is None:
            with self.lock, self.connect() as conn:
                return self.save_file_artifact(record, conn)
        db.execute("INSERT INTO file_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (record["id"], record["request_key"], record["name"], record["task_id"], record["step_id"],
                    record["dataset_id"], record["sha256"], record["sha256_dataset"], record["bytes"],
                    canonical(record), record["created_at"]))
        return record

    def _file_artifact_row(self, column, value, db=None):
        if db is None:
            with self.connect() as conn:
                return self._file_artifact_row(column, value, conn)
        row = db.execute(f"SELECT document FROM file_artifacts WHERE {column}=?", (value,)).fetchone()
        return json.loads(row[0]) if row else None

    def file_artifact(self, reference, db=None):
        record = self._file_artifact_row("id", reference, db) or self._file_artifact_row("name", reference, db)
        require(record is not None, "NOT_FOUND", "文件产物不存在", reference=reference)
        return record

    def file_artifact_by_key(self, request_key, db=None):
        return self._file_artifact_row("request_key", request_key, db)

    def file_artifact_by_name(self, name, db=None):
        return self._file_artifact_row("name", name, db)

    def file_artifacts(self):
        with self.connect() as db:
            rows = db.execute("SELECT document FROM file_artifacts ORDER BY created_at").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_task(self, task, db=None):
        if db is None:
            with self.connect() as conn:
                return self.save_task(task, conn)
        db.execute("INSERT INTO tasks VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                   (task["id"], canonical(task), task["created_at"]))

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT document FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise GISFault("NOT_FOUND", "任务不存在", task_id=task_id)
        return json.loads(row[0])

    def tasks(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT document FROM tasks ORDER BY created_at DESC LIMIT 100")]

    def save_evidence(self, evidence, db):
        db.execute("INSERT INTO evidence VALUES (?,?,?)", (evidence["id"], evidence["task_id"], canonical(evidence)))

    def evidence(self, evidence_id):
        with self.connect() as db:
            row = db.execute("SELECT document FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise GISFault("NOT_FOUND", "证据不存在")
        return json.loads(row[0])

    def recover_interrupted(self):
        with self.lock, self.connect() as db:
            for row in db.execute("SELECT document FROM tasks").fetchall():
                task = json.loads(row[0])
                if task["task_state"] in ("EXECUTING", "PRECHECKED"):
                    task.update(task_state="ISOLATED", state_as_of=now(),
                                error={"code": "TARGET_STATE_UNKNOWN", "message": "进程在任务结束前退出；须人工核查后新建任务"})
                    for step in task["steps"]:
                        if step["state"] == "EXECUTING":
                            step["state"] = "ISOLATED"
                    self.save_task(task, db)
