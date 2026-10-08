import json
from pathlib import Path
import threading
import pytest
from werkzeug.serving import make_server
from workflow import *
from connector import create_connector
from app import create_app

ROOT = Path(__file__).parents[1]
RULES = json.loads((ROOT / "samples/checklist.json").read_text())
VALID = json.loads((ROOT / "samples/valid.json").read_text())


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "workflow.db")
    initialize(p)
    return p


def test_rule_evidence():
    assert check(VALID, RULES) == []
    findings = check(json.loads((ROOT / "samples/invalid.json").read_text()), RULES)
    assert {f["id"] for f in findings} == {
        "owner-required",
        "version-format",
        "date-order",
        "approval-value",
    }
    assert next(f for f in findings if f["id"] == "owner-required")["evidence"] == ""


@pytest.mark.parametrize(
    "field,value,rule",
    [
        ("owner", "", "owner-required"),
        ("effective_date", "2026-02-30", "effective-valid"),
        ("approval", "pending", "approval-value"),
        ("version", "v1", "version-format"),
        ("review_date", "2025-01-01", "date-order"),
    ],
)
def test_rules(field, value, rule):
    assert rule in {f["id"] for f in check({**VALID, field: value}, RULES)}


def approve(path, now=0):
    j = create_job(path, VALID, RULES, now)
    j = transition(
        path, j["id"], "request_review", 1, "Reviewer", "Ready for review", now=now
    )
    return transition(
        path, j["id"], "approve", 2, "Reviewer", "Requirements satisfied", now=now
    )


def test_states_stale_version_and_waiver(path):
    j = create_job(path, {**VALID, "owner": ""}, RULES)
    with pytest.raises(Conflict):
        transition(path, j["id"], "approve", 1, "A", "Approve directly")
    transition(path, j["id"], "request_review", 1, "A", "Please review")
    with pytest.raises(Conflict):
        transition(path, j["id"], "approve", 1, "A", "Stale review")
    with pytest.raises(Conflict):
        transition(path, j["id"], "approve", 2, "A", "No reason for waiver")
    j = transition(
        path,
        j["id"],
        "approve",
        2,
        "A",
        "Temporary exception",
        {"owner-required": "Synthetic demo exception acknowledged"},
    )
    assert j["state"] == "approved" and len(j["events"]) == 3
    assert "Synthetic demo exception" in j["events"][-1]["note"]


def test_restart_retry_and_no_duplicates(path, tmp_path):
    connector = create_connector(str(tmp_path / "tickets.db"), failures=1)
    server = make_server("127.0.0.1", 0, connector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        send = lambda p, k: deliver_http(url, p, k)
        j = approve(path)
        assert run_worker(path, send, now=0)["status"] == "pending"
        initialize(path)  # same durable state after process setup
        assert run_worker(path, send, now=1)["status"] == "idle"
        result = run_worker(path, send, now=2)
        assert result["status"] == "delivered"
        assert run_worker(path, send, now=9)["status"] == "idle"
        assert get_job(path, j["id"])["state"] == "synced"
        c = connector.test_client()
        assert len(c.get("/tickets").json["tickets"]) == 1
        payload = {
            "job_id": j["id"],
            "document_id": j["document"]["document_id"],
            "findings": j["findings"],
            "checklist_version": 1,
        }
        assert (
            deliver_http(url, payload, "document-review:" + j["id"])
            == result["ticket_id"]
        )
        assert len(c.get("/tickets").json["tickets"]) == 1
    finally:
        server.shutdown()
        thread.join()


def test_retry_limit(path):
    approve(path)

    def fail(p, k):
        raise ConnectionError("Unavailable")

    assert run_worker(path, fail, now=0)["status"] == "pending"
    assert run_worker(path, fail, now=2)["status"] == "pending"
    assert run_worker(path, fail, now=6)["status"] == "failed"
    assert run_worker(path, fail, now=100)["status"] == "idle"


def test_permanent_error(path):
    approve(path)

    def fail(p, k):
        raise ValueError("Invalid payload")

    assert run_worker(path, fail, now=0)["status"] == "failed"


def test_expired_lease_recovers(path):
    j = approve(path)
    with connect(path) as c:
        c.execute(
            "UPDATE outbox SET status='processing',lease_until=30,lease_token='old' WHERE job_id=?",
            (j["id"],),
        )
    assert run_worker(path, lambda p, k: "ONE", now=20)["status"] == "idle"
    assert run_worker(path, lambda p, k: "ONE", now=31)["status"] == "delivered"


def test_api(tmp_path):
    c = create_app(str(tmp_path / "api.db"), lambda p, k: "DEMO-1").test_client()
    j = c.post("/api/jobs", json={"document": VALID}).json
    assert c.get("/api/jobs/" + j["id"]).status_code == 200
    assert c.post("/api/jobs", json=[]).status_code == 400
    assert (
        c.post(
            "/api/jobs/" + j["id"] + "/actions",
            json={
                "action": "approve",
                "version": 1,
                "actor": "A",
                "note": "Out of order",
            },
        ).status_code
        == 409
    )
    assert c.get("/").status_code == 200
