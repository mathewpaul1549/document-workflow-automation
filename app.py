import json
import os
from pathlib import Path
from flask import Flask, render_template, request
from workflow import (
    initialize,
    create_job,
    get_job,
    transition,
    run_worker,
    deliver_http,
    Conflict,
)

ROOT = Path(__file__).parent


def create_app(database=None, connector=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000
    path = database or os.getenv("DATABASE_PATH", "data/workflow.sqlite3")
    initialize(path)
    checklist = json.loads((ROOT / "samples/checklist.json").read_text())
    connector = connector or (
        lambda payload, key: deliver_http("http://127.0.0.1:8104", payload, key)
    )

    def body():
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            raise ValueError("JSON object required")
        return b

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "document-workflow-automation"}

    @app.get("/api/checklist")
    def rules():
        return checklist

    @app.get("/api/samples/<name>")
    def sample(name):
        if name not in {"valid", "invalid"}:
            return {"error": "Sample not found"}, 404
        return json.loads((ROOT / f"samples/{name}.json").read_text())

    @app.post("/api/jobs")
    def create():
        return create_job(path, body().get("document"), checklist), 201

    @app.get("/api/jobs/<identity>")
    def get(identity):
        return get_job(path, identity) or ({"error": "Job not found"}, 404)

    @app.post("/api/jobs/<identity>/actions")
    def action(identity):
        b = body()
        return transition(
            path,
            identity,
            b.get("action"),
            b.get("version"),
            b.get("actor"),
            b.get("note"),
            b.get("waivers"),
        )

    @app.post("/api/worker/tick")
    def tick():
        return run_worker(path, connector)

    @app.errorhandler(Conflict)
    def conflict(exc):
        return {"error": str(exc)}, 409

    @app.errorhandler(ValueError)
    def invalid(exc):
        return {"error": str(exc)}, 400

    @app.errorhandler(413)
    def large(_):
        return {"error": "Limit 1 MB"}, 413

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "8103")))
