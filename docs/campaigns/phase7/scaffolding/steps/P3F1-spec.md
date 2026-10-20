# P3F1 — The extractor's four majors: codec acceptance, hidden state, charset prescan, UTF-8 guarantee

Step P3 (the in-module HTML-to-text extractor) reached `main` without a review: the campaign engine died
reading its diff, and the step was landed and then reviewed separately. That review found four majors,
all in `modules/web/__init__.py`, all reachable through `extract_text`. This step closes them. It is the
whole scope: nothing else changes.

## The four defects, verbatim from the review

**F1 — a codec can exist and still refuse `errors="replace"`.**
`extract_text(b'<p>body</p>', 'text/html; charset=idna')` raises `UnicodeError` (the IDNA codec rejects
the error handler), because only `LookupError` is caught. R3 says decoding never raises. Recognising a
codec is not the same as being able to decode with replacement.

**F2 — discarded content mutates the visible result.**
- `<noscript><title>hidden</title></noscript><title>real</title><p>body</p>` → `title is None`, because
  the hidden title set the title-done flag.
- `<noscript><title>hidden</noscript><p>visible</p>` → the hidden unclosed title leaves the in-title flag
  set, so `visible` becomes the title and the body is empty.
- `<p>a<noscript><pre>hidden</pre></noscript>b</p>` → `a\nb` instead of `ab`, because the hidden `pre`
  injected a block separator.
Inside a skipped element, descendants must not mutate title capture, preformatted state or separators.

**F3 — the charset prescan matches text that is not a declaration.**
`<!-- <meta charset="iso-8859-1"> --><p>caf\xc3\xa9</p>` and
`<meta name="description" content="charset=iso-8859-1"><p>caf\xc3\xa9</p>` both decode as Latin-1 and
produce `cafÃ©`. A comment and an unrelated attribute value are not declarations. Only a real `meta`
charset element, or a `meta http-equiv="content-type"` whose `content` carries `charset=`, may decide the
encoding — inside the 1024-byte bound.

**F4 — the extracted text is not guaranteed UTF-8 encodable.**
`text/plain; charset=unicode_escape` on the six ASCII bytes `\ud800` decodes to a lone surrogate even
with replacement; `cut_utf8`'s no-cut branch then returns it unchanged, and a later strict `encode()`
raises. The cutting branch returns `'?'`, so sanitisation depends on the bound — an invariant that
depends on a limit is not an invariant.

## What to do

- **F1**: accept a codec only if it is a text codec *and* can decode with the replacement error handler;
  probe it, and fall back to UTF-8 when it cannot. Catch `UnicodeError` as well as `LookupError` on the
  actual decode, so no label can make extraction raise.
- **F2**: keep the skip depth, but while inside a skipped element do not touch title capture, the
  preformatted flag or the separator list. A skipped subtree is invisible in every respect.
- **F3**: replace the free-text regex with a small prescan that reads `meta` elements and their
  attributes, skips comments entirely, and accepts only `charset=` on a `meta` element or `charset=` in
  the `content` of a `meta http-equiv="content-type"`.
- **F4**: guarantee the postcondition — the returned text is always UTF-8 encodable — by normalising
  once after decoding (and once after any cut), so no branch can return a lone surrogate.

## Tests (each must fail on the P3 tree and pass here)

One per finding, written against the exact inputs above, plus:
- a codec that exists but rejects replacement (`idna`), and a codec label that is not text (`base64`,
  `rot13`) — both fall back to UTF-8 without raising;
- the three hidden-state inputs of F2, asserting title and text exactly;
- the two F3 inputs, asserting `café` is recovered as UTF-8 **and** that a genuine `meta charset` still
  wins (the P3 test must keep passing);
- `extract_text(...).text.encode('utf-8')` does not raise for every body in the corpus, and
  `cut_utf8(text, n)` returns text whose strict encoding never raises for every `n` in 0..len(text).


## Also in scope: F1 from the closing gate

The gate reproduced a **HIGH** defect on the same file, and it belongs to this step because it is about
what extraction does to the call:

`web.read` with `request_timeout_seconds: 1` and an 8 MiB page of `<p>` tags returned **success after
3.908 s**. The fetch has a timer; the synchronous parse does not. The module's deadline is therefore not
a deadline at configuration time, only a network timeout, which R5 does not allow.

Reproduction (real asyncio timer, no injected clock):

```python
rig = await t.read_rig(max_response_bytes=8388608, request_timeout_seconds=1, cache_ttl_seconds=0)
rig.handle._sleeper = None
rig.transport.script('example.org', t.html_page(b'<p>' * (8388608 // 3)))
observation = await rig.read('https://example.org/')   # 3.908 success
```

Required: the whole configured deadline bounds the call including extraction and the result/cache work
that follows it. A bare `asyncio.wait_for` around synchronous work cannot preempt it, so:
- run extraction off the event loop (a worker thread) and await it under the remaining budget, discarding
  its result if the budget expires;
- give the parser a **cooperative deadline** checked as it walks (so the thread does not keep burning a
  core), aborting with the same deterministic `timeout timed_out` outcome;
- keep the outcome honest: a call that ran out of time is `timed_out`, never `success` with truncated text.

Test: the reproduction above (8 MiB `<p>` stream, 1 s setting) returns `timeout` at ≈1 s, and the
never-answering-body control still returns `timeout` at 1 s. Assert elapsed wall time under a small
margin, not just the status.

## Constraints

- `modules/web/__init__.py` and `tests/test_web.py` only (plus a fixture if a declaration case needs one).
- No new runtime dependency; `html.parser` and `codecs` are enough for the prescan.
- No test is skipped, xfailed, deleted or loosened; the full suite is green:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Standing phase-7 decisions apply: the module records nothing of the page, and no test opens a socket.
- Reply with what changed, the four tests by name, and the full-suite count.
