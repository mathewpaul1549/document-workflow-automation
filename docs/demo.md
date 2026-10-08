# Five-minute demonstration

1. Start the synthetic connector in a separate terminal: `python connector.py` (port 8104).
2. Start `python app.py` and open http://127.0.0.1:8103.
3. Create a job from **Policy with exceptions**. Inspect four findings and their field evidence.
4. Request review with a rationale. Approval is blocked unless every error has a waiver reason of at least 10 characters. Reject or document the individual waivers.
5. For the simplest delivery demo, create a **Passing policy** job, request review and approve it.
6. Click **Run worker once**. On a fresh connector database, the first new ticket fails with HTTP 503.
7. Wait at least 2 seconds and run the worker again. Expect `delivered`, `synced`, attempt 2 and ticket `DEMO-1`.
8. A further worker tick is idle. Inspect the events to see both failure and recovery.

The connector injects one failure per fresh database, not once per process restart. `DEMO_FAILURES` sets its initial failure count. All data is synthetic.
