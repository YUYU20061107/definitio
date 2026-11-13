"""Session-bound, bounded write grants with durable reservations and revocation."""
from __future__ import annotations

import json
import time

from .model import canonical, digest, name_ok, require
from .operations import SPECS


class Authorizations:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_grants (
                    id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, prefix TEXT NOT NULL,
                    verbs TEXT NOT NULL, maximum INTEGER NOT NULL, used INTEGER NOT NULL,
                    expires REAL NOT NULL, revoked INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_grant_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, grant_id TEXT NOT NULL,
                    event TEXT NOT NULL, request_id TEXT, count INTEGER NOT NULL, at REAL NOT NULL,
                    UNIQUE(grant_id, event, request_id));
            """)

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
        value.update(remaining=value["maximum"] - value["used"], active=not value["revoked"] and time.time() < value["expires"],
                     audit=events, audit_total=count, audit_recent_limit=20)
        return value

    def grant(self, a, session):
        require(a.get("confirm") is True, "CONFIRM_REQUIRED", "仅在宿主或用户已批准作用域后创建授权")
        session_hash = self.session_hash(session)
        name_ok(a["target_prefix"])
        require(all(v == "dataset_import" or v in SPECS and SPECS[v][2] != "R0" for v in a["verbs"]),
                message="授权仅接受已实现的写动词；不包含破坏性操作")
        grant_id = "grant_" + digest({"session": session_hash, "request": a})[:32]
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO agent_grants VALUES (?,?,?,?,?,0,?,0)",
                       (grant_id, session_hash, a["target_prefix"], canonical(a["verbs"]), a["max_writes"], time.time() + a["ttl_seconds"]))
            db.execute("INSERT OR IGNORE INTO agent_grant_events(grant_id,event,request_id,count,at) VALUES (?,?,?,?,?)",
                       (grant_id, "granted", grant_id, 0, time.time()))
        return self.get(grant_id, session)

    def revoke(self, grant_id, session):
        self.get(grant_id, session)
        with self.store.connect() as db:
            db.execute("UPDATE agent_grants SET revoked=1 WHERE id=?", (grant_id,))
            db.execute("INSERT OR IGNORE INTO agent_grant_events(grant_id,event,request_id,count,at) VALUES (?,?,?,?,?)",
                       (grant_id, "revoked", grant_id, 0, time.time()))
        return self.get(grant_id, session)

    def reserve(self, grant_id, session, request_id, writes):
        grant = self.get(grant_id, session)
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

    def authorize(self, a, session, request_id, writes, replay=False):
        if a.get("authorization_id"):
            if replay:
                self.get(a["authorization_id"], session)
                return True
            self.reserve(a["authorization_id"], session, request_id + ":" + digest(a), writes)
            return True
        require(not writes or a.get("confirm") is True, "CONFIRM_REQUIRED", "写入需要确切确认或会话作用域授权")
        return a.get("confirm", False)
