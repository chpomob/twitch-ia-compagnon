# P12F3 — The three test-treatment findings of the closing gate

The closing gate's first pass raised three findings about the tests themselves. This step closes all
three. Scope: `tests/test_web.py`, `tests/test_config_ui.py`, `core/config_ui/__init__.py` (comments
only), and `docs/campaigns/phase7/summary.md` for the stated invariant. Nothing else.

## F3b — the A2 predicate was narrowed instead of made precise

`tests/test_config_ui.py::test_a2_the_ui_source_names_no_module` originally searched the source for
`\bweb\b`. It was changed to `WEB_MODULE_FORMS`
(`r"[\"']web[\"']|\bmodules[./]web\b|\bweb\.(search|read)\b|/module/web\b"`), which no longer detects a
comment like `# Enable web when the operator grants its actions` — the gate's own counterexample.

The narrowing had a real cause: `core/config_ui/__init__.py` legitimately imports aiohttp as `web`
(`from aiohttp import web`, `web.Request`, `web.Response`, `web.Application`, `web.run_app`,
`web.StreamResponse`). So the rule must be precise, not narrow:

- every **COMMENT** token containing the bare word `web` must be flagged (a comment has no reason to name
  the module, and cannot be aiohttp usage);
- in code, the forms already covered stay covered, and any `web` that is not the aiohttp name
  (`import`ed binding or attribute of it) must be flagged;
- the test must contain the gate's counterexample as a case: a comment naming the module fails; a
  `from aiohttp import web` plus `web.Response(...)` does not.

If the UI source has a comment that names the module, reword it (remove the module name) rather than
weakening the rule.

## F4 — three new skip paths

`tests/test_web.py:5346`, `:5348`, `:5350` (inside `test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`)
call `pytest.skip` when git, the work tree or the base commit is missing. The gate forbids adding any skip
path, whether or not it executes here.

Replace each with a hard failure — a missing base commit in this repository is a broken checkout, not a
reason to pass. The test must run and pass unconditionally in this environment, where `7c29434` exists.

## F5 — the guard self-test creates a real socket

`tests/test_web.py:615` calls `socket.socket(socket.AF_INET, socket.SOCK_STREAM)`, which the gate's audit
hook observes as a real kernel socket. The phase's own invariant is that its tests open none (the only
sockets in the suite are the pre-existing loopback ones in `tests/test_kick.py`, which this phase does not
touch and must not relabel as a regression).

Keep the `connect`/`connect_ex` refusal assertions without a kernel socket: use a stand-in object whose
`connect`/`connect_ex` resolve through the patched `socket.socket` methods (for example
`object.__new__(socket.socket)`), so the guard is exercised before any OS call and nothing is created.
Then state the invariant precisely in `docs/campaigns/phase7/summary.md`: **this phase adds no socket of
any kind; the suite's only connections are the pre-existing loopback ones in `test_kick.py`**, and no test
contacts a real provider.

## Acceptance

- `tests/test_config_ui.py` (A2) detects the counterexample comment and still passes on the real UI source.
- `tests/test_web.py` has no `pytest.skip` added by this phase (the three are gone), and AC24 passes.
- Creating a socket is absent from the phase's tests; the audit-hook check the gate ran finds none.
- Full suite green: `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Amendment A1 in `docs/campaigns/phase7/spec.md` now authorizes the AC30 constants; verify the test still
  asserts exact constants and note it in your reply.
