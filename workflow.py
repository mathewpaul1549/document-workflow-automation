"""Versioned checklist validation, optimistic review and a durable outbox."""

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import date
import urllib.request
import urllib.error


class Conflict(ValueError):
    pass


def check(document, checklist):
    if not isinstance(document, dict):
        raise ValueError("document must be a JSON object")
    findings = []
    for rule in checklist["rules"]:
        value = document.get(rule["field"])
        missing = value is None or (isinstance(value, str) and not value.strip())
        invalid = (
            (missing or not isinstance(value, str))
            if rule["type"] == "required"
            else False
        )
        if not missing:
            if rule["type"] == "pattern":
                invalid = (
                    not isinstance(value, str)
                    or re.fullmatch(rule["pattern"], value) is None
                )
            if rule["type"] == "date":
                try:
                    if not isinstance(value, str) or not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        raise ValueError()
                    date.fromisoformat(value)
                except (ValueError, TypeError):
                    invalid = True
            if rule["type"] == "enum":
                invalid = value not in rule["values"]
        if invalid:
            findings.append(
                {
                    "id": rule["id"],
                    "field": rule["field"],
                    "severity": rule["severity"],
                    "message": rule["message"],
                    "evidence": value,
                }
            )
    if document.get("effective_date") and document.get("review_date"):
        try:
            if date.fromisoformat(document["review_date"]) < date.fromisoformat(
                document["effective_date"]
            ):
                findings.append(
                    {
                        "id": "date-order",
                        "field": "review_date",
                        "severity": "error",
                        "message": "Review date must be on or after effective date.",
                        "evidence": {
                            "effective_date": document["effective_date"],
                            "review_date": document["review_date"],
                        },
                    }
                )
        except (ValueError, TypeError):
            pass
    return findings


def connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as con:
        con.executescript(
            """CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,state TEXT,version INTEGER,document TEXT,checklist TEXT,findings TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,job_id TEXT,actor TEXT,action TEXT,note TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS outbox(job_id TEXT PRIMARY KEY,status TEXT,attempts INTEGER,next_attempt REAL,lease_until REAL,lease_token TEXT,last_error TEXT,ticket_id TEXT);"""
        )


def event(con, identity, actor, action, note, now):
    con.execute(
        "INSERT INTO events(job_id,actor,action,note,created) VALUES(?,?,?,?,?)",
        (identity, actor, action, note, now),
    )


def create_job(path, document, checklist, now=None):
    findings = check(document, checklist)
    identity = uuid.uuid4().hex[:16]
    with connect(path) as con:
        con.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?)",
            (
                identity,
                "draft",
                1,
                json.dumps(document),
                json.dumps(checklist),
                json.dumps(findings),
            ),
        )
        event(
            con,
            identity,
            "system",
            "created",
            f"Checked against {checklist['id']} v{checklist['version']}",
            now if now is not None else time.time(),
        )
    return get_job(path, identity)


def get_job(path, identity):
    with connect(path) as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if not row:
            return None
        job = dict(row)
        for key in ("document", "checklist", "findings"):
            job[key] = json.loads(job[key])
        job["events"] = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM events WHERE job_id=? ORDER BY id", (identity,)
            )
        ]
        outbox = con.execute(
            "SELECT job_id,status,attempts,next_attempt,last_error,ticket_id FROM outbox WHERE job_id=?",
            (identity,),
        ).fetchone()
        job["delivery"] = dict(outbox) if outbox else None
    return job


