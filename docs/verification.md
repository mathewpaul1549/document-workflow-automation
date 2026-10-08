# Verification record

Verified 8 October 2026 (Asia/Kolkata).

- Python regression tests: **12 passed** using Python 3.14.6.
- Reproducible synthetic evaluation: see `evaluation.json`, including per-case results and limitations.
- Browser walkthrough: local UI renders and the primary flow completes.
- Browser: draft → review → approved → synced; injected HTTP 503 recovered on attempt 2 with ticket DEMO-1.
- Automated HTTP integration test verifies a retry produces one ticket and repeated idempotency keys reuse it.

CI is included. Remote CI status must be checked separately after pushing.
