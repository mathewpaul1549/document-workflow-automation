"""Local synthetic ticket service with durable idempotency and failure injection."""

import json
import os
from pathlib import Path
import sqlite3
from flask import Flask, request


def create_connector(database="data/tickets.sqlite3", failures=1):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    Path(database).parent.mkdir(parents=True, exist_ok=True)

    def db():
        return sqlite3.connect(database)

    with db() as con:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, key TEXT UNIQUE, payload TEXT); CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, failures INTEGER);"
        )
        con.execute("INSERT OR IGNORE INTO config VALUES(1,?)", (failures,))

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/tickets")
    def create():
        key = request.headers.get("Idempotency-Key", "")
        payload = request.get_json(silent=True)
        if not key or len(key) > 200 or not isinstance(payload, dict):
            return {"error": "Key and JSON object required"}, 400
        serialized = json.dumps(payload, sort_keys=True)
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT id,payload FROM tickets WHERE key=?", (key,)
            ).fetchone()
            if existing:
                if existing[1] != serialized:
                    return {"error": "Key reuse conflict"}, 409
                return {"id": f"DEMO-{existing[0]}", "replayed": True}
            remaining = con.execute(
                "SELECT failures FROM config WHERE id=1"
            ).fetchone()[0]
            if remaining:
                con.execute("UPDATE config SET failures=failures-1 WHERE id=1")
                return {"error": "Injected transient failure"}, 503
            cur = con.execute(
                "INSERT INTO tickets(key,payload) VALUES(?,?)", (key, serialized)
            )
            return {"id": f"DEMO-{cur.lastrowid}", "replayed": False}, 201

    @app.get("/tickets")
    def listing():
        with db() as con:
            rows = con.execute("SELECT id,payload FROM tickets ORDER BY id").fetchall()
        return {"tickets": [{"id": f"DEMO-{r[0]}", **json.loads(r[1])} for r in rows]}

    return app


if __name__ == "__main__":
    create_connector(failures=int(os.getenv("DEMO_FAILURES", "1"))).run(
        host="127.0.0.1", port=8104
    )
