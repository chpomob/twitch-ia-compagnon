# Step P27F2 — gate findings F3 and F2 (memory bounds hold on failed evictions; unsupported polls get a reason)
commit: fix(phase3): P27F2 — memory bounds hold across failed startup evictions; unsupported poll reasons

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. Authority: the gate report
`docs/campaigns/phase3/scaffolding/gate-report-1.md` (read F2 and F3 in full — they carry runnable
reproductions), the phase 3 spec (R3/AC18, R7, AC39) and plan (P10, P21, P26).

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (project virtualenv).

## F3 — Major: failed startup evictions silently violate memory file bounds

Locations: `modules/viewer_memory/__init__.py:554`, `:752`, `:815`, `:887`, `:904`.

The operator's rule is that memory stays bounded (per file, in total bytes and in file COUNT) and that a
maximum is resolved by analysing least-used/oldest and deleting. The gate shows a path where evictions fail at
startup and the bounds are then silently exceeded — the invariant is stated but not enforced. Fix the
production behaviour so the configured bounds hold in the presence of a failing eviction (retry/escalate per
the spec's terminal-outcome rules, or refuse the offending write explicitly), and pin it with a test that
drives a failing eviction and asserts the bound still holds and the failure is visible (never silent).

## F2 — Major: unsupported poll capabilities have no AC39 reason

Locations: `modules/stream_control/__init__.py:886`, `:905`, `:925`; `tests/test_youtube.py:1595`, `:1630`;
`docs/README.md:742`.

Kick and YouTube publish no poll service, so `stream.poll.create` must appear unbound with a NAMED, value-free
reason per AC39 (the honest-capability rule). Today no reason is produced for those platforms and the README
row does not state it. Fix the module, the tests and the README row consistently.

## Constraints
- Fix the PRODUCTION code; never weaken, skip or delete an unrelated assertion. Where an assertion encoded the
  defect, change it to the normative outcome and say so in the commit body.
- Memory stays an optional module with the operator's configurability (duration, total size, file count) and
  the analysis-based eviction; do not change those defaults (30 days).
- One atomic commit; message pinned above.
- Report: `file:line` per finding, the test that now enforces each, anything not fixed and why.
