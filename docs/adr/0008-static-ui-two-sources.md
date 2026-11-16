# ADR 0008: One static UI, two data sources

Status: accepted (2026-11-16)

**Context.** A hiring manager should be able to see the system work at any time, but a public remote-code-execution service
is not acceptable and an always-on cluster costs money.

**Decision.** One static single-page app behind a `DataSource` interface. *Live* mode (local only) calls the API on localhost.
*Replay* mode (the public site, built with `VITE_MODE=replay`) plays recorded runs from a scrubbed `snapshot.json` with their
original timing. The replay build contains no API address and cannot execute anything.

**Consequences.** The public site is static files: nothing to keep warm, no credentials. Recordings go stale and carry a
fixed banner with the recording date and environment. Recording needs a live cluster (`make snapshot`).

**Verified by.** `web` vitest (replay timing, abort, speed, "cannot execute"), `tests/unit/test_snapshot.py` (scrubbing), the `showcase`
workflow's leak checks on the built site.
