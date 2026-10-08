import json, platform, statistics, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

from workflow import check

rules = json.loads((ROOT / "samples/checklist.json").read_text())
valid = json.loads((ROOT / "samples/valid.json").read_text())
cases = [
    ("valid", {}, []),
    ("owner missing", {"owner": ""}, ["owner-required"]),
    ("bad version", {"version": "draft"}, ["version-format"]),
    ("bad date", {"effective_date": "2026-02-30"}, ["effective-valid"]),
    ("pending approval", {"approval": "pending"}, ["approval-value"]),
    ("review before effective", {"review_date": "2025-01-01"}, ["date-order"]),
    ("missing review", {"review_date": None}, ["review-required"]),
    (
        "two errors",
        {"owner": "", "approval": "pending"},
        ["owner-required", "approval-value"],
    ),
]
results = []
tp = fp = fn = 0
latencies = []
for name, changes, labels in cases:
    start = time.perf_counter()
    actual = {f["id"] for f in check({**valid, **changes}, rules)}
    latencies.append((time.perf_counter() - start) * 1000)
    expected = set(labels)
    tp += len(actual & expected)
    fp += len(actual - expected)
    fn += len(expected - actual)
    results.append(
        {"case": name, "expected": sorted(expected), "actual": sorted(actual)}
    )
report = {
    "run_utc": datetime.now(timezone.utc).isoformat(),
    "python": platform.python_version(),
    "dataset": "8 seeded metadata documents; checklist operations-policy v1",
    "true_positives": tp,
    "false_positives": fp,
    "false_negatives": fn,
    "precision": tp / (tp + fp) if tp + fp else 0,
    "recall": tp / (tp + fn) if tp + fn else 0,
    "p50_ms": round(statistics.median(latencies), 3),
    "cases": results,
    "limitations": "Deterministic synthetic checklist regression, not legal compliance accuracy or extraction quality.",
}
(ROOT / "docs/evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
