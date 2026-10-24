# P19F2 — A credential never reaches a stream, and a fragment of it is still a credential

Two HIGH findings from the closing gate, both in `core/config_ui/__init__.py`, both reproduced by the gate
with a canary secret.

## F1 — the stored credential is forwarded verbatim to the UI's stderr

The supervisor forwards the child's stderr to the server's own stderr without redaction
(`core/config_ui/__init__.py:5669`, `:5680`, `:5850`). Through the real supervisor and a `POST /start`, the
gate observed the canary `credential-canary-2846159703` **once** in the server's stderr while the page
itself was clean.

Required: nothing that leaves the server is unredacted. Either the child's stderr is captured and never
forwarded raw, or it passes through the same redaction as every other surface before any byte is written.
A traceback's shape must still be visible (the operator needs to see *that* it failed); its values must not.

## F2 — redaction before truncation leaves a suffix

With a 9,042-character secret, the raw detail kept `TAIL-FRAGMENT-9153726804-END` — the retention keeps a
windowed excerpt and the secret's tail survives it (`:5670`, `:5690`, `:5921`, `:5937`).

Required: redact the **whole** occurrence, not a window of it. Order matters: locate and remove every
complete occurrence of every configured value in the buffer *before* any truncation, then truncate; a
partially matched value is removed entirely, never shortened. Prove it with a secret long enough to span
the retention bound.

## Tests (each fails on the current tree)

- The gate's reproduction, unchanged: a child whose stderr contains a traceback and a canary secret; the
  page, the status record and the server's stderr each contain the secret **zero** times, while the
  operator still sees that the child failed and its exit status.
- The long-secret reproduction: a 9,000+ character secret in the child's stderr; neither the secret nor any
  suffix fragment of it appears in the raw detail, the page, the status record or the stderr; and the raw
  detail is still bounded.
- A negative control: with **no** stored credential, a traceback in the child's stderr still reaches the
  operator's raw detail intact (the fix must not blind the operator).

## Constraints

- `core/config_ui/__init__.py` and its tests only.
- No new dependency; no test skipped, xfailed or loosened; full suite green on the project interpreter and
  on the 3.10 venv.
- Reply with the two reproductions' observed counts, the tests' names, and both suite counts.
