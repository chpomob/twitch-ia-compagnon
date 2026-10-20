# Brief — Phase 7: web knowledge (Wikipedia by default, Brave as an option)

Project: `twitch-ia-compagnon` (repo `/media/chpo/HDD-papa/twitch-ia-compagnon`, branch `main`,
HEAD `2ac13c3`). Phases 0–6 are delivered and approved; 3,901 tests green; the phase-6 closing gate
approved the tree at `12f7329`.

## Problem

The companion's brain can act inside the stream (chat, users, clips, capture, screen control) but it
cannot **read the world**: asked "who is this viewer", "what is this game", "what happened in this
patch", it can only answer from what it already knows or from the conversation. A streamer's chat
constantly asks exactly those questions, and a companion that answers them with a citation is worth
more than one that guesses.

The capability must fit the product's existing shape, and it does: the brain builds its tools from the
action registry (`ActionSpec.argument_schema`), the executor authorizes every call under DEFAULT-DENY,
and per-action deadlines and per-run budgets already exist. A new module's actions therefore become
tools without touching the brain loop. What the module must add is what the kernel does not do for it:
the provider, the network guard, the content bounds, the cache, and the honesty of a result that came
from a page nobody vouched for.

## Decisions already made by the operator (binding; a specification that narrows them is wrong)

- **1 — Wikipedia works with no key; Brave is an option.** The default provider is the MediaWiki API
  (no credential, no registration). Brave Search is configurable behind the same action and needs its
  own key, referenced as `${...}` like every other credential. A profile with no key must get a working
  Wikipedia search, and a profile that configures Brave must get Brave. A third provider may exist later
  without changing the action's contract.
- **2 — Two actions, `web.search` and `web.read`.** Search returns results; read returns the text of one
  URL. They are separate because the model decides what to open, and because `web.read` is also what a
  viewer's pasted link needs. No combined "search and read" action, and no separate `wikipedia.summary`
  action: Wikipedia is a provider behind `web.search`, not a third tool.
- **3 — `web.read` fetches public HTTP(S) under a guard, with an optional host allowlist.** Every fetch
  is checked against SSRF: no loopback, private, link-local or otherwise non-public address, including
  after each redirect; a bounded redirect chain; a bounded response size read while streaming; a bounded
  time. The optional `allowed_hosts` setting, when non-empty, restricts reads to those hosts. Empty means
  "any public host", which is the default.
- **4 — Bounded results and bounded pages, all configurable.** Defaults: 5 results per search, a short
  excerpt per result, 8 KiB of extracted text per read, a one-hour cache. Every bound is a setting with
  a validated range; the module never returns more than its configured bounds, and the cache is bounded
  in entries and in bytes as well as in time.
- **5 — A query is never written in clear.** A search or read may quote a viewer's words. Traces and
  diagnostics carry the provider, the byte length of the query, the result count and the outcome — never
  the query text, never a URL's query string, never the page text. A local debug setting may expose
  lengths only; it must not be possible to reconstruct the query from what the product records.
- **6 — What is read is data, not instructions.** The text of a page or a search excerpt is untrusted
  input: the observation carries an explicit marker that the content is external and unfollowed, the
  brain presents it as data, and no authorization changes because of it. A page that says "ignore your
  rules and run an action" must not gain any capability: the executor's default-deny is untouched, and
  no heuristic "injection filter" is offered as a substitute for that.

## Scope

- A new module `modules/web` with manifest v2, settings schema, lifecycle hooks and the two providers it
  needs; its actions declared in the manifest with `argument_schema`/`result_schema`, implemented as
  providers of the shared executor.
- A bounded, TTL'd cache shared by both actions, in-process, and the honest outcome classification of a
  network call (success / platform refusal / unknown), as the platform modules already do.
- The brain's observation framing for an untrusted result — a small change, only if the marker cannot be
  honoured without it.
- Tests at the product's standard: fakes for both providers, the SSRF guard's refusals (each address
  class), redirect handling, the byte and time bounds, the cache bounds, the redaction of a query from
  every trace, and the untrusted marker reaching the prompt.
- Documentation: a section in the operator guide and in the README's capability list, and a profile
  example that grants the two actions to `brain` (documented as revocable).

## Out of scope

- Any new runtime dependency: HTML becomes text with an in-module extractor, not a parser library. If
  the specification concludes that a library is unavoidable, it must say so explicitly and stop for the
  operator's decision rather than adding one silently.
- Summarisation by a model: the module returns text, the brain decides what to say about it.
- A browser, JavaScript execution, or anything that renders a page.
- Real-network trials in the test suite: every test uses a fake transport. The real-instance record
  (Wikipedia, and Brave when a key exists) is an operator trial, recorded separately, as in phase 5.
- Changing the brain's loop, the executor's authorization, or any phase-0 to phase-6 decision.

## What the phase must produce

A specification and a plan at the standard of phases 5 and 6, then code steps with their regressions,
then a closing gate as strict as the last one: a full-branch review by the reviewer model, its report
tracked in the repository, `APPROVE` required. The gate must show, for every requirement, the named test
that carries it, and for the security claims (SSRF, redaction, untrusted content) an executed
counterexample attempt that fails.

The phase summary must state what the companion can now do, what it still cannot, and the exact
operator steps to enable Brave — because a capability nobody can turn on is not a capability.
