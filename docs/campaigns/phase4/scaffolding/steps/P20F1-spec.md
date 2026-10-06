commit: fix(ci): P20F1 — portable ISO-8601 parsing and a symlink-loop guard that survives Python 3.13

# Step P20F1 — the two portability defects the published CI matrix found

Repo: `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`. The project is published at
`github.com/chpomob/twitch-ia-compagnon` with a GitHub Actions matrix over Python **3.10, 3.11, 3.12,
3.13** (`.github/workflows/ci.yml`). The first run was green on 3.11 and 3.12 and **red on 3.10 and 3.13**.
Both failures reproduce locally; both are real defects, not flakiness.

Test command (must pass on every supported version):
`python -m pytest tests/ -q -p no:cacheprovider`. The suite must not drop below **3,057 passed**.

## How to reproduce and verify (do this — the fix must be proven on the failing versions)

`uv` is installed. Create the two interpreters, install the project into each, and run the suite:

```bash
uv venv --python 3.10 /tmp/py310 && uv pip install -q --python /tmp/py310/bin/python -e ".[test]"
uv venv --python 3.13 /tmp/py313 && uv pip install -q --python /tmp/py313/bin/python -e ".[test]"
/tmp/py310/bin/python -m pytest tests/ -q -p no:cacheprovider
/tmp/py313/bin/python -m pytest tests/ -q -p no:cacheprovider
```

Both must be fully green when you are done, and `python -m pytest tests/ -q` on the local 3.11 `.venv`
must stay green.

## Defect 1 — Python 3.10: ISO-8601 timestamps with a `Z` are refused

`modules/kick/__init__.py:447` parses a webhook timestamp with `datetime.fromisoformat(value.strip())`.
Python 3.10's `fromisoformat` does **not** accept the trailing `Z` (nor several other ISO-8601 forms that
3.11+ accept), so a legitimate delivery carrying `...Z` is parsed as invalid and refused with **400**.
Reproduced locally on 3.10: `22 failed, 53 passed` in `tests/test_kick.py`, the first symptom being
`assert 400 == 200` in `tests/test_kick.py::test_ac32_an_author_id_with_a_colon_is_refused_and_counted`.

The same pattern exists at `modules/twitch/__init__.py:681` — it is latent there only because no test
feeds it a `Z` timestamp.

Required: parse these timestamps **portably across 3.10–3.13**, in one shared helper rather than two
copies, accepting the forms the platform actually sends (including a trailing `Z`, and an explicit
offset), never guessing a timezone for an ambiguous value, and still refusing a genuinely malformed
timestamp. The Kick behaviour is the contract: a valid delivery must be admitted on every supported
Python. Fix the Twitch call site in the same commit and **add the test that would have caught it** (a
`Z` timestamp), because a latent defect with no test is how this one survived four phases.

## Defect 2 — Python 3.13: a symlink loop is no longer refused

`core/overlay.py:208`–`:214` deliberately uses `Path.resolve()` rather than `os.path.realpath` — the
comment says so — precisely because a link loop used to raise. **Python 3.13 changed `Path.resolve()`** to
non-strict semantics: it no longer raises on a loop, so the guard silently accepts a looping path and the
collision protection this project fought nine gate passes for quietly stops holding on 3.13.

Reproduced locally on 3.13:
`tests/test_overlay.py::test_a_link_loop_cannot_be_canonicalised` — `Failed: DID NOT RAISE <class 'core.overlay.OverlayError'>`.

Required: a path that cannot be canonicalised — a symlink loop, a path whose resolution cannot settle —
is **refused explicitly** with the existing named diagnostic, on **every** supported Python, without
relying on a standard-library behaviour that changed between minor versions. Keep every other
canonicalisation guarantee: `~` and environment expansion, absolute form, `..`, symlink and **hard-link**
identity (paths that do not exist yet fall back to the canonical path comparison, as today). Convert the
loop case into a test that fails on 3.13 before your change and passes after it on all four versions.

## Constraints

- One atomic commit; the message is pinned at the top of this file. Never weaken, skip or delete an
  existing assertion — the two failing tests encode the correct behaviour; the code is what must change.
- No new runtime dependency. Keep `aiohttp` + `PyYAML` as the only ones.
- Report: the root cause of each defect in one sentence, the `file:line` changed, the shared helper you
  introduced, the suite counts on 3.10, 3.11, 3.12 and 3.13, and anything you could not verify.
