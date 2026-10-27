# P12F5 — Two defects in P12F4's own fixes: lost input on recovery, and a session-free route

The independent review of P12F4 (its loop died before reviewing) returned REQUEST_CHANGES with one blocker
and one major. Both are in P12F4's own code. Fix exactly these two; nothing else changes.

## F1 (blocker) — the stale-CSRF recovery throws away what the user typed

`core/config_ui/__init__.py:3571`:

```python
again = UIRequest(method="GET", path=page, query={}, headers=request.headers, body=b"")
response = _with_notice(route[1](again, session), self.wizard.expired_notice())
```

Re-issuing a GET discards the POST body, so every value the operator had typed on that screen is gone —
including safe ones such as `modules_directory`. B6 required recovering **and** keeping the submitted values
where it is safe to do so.

Re-render the same screen from the submitted form, with a fresh token, keeping the values that are not
secrets (a field whose declared kind is a credential or a secret stays blank, exactly as the normal render
does). The notice stays. Add a test that posts a screen with a non-secret value, a stale token, and asserts
both: a fresh token in the answer, and the value still present in the re-rendered form.

## F2 (major) — the favicon route bypasses the session

`core/config_ui/__init__.py:3498` answers `FAVICON_ROUTE` **before** the session check, so it is a
session-free endpoint. R1 says every route requires the access token and a session, with `/instance` the only
session-free route, and phase 9 closed on that contract.

Move the favicon answer after the session check: a browser sends its cookie with the favicon request, so an
authenticated page still gets it; an unauthenticated request gets the ordinary 401, which costs nothing. Keep
the 204 answer, and add a test asserting both: with a session → 204, without → 401 (and that `/instance`
remains the only session-free route).

## Acceptance

- F1's test fails against the old behaviour (the value is gone) and passes now — say how you know.
- F2: 204 with a session, 401 without; the route contract test that names the session-free routes still
  passes and still names only `/instance`.
- The five trial nodes pass twice, the ordinary suite is green with no daemon and its five skips, and no
  existing assertion is weakened. One atomic commit per concern.
