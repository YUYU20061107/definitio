"""Session-bound, bounded write grants with durable reservations and revocation."""
from __future__ import annotations

import json
import time

from .model import canonical, digest, name_ok, require
from .operations import SPECS


# RT-04: four independent side-effect classes. A grant that only covers dataset
# writes must never be stretched to cover file exports, logical-head changes or
# external publication. Plan-declared exports are data, not user authorization.
EFFECT_KINDS = ("dataset", "file_export", "logical_head", "external_publish")
DEFAULT_CAPABILITIES = ("dataset",)


class Authorizations:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_grants (
                    id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, prefix TEXT NOT NULL,
                    verbs TEXT NOT NULL, maximum INTEGER NOT NULL, used INTEGER NOT NULL,
                    expires REAL NOT NULL, revoked INTEGER NOT NULL,
                    capabilities TEXT NOT NULL, max_file_exports INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agent_grant_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, grant_id TEXT NOT NULL,
                    event TEXT NOT NULL, request_id TEXT, count INTEGER NOT NULL, at REAL NOT NULL,
                    UNIQUE(grant_id, event, request_id));
            """)
            # Migration for workspaces created before the side-effect split.
            columns = {row[1] for row in db.execute("PRAGMA table_info(agent_grants)")}
            if "capabilities" not in columns:
                db.execute("ALTER TABLE agent_grants ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[\"dataset\"]'")
            if "max_file_exports" not in columns:
                db.execute("ALTER TABLE agent_grants ADD COLUMN max_file_exports INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def session_hash(session):
        require(isinstance(session, str) and 32 <= len(session) <= 256, "AUTHORIZATION_REQUIRED",
                "作用域授权须绑定 MCP 会话或 X-Definitio-Session 高熵令牌")
        return digest(session)

    def get(self, grant_id, session):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM agent_grants WHERE id=? AND session_hash=?",
                             (grant_id, self.session_hash(session))).fetchone()
            require(row is not None, "AUTHORIZATION_REQUIRED", "授权不存在或不属于当前会话")
            value = dict(row)
            events = [dict(r) for r in db.execute(
                "SELECT event,request_id,count,at FROM agent_grant_events WHERE grant_id=? ORDER BY seq DESC LIMIT 20", (grant_id,))]
            count = db.execute("SELECT COUNT(*) FROM agent_grant_events WHERE grant_id=?", (grant_id,)).fetchone()[0]
        value.pop("session_hash")
        value["verbs"] = json.loads(value["verbs"])
        caps = json.loads(value["capabilities"])
        value["capabilities"] = caps
        value["effect_capabilities"] = sorted(caps)
        value.update(remaining=value["maximum"] - value["used"], active=not value["revoked"] and time.time() < value["expires"],
                     audit=events, audit_total=count, audit_recent_limit=20)
        return value

    def grant(self, a, session):
        require(a.get("confirm") is True, "CONFIRM_REQUIRED", "仅在宿主或用户已批准作用域后创建授权")
        session_hash = self.session_hash(session)
        name_ok(a["target_prefix"])
        require(all(v == "dataset_import" or v in SPECS and SPECS[v][2] != "R0" for v in a["verbs"]),
                message="授权仅接受已实现的写动词；不包含破坏性操作")
        caps = a.get("capabilities", list(DEFAULT_CAPABILITIES))
        require(isinstance(caps, list) and caps and all(k in EFFECT_KINDS for k in caps)
                and len(set(caps)) == len(caps), "INVALID_ARGUMENT",
                "capabilities 须为不重复的副作用类别子集", allowed=list(EFFECT_KINDS))
        max_files = a.get("max_file_exports", 0)
        require(type(max_files) is int and max_files >= 0, message="max_file_exports 须为非负整数")
        require(max_files == 0 or "file_export" in caps, "AUTHORIZATION_SCOPE",
                "文件导出次数须与 file_export 能力一并授予")
        grant_id = "grant_" + digest({"session": session_hash, "request": a})[:32]
        with self.store.connect() as db:
            db.execute("""INSERT OR IGNORE INTO agent_grants
                          (id, session_hash, prefix, verbs, maximum, used, expires, revoked, capabilities, max_file_exports)
                          VALUES (?,?,?,?,?,0,?,0,?,?)""",
                       (grant_id, session_hash, a["target_prefix"], canonical(a["verbs"]), a["max_writes"],
                        time.time() + a["ttl_seconds"], canonical(caps), max_files))
            db.execute("INSERT OR IGNORE INTO agent_grant_events(grant_id,event,request_id,count,at) VALUES (?,?,?,?,?)",
                       (grant_id, "granted", grant_id, 0, time.time()))
        return self.get(grant_id, session)

    @staticmethod
    def check_effects(grant, effects):
        """Every real side effect must be covered by a granted capability.

        Called before any write happens, including on the direct Runtime path that
        bypasses AgentService. Plan-declared exports are never a substitute.
        """
        for kind, count in sorted((effects or {}).items()):
            if count <= 0:
                continue
            require(kind in grant["capabilities"], "AUTHORIZATION_SCOPE",
                    "副作用类别未获授权；拒绝在写入前", kind=kind, count=count,
                    granted=sorted(grant["capabilities"]))
        exports = (effects or {}).get("file_export", 0)
        require(exports <= grant.get("max_file_exports", 0), "AUTHORIZATION_SCOPE",
                "文件导出次数超出显式授权", requested=exports, granted=grant.get("max_file_exports", 0))

    def revoke(self, grant_id, session):
        self.get(grant_id, session)
        with self.store.connect() as db:
            db.execute("UPDATE agent_grants SET revoked=1 WHERE id=?", (grant_id,))
            db.execute("INSERT OR IGNORE INTO agent_grant_events(grant_id,event,request_id,count,at) VALUES (?,?,?,?,?)",
                       (grant_id, "revoked", grant_id, 0, time.time()))
        return self.get(grant_id, session)

    def reserve(self, grant_id, session, request_id, writes, effects=None):
        grant = self.get(grant_id, session)
        # Capability coverage is checked BEFORE any write, and before replay
        # short-circuits, so a lost-response retry cannot smuggle a new effect.
        self.check_effects(grant, effects)
        # Ownership is checked even on response-loss replay.
        with self.store.connect() as db:
            prior = db.execute("SELECT 1 FROM agent_grant_events WHERE grant_id=? AND event='reserved' AND request_id=?",
                               (grant_id, request_id)).fetchone()
            require(grant["active"], "AUTHORIZATION_REQUIRED", "授权已到期或撤销")
            require(all(verb in grant["verbs"] and target.startswith(grant["prefix"]) for verb, target in writes),
                    "AUTHORIZATION_SCOPE", "动词或目标不在授权范围内", writes=writes)
            if prior:
                return
            require(len(writes) <= grant["remaining"], "AUTHORIZATION_EXHAUSTED", "授权剩余写入次数不足",
                    remaining=grant["remaining"], requested=len(writes))
            db.execute("UPDATE agent_grants SET used=used+? WHERE id=?", (len(writes), grant_id))
            db.execute("INSERT INTO agent_grant_events(grant_id,event,request_id,count,at) VALUES (?,?,?,?,?)",
                       (grant_id, "reserved", request_id, len(writes), time.time()))

    def authorize(self, a, session, request_id, writes, effects=None, replay=False):
        if a.get("authorization_id"):
            if replay:
                grant = self.get(a["authorization_id"], session)
                self.check_effects(grant, effects)
                return True
            self.reserve(a["authorization_id"], session, request_id + ":" + digest(a), writes, effects)
            return True
        require(not writes or a.get("confirm") is True, "CONFIRM_REQUIRED", "写入需要确切确认或会话作用域授权")
        return a.get("confirm", False)
