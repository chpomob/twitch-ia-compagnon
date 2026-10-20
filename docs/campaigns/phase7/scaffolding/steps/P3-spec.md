# Step P3 — The in-module HTML-to-text extractor and charset decoding (R3; AC7, AC8 text half)

Plan step `P3` of the approved Phase 7 plan for `twitch-ia-compagnon`.
Authority documents (read them, do not re-derive):
- Approved plan (this step is the whole scope, nothing more): `docs/campaigns/phase7/plan.md`
- Approved specification (requirement and acceptance-criteria wording): `docs/campaigns/phase7/spec.md`
- Brief with the operator's binding decisions: `docs/campaigns/phase7/brief.md`
- Approved v2 design (rationale, and the trust model the guard implements): `docs/design-v2.md`
Repository: `/media/chpo/HDD-papa/twitch-ia-compagnon` (Python, asyncio, event-bus core + modules). Working tree at `main`.

## Problem

`extract_text(body: bytes, content_type_header: str | None) -> Extracted(title,
  text)`:
  - charset: the `charset=` parameter of the header, else a `<meta charset>` / `<meta http-equiv
    content-type>` in the first 1024 bytes, else UTF-8; an unknown codec name falls back to UTF-8;
    decoding always `errors="replace"`.
  - `text/plain`: decoded text, whitespace runs collapsed per line.
  - HTML/XHTML: an `html.parser.HTMLParser` subclass with `convert_charrefs=True` (entities and
    numeric references decoded); skips everything inside `script`, `style`, `noscript`, `template`
    (depth-counted, so nested/unclosed tags cannot leak), ignores comments, declarations and
    processing instructions; emits a line break at the start/end of block elements (`p`, `div`,
    `br`, `li`, `h1`–`h6`, `tr`, `section`, `article`, `header`, `footer`, `pre`, `blockquote`,
    `table`, `ul`, `ol`, `hr`, …); captures the first `<title>` as `title` (not repeated in the
    text). Output: whitespace runs collapsed inside lines, blank lines collapsed, stripped. No
    JavaScript runs, nothing is rendered.
  - `cut_utf8(text, max_bytes) -> (str, bool)`: encodes, cuts at `max_bytes`, backs off to the last
    complete character (`decode(errors="ignore")` on the cut), returns whether anything was cut —
    reused by excerpts (P6) and page text (P5).
  - `tests/fixtures/web/PROVENANCE.md` names, for every fixture file, the public documentation its
    shape follows (WHATWG HTML parsing/charset rules for pages; later the MediaWiki and Brave docs).

## Requirements

Plan mapping: R3
Acceptance criteria owned by this step: AC7, AC8
Read the exact R/AC text in the specification file before writing code. Requirements not listed
here are other steps' responsibility — do not implement them.

Standing phase-7 decisions that override any contrary reading of a requirement:
- **Wikipedia works with no key; Brave is an option.** A profile with no credential must get a
  working Wikipedia search; Brave is configured, never required, and its key is a `${...}` reference.
- **Two actions only**, `web.search` and `web.read`. No combined action and no separate
  `wikipedia.summary`: Wikipedia is a provider behind the search contract.
- **The network guard is the only way out.** Every request, at every hop, goes through R4: no
  loopback, private, link-local or non-public address, redirects re-checked, the byte bound measured
  while streaming, the whole call bounded, and no proxy environment variable may ever be honoured.
- **Every bound is a setting with a validated range** (R5), and nothing returns more than its bound.
- **The query is never written.** A query may be a viewer's words: traces and diagnostics carry the
  provider, lengths, counts and the outcome, never the text. The in-memory cache key is not a record;
  it never leaves the process.
- **Read content is data.** The observation carries the untrusted marker, the brain renders it as
  data, and no configuration of this module can grant a capability: default-deny is untouched.
- **No new runtime dependency.** HTML becomes text with the in-module extractor; if a library looks
  unavoidable, stop and report instead of adding one.
- **No test opens a real socket.** Every test uses a fake transport; the real-instance trial is a
  documented manual procedure with its own record, never a pytest node.
