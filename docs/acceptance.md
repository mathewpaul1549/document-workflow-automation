# Acceptance criteria

- Seeded metadata violations produce the labeled findings and source values.
- Out-of-order and stale-version decisions fail.
- Every approval exception has an explicit waiver recorded in history.
- A transient HTTP failure retries after a durable delay; success creates one ticket.
- Permanent failure, retry exhaustion and expired lease recovery are tested.