def transition(path, identity, action, version, actor, note, waivers=None, now=None):
    if not all(
        isinstance(v, str) and 1 <= len(v.strip()) <= 1000 for v in (actor, note)
    ):
        raise ValueError("Actor and rationale required (max 1000 characters)")
    if type(version) is not int:
        raise ValueError("Integer version required")
    now = time.time() if now is None else now
    with connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        job = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if not job:
            raise ValueError("Job not found")
        if job["version"] != version:
            raise Conflict("Stale version: refresh before acting")
        allowed = {
            ("draft", "request_review"): "in_review",
            ("in_review", "approve"): "approved",
            ("in_review", "reject"): "rejected",
        }
        target = allowed.get((job["state"], action))
        if not target:
            raise Conflict("This action is not allowed in the current state")
        waivers = waivers or {}
        if not isinstance(waivers, dict):
            raise ValueError("Waivers must map finding IDs to reasons")
        if action == "approve":
            findings = json.loads(job["findings"])
            ids = {f["id"] for f in findings}
            if any(k not in ids for k in waivers):
                raise ValueError("Unknown waiver finding")
            for f in findings:
                reason = waivers.get(f["id"], "")
                if f["severity"] == "error" and (
                    not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000
                ):
                    raise Conflict(
                        "Each error needs a documented waiver of at least 10 characters before approval"
                    )
            con.execute(
                "INSERT INTO outbox VALUES(?,?,?,?,?,?,?,?)",
                (identity, "pending", 0, now, 0, "", "", ""),
            )
        con.execute(
            "UPDATE jobs SET state=?,version=version+1 WHERE id=?", (target, identity)
        )
        event(
            con,
            identity,
            actor,
            action,
            json.dumps({"note": note, "waivers": waivers}),
            now,
        )
    return get_job(path, identity)


def deliver_http(url, payload, key):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url + "/tickets",
        data=data,
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        if exc.code in {408, 429} or exc.code >= 500:
            raise ConnectionError(f"Transient connector HTTP {exc.code}") from exc
        raise ValueError(f"Permanent connector HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError("Connector unavailable or timed out") from exc
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("id"), str)
        or not result["id"]
    ):
        raise ValueError("Connector returned an invalid receipt")
    return result["id"]


def run_worker(path, connector, now=None):
    """One leased delivery attempt. Call repeatedly; next_attempt survives process restarts."""
    now = time.time() if now is None else now
    token = uuid.uuid4().hex
    with connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM outbox WHERE (status='pending' AND next_attempt<=?) OR (status='processing' AND lease_until<=?) ORDER BY next_attempt LIMIT 1",
            (now, now),
        ).fetchone()
        if not row:
            return {"status": "idle"}
        identity = row["job_id"]
        attempt = row["attempts"] + 1
        con.execute(
            "UPDATE outbox SET status='processing',lease_until=?,lease_token=?,attempts=? WHERE job_id=?",
            (now + 30, token, attempt, identity),
        )
    job = get_job(path, identity)
    payload = {
        "job_id": identity,
        "document_id": job["document"].get("document_id"),
        "findings": job["findings"],
        "checklist_version": job["checklist"]["version"],
    }
    try:
        ticket = connector(payload, "document-review:" + identity)
        status = "delivered"
        error = ""
    except (ConnectionError, ValueError) as exc:
        ticket = ""
        error = str(exc)[:500]
        status = (
            "pending" if isinstance(exc, ConnectionError) and attempt < 3 else "failed"
        )
    with connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT lease_token FROM outbox WHERE job_id=?", (identity,)
        ).fetchone()
        if row["lease_token"] != token:
            return {"status": "lease_lost"}
        con.execute(
            "UPDATE outbox SET status=?,next_attempt=?,lease_until=0,last_error=?,ticket_id=? WHERE job_id=?",
            (status, now + 2**attempt, error, ticket, identity),
        )
        if status == "delivered":
            con.execute(
                "UPDATE jobs SET state='synced',version=version+1 WHERE id=?",
                (identity,),
            )
        event(
            con,
            identity,
            "connector",
            "delivery_" + status,
            json.dumps({"attempt": attempt, "error": error, "ticket_id": ticket}),
            now,
        )
    return {
        "job_id": identity,
        "status": status,
        "attempt": attempt,
        "ticket_id": ticket,
        "error": error,
    }