- Close exactly the requirement(s) this step names. If a sibling path shows the same defect, report
  it in your final message instead of widening the step.
- No test is skipped, xfailed, deleted or loosened. The full suite is green at every step:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` (never the system python: no aiohttp).

## Files

[`modules/web/__init__.py`, `tests/test_web.py`, `tests/fixtures/web/page_basic.html`, `tests/fixtures/web/page_latin1.html`, `tests/fixtures/web/PROVENANCE.md`]

Touch ONLY these files. Creating a listed file that does not exist yet is part of the step.
If you believe another file must change, stop and report it instead of editing it.

## Dependencies

[P1, P2] (P2 also edits `modules/web/__init__.py` and `tests/test_web.py`;
  ordering after it keeps the two edits of those files sequential)

All dependencies are already merged into `main` when this step runs.

## Tests

AC7 — `page_basic.html` (a `<title>`, a 5 KiB `<script>`, a `<style>`, a comment,
  `&eacute;`, `&#x263A;`, three `<p>`) → text has the three paragraphs on 3 separate lines, `é` and
  `☺`, and 0 bytes from script/style/comment (assert sentinel strings planted in each are absent);
  `page_latin1.html` with header `text/html; charset=iso-8859-1` and byte `0xE9` → `é`; the same
  page with the charset only in `<meta>` → `é`. Unclosed `<script>` at EOF leaks nothing;
  `<noscript>`/`<template>` content absent. AC8 text half — `cut_utf8` on a 20 000-byte extracted
  text at 8192 → ≤ 8192 bytes, valid UTF-8, cut flag true, including when byte 8192 falls inside a
  multi-byte character; a 1000-byte text → unchanged, flag false.

Test command (must pass): `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Always use the project virtualenv: the SYSTEM python lacks `aiohttp`, which the proxy and the
two-process topology tests import — a `python3 -m pytest` run fails at collection there.
Every acceptance criterion this step owns must be enforced by a test that fails without the
change. Never weaken, skip, xfail or delete an existing assertion to make the suite pass; if an
existing test encodes a superseded guarantee, replace it and cite the superseding requirement in
the test docstring. Do not use positive-duration sleeps in tests — inject the clock, the RNG and
the transports. The suite must not drop below 3,124 passed / 18 skipped, the count at the start of
this phase; the seven allowlisted tests are adapted as the specification lists them, never weakened.

## Risks

`HTMLParser` is lenient but CDATA content of `script`/`style` is handled specially by
  the parser (`CDATA_CONTENT_ELEMENTS`); `noscript`/`template` are not, so depth counting must cover
  them. Pathological input (deep nesting, a 1 MiB comment) must stay linear — the extractor input is
  already capped at `max_response_bytes` by P4. A wrong charset label silently mis-decodes; the
  fallback is replacement, never an error, per R3.

## Constraints

- One atomic commit for the whole step when the suite is green; Conventional Commit message
  (`feat(phase7): ...`, `fix(phase7): ...`, `test(phase7): ...`, `refactor(phase7): ...`, `docs(phase7): ...`).
  The scope MUST be `phase7`: the repository history is full of `feat(phase4):` / `fix(phase3):` commits
  from earlier phases — do NOT copy that habit. Every commit this campaign makes is `(phase7)`.
- Keep the phase-0 to phase-4 guarantees in force: bounded admission, phase lifecycle, explicit
  terminal action outcomes, default-deny authorization (reads included), bounded retention, a single
  global startup/shutdown cleanup deadline, redaction of configured secrets in traces and loss
  diagnostics, the phase-3 moderation/memory/watch semantics, and the phase-4 configuration UI's
  local-only, read-only-for-secrets-and-actions surface.
- No module name may be added to `core/main.py` — a new module starts and stops through its manifest.
- Platform neutrality: contracts and brain must not depend on a specific platform; a second fake
  platform exercises the same contracts.
- A secret VALUE must never reach a response, a log line or a diagnostic — only its reference and its
  set/unset state.
- No new runtime dependency (aiohttp and PyYAML only); no model, provider or vendor name in code,
  config or commit messages.
- Report at the end: what changed, the exact test command output count, and anything you could
  not do because the plan did not cover it.
