---
spec: "phase8-declared-triggers"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Authority: `spec.md` (R1–R9, AC1–AC40, assumptions A1–A8, binding decisions 1–7), the brief
`docs/campaigns/phase8/brief.md`. Pre-phase tree for evidence: `7782e5f`. Review findings in
`findings.json` are an input to this revision (`findings-input: true`): finding P1 (AC33 must hold
for the *whole* suite, not only for the tests this phase adds) is addressed by P1's autouse isolation
guard, the shrinking `AC33_PENDING` ledger, the conversion steps P15a–P15e and P16's empty-ledger
check (D8); finding P2 (P8 and P9 both edit
`test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`) is addressed by P9
depending on P8.

Ground rules every step keeps:

- **Generic types decide exactly as before.** `TriggerEngine.evaluate` keeps its stage order
  (invariants → dedup window → resolved policy) and its existing rule evaluators. Declared rules are a
  new branch of `_evaluate_rule`, reached only for a rule whose type is a declared one; a policy made
  only of the four generic names never enters it. `tests/test_triggers.py` is never edited (AC10).
- **No new runtime dependency.** Only `aiohttp` and `PyYAML` (`pyproject.toml` `dependencies` stays
  byte-identical). The operator listener uses aiohttp's server; the press command uses
  `urllib.request`; the HTTP detector uses aiohttp's client; the wildcard matcher is hand-written.
- **No shipped `*.example` profile is edited** (R4). The memory of earlier phases is that editing one
  ripples into `test_integration`, `test_main` and `test_proxy_process` fixtures; this phase does not.
- **Socket-free and process-free tests** (R9, AC33). Every module ships seams (settings keys starting
  with `_`, as `modules/audio_input/__init__.py:245` `_SEAMS` and `modules/capture` do): the operator's
  `_server_factory`, the press command's `opener` argument, the audio input's existing
  `_subprocess_runner` plus a new `_detector_session` seam, and the twitch fake EventSub already in
  `tests/conftest.py`. AC33 covers the **whole suite**. An autouse isolation fixture in
  `tests/conftest.py` (P1) makes socket creation raise and process spawning raise for every test.
  It extends the shape `tests/test_phase5_trials.py:180-197` already uses. The pre-existing tests that
  open sockets or start processes today are converted to fakes in P15a–P15e. P16 proves that none
  remain (D8).
- **Startup diagnostics are value-free and name the field.** Settings diagnostics go through each
  module's `settings_validator` (the loader's existing path, run before any transport); a check that
  needs the *resolved* trigger policy (timer, R8) runs in `prepare`, which reports `module.degraded`
  with the field-naming reason before raising, because `PhaseCoordinator` strips exception text
  (`core/lifecycle.py`; precedent: phase-1 P9 clips).
- **Allowlist only.** The nine tests in the spec's "Test failure allowlist" are adapted (expected
  constants only, nothing loosened) in the step that makes them fail; any other pre-existing test
  that fails stops the step and is flagged, never adapted silently. The one exception is AC33's
  conversions (D8), and they are planned, not silent. P15a–P15e change *how* a pre-existing test
  reaches its subject: a fake process or an in-memory transport replaces the real one. They do not
  change what it asserts. Every `assert` of a converted test is kept, word for word or with only the
  fake's name substituted, and P17 checks this test by test.

### Design decisions taken by this plan (flagged, each justified by the spec)

- **D1 — Hooks are awaited through a new async entry point.** `TriggerEngine.evaluate` is
  synchronous and "awaits nothing" (`core/triggers.py:1-7`), but R3 needs a hook that "never returns"
  to time out on the engine's clock and the evaluation to complete anyway. The engine therefore gains
  `async def evaluate_async(...)` with the identical stage order, which awaits a hooked rule's hook
  raced against the engine's sleeper for `hook_timeout_seconds`. The synchronous `evaluate` is
  unchanged for every policy without a hooked rule; for a policy that contains one it does not call
  the hook and decides the fail-closed `rejected:<type>_hook_unavailable` (a hook is only reachable
  where it can be bounded). Only `audio_input` declares a hooked type and it calls `evaluate_async`.
- **D2 — A hook is a coroutine function.** `supply_hook` refuses a callable that is not
  `inspect.iscoroutinefunction`, so the timeout race is real for cooperative code. A hook that blocks
  the event loop synchronously inside its coroutine cannot be pre-empted in-process; the guide states
  it and the shipped `wake_word` hook is pure and constant-time.
- **D3 — The engine's sleeper.** `TriggerEngine.__init__` gains an optional `sleeper`; when omitted
  it uses the injected clock's `sleep` attribute if callable (ManualClock in tests) and otherwise
  `asyncio.sleep`. `core/main.py:1295` therefore needs no change (production clock is
  `time.monotonic`, so the real sleeper is `asyncio.sleep`).
- **D4 — Hook supply goes through the module's own context.** `ModuleContext` gains
  `supply_trigger_hook(type_name, hook)`, which calls `TriggerEngine.supply_hook(module_name,
  type_name, hook)`; the registry refuses unless `type_name` is a hooked type in *that module's own*
  registered spec (R3: "only the module that declared the type").
- **D5 — The wildcard matcher is segment-based and non-recursive.** The pattern is split on `*`; the
  anchored prefix segment and anchored suffix segment are checked first, then each middle segment is
  located leftmost in order (leftmost placement is optimal for `*`-separated segments, `?` included).
  Every pattern-position/text-position comparison is one step; characters are compared one by one with
  `str.casefold()` on each single character, so the text length — and the (len(text)+1)×(len(pattern)+1)
  bound — is never changed by folding.
- **D6 — `draws` reaches traces via one helper.** `core/triggers.py` gains
  `decision_trace_payload(decision)` returning the existing six-key trace mapping, plus `draws` (a
  list of the five-scalar mappings) **only when non-empty**, so every generic-only trace stays
  byte-identical (AC16). The three new inputs and twitch's reward path use it; the other inputs keep
  their own `_trace_decision` (their shipped manifests declare no probability type).
- **D7 — AC16 baseline is recorded before any change.** The phase-7 decisions, subscriptions and
  admissions of each shipped profile are captured at `7782e5f` into
  `tests/fixtures/phase8/profile_baseline.json` (P1). This fixture file and `tests/conftest.py` are
  outside the spec's target list; both are flagged in the step reports.
- **D8 — AC33 is enforced on the whole suite, and the spec's target list is widened to do it.**
  AC33 says "the full suite runs with network access blocked at the socket layer … and no test spawns
  a process outside a fake runner". A probe of the suite at `7782e5f` (an autouse fixture that logs
  each `AF_INET`/`AF_INET6` socket and `subprocess.Popen` call; 4402 passed) found **166 pre-existing
  test nodes in 24 files** that open a socket or start a process. The two largest groups are
  `test_automation_bridge.py` (78 nodes, a loopback `web.TCPSite` fake Streamer.bot) and `test_kick.py`
  (31 nodes, `aiohttp.test_utils.TestServer`). The others are child-process tests of the shipped
  runners, `git`/`grep` subprocesses in repository-fact tests, CLI and installation smoke tests, and
  "unreachable endpoint" tests that connect to `127.0.0.1:1`. The spec's target list and allowlist
  name none of these 24 files, yet AC33 cannot pass without converting them. The acceptance criterion
  wins. The conversions are limited to `tests/`: `conftest.py`, the 24 files, the new guard tests
  `tests/test_ac33_isolation.py` and the committed baseline fixtures P15a adds. No production module changes for this: every runner already has a
  seam. They are `modules/audio_input` `_subprocess_runner`, `modules/capture` `spawn`/
  `_subprocess_runner`, `modules/audio_output` `spawn`, `core/config_ui` `Supervisor(popen=...)` and
  `modules/kick` `_session_factory`. Where no seam exists, the test monkeypatches the *dependency*
  (`aiohttp.ClientSession`, `asyncio.create_subprocess_exec`), never the subject under test.
  Two readings are flagged. First, the guard lets **only** the event loop's own self-pipe through
  (`socket.socketpair` called from `asyncio`'s `_make_self_pipe`). Without it no `asyncio` loop can
  be created on Linux, and it is in-process IPC, not network access. Second, the "fake runner" of
  AC33 is any test double that stands in for `Popen`/`create_subprocess_exec` and starts nothing.
  What converting loses: real-OS evidence of process-group kills, signal delivery, a real loopback
  listener and the real two-process topology. `summary.md` (P16) states this loss per converted test.
  No real-process stage is added to CI, because that would be scope creep.

## Steps

### P1: Phase-7 baseline of the shipped profiles, and the whole-suite isolation guard (R4 AC16 baseline half; R9 AC33)
- **Files:** [`tests/fixtures/phase8/profile_baseline.json`, `tests/test_declared_triggers.py`, `tests/conftest.py`, `tests/test_ac33_isolation.py`]
- **Description:**
  - Before any production change (HEAD `7782e5f`), drive each shipped `*.example` profile
    (`config.yaml.example`, `config.server.yaml.example`, `agent.yaml.example`,
    `presence.yaml.example`) against the existing fakes in `tests/conftest.py` on one fixed event
    sequence (chat messages, each phase-7 notice kind, a `cue`, a `watch_tick`) and record per profile:
    every trigger decision as `(input, reason, policy_version)`, every EventSub/platform subscription
    the fakes saw, and every admission result. Write the record as sorted JSON to
    `tests/fixtures/phase8/profile_baseline.json`.
  - In `tests/test_declared_triggers.py`, add the comparison test
    `test_ac16_shipped_profiles_decide_as_in_phase7` that replays the same sequence and asserts 0
    differences, and `test_ac16_runtime_dependencies_are_exactly_aiohttp_and_pyyaml` (parses
    `pyproject.toml`).
  - In `tests/conftest.py` add a `RecordedRandom` helper. It serves a given list of values, counts
    `ordinal` and can be re-seeded. It is only added if `tests/test_triggers.py` does not already
    expose one that can be imported.
  - In `tests/conftest.py` add the **autouse** fixture `_ac33_isolation` (D8). It applies to every
    test of the suite and uses `monkeypatch`, so it is undone after each test. While it is active:
    - **Socket creation raises** `AssertionError(f"AC33: {nodeid} created a socket")`. This covers
      `socket.socket.__init__` for any family when no `fileno` is given, `socket.create_connection`,
      `socket.create_server`, `socket.fromfd`, `socket.getaddrinfo` and `socket.socketpair`. The
      only exception is a `socketpair` whose caller frame is
      `asyncio.selector_events.BaseSelectorEventLoop._make_self_pipe`, the event loop's own wakeup
      pair. Wrapping the fds of that permitted pair (`socket.socket(..., fileno=...)`) is allowed.
    - **Process spawning raises** in the same way. This covers `subprocess.Popen.__init__` (which
      `subprocess.run`/`check_output` and asyncio's `create_subprocess_exec`/`_shell` all reach),
      `os.system`, `os.popen`, `os.posix_spawn`, `os.posix_spawnp`, `os.fork`, `os.forkpty` and the
      `os.exec*` family.
    - **Transitional ledger.** A module-level `AC33_PENDING: frozenset[str]` holds the node ids
      (`file::test`, parameter suffix stripped) of the 166 pre-existing tests the D8 probe found. The
      probe's list is committed verbatim in this step. For a node in the ledger the fixture yields
      without patching. The ledger may only shrink: P15a–P15e each remove the entries they convert,
      and P16 asserts it is empty. No other test may be added to it.
    - **Operator trials stay possible.** The real-instance trial files `test_phase2_trials.py`,
      `test_phase3_trials.py` and `test_phase5_trials.py` skip every node unless an operator sets
      that node's opt-in variable. The default run sets none, which is why 20 nodes are skipped. The
      guard yields unpatched only for a trial node that *is* opted in, decided by each file's own gate
      (for example `opted_in(question)` at `tests/test_phase5_trials.py:177`). In the default run no
      trial is opted in, so every executed test runs under the guard. This is a permanent rule, not a
      ledger entry, and P16 proves it is inert in the default run.
  - Every test that P2–P15 add runs under the guard from the start, because none of them is in the
    ledger. The extended `test_twitch.py` and `test_audio_input.py` tests are covered as well. No
    per-file mark is needed.
- **Dependencies:** []
- **Tests:** The two AC16 tests pass at `7782e5f` and at every later step. The new
  `tests/test_ac33_isolation.py` holds the guard's own tests:
  - `socket.socket()`, `socket.socket(AF_UNIX)`, `socket.create_connection(("127.0.0.1", 1))` and a
    direct `socket.socketpair()` each raise.
  - `subprocess.run(["true"])`, `asyncio.run(asyncio.create_subprocess_exec("true"))` and
    `os.system("true")` each raise. Each call is made inside `pytest.raises`, so nothing starts.
  - `asyncio.run(asyncio.sleep(0))` succeeds, which proves the self-pipe exception works.
  - Every ledger entry names a collected test (no stale entries), read from
    `request.session.items`.
  - The ledger is a subset of the committed probe list, so it has not grown.
  - With no opt-in variable set, no trial node is exempt.
  The full suite passes with the guard on, apart from ledger nodes, which run as before.
- **Risks:** The ledger hides the 166 nodes until P15a–P15e convert them, so it is a real
  temporary exemption. That is why the ledger may only shrink, why its content is the probe's
  verbatim, and why P16 fails on any leftover. Matching the self-pipe by caller frame is
  fragile across Python versions. The self-test `asyncio.run(asyncio.sleep(0))` catches a mismatch
  at once, and the match uses the function's qualified name, not a line number. A guard that
  patches `subprocess.Popen.__init__` must not break pytest itself: pytest starts no process during a
  test, and the fixture is function-scoped, so collection and reporting are unaffected.
  A baseline taken after a production edit would hide a regression: the step commits the
  fixture before touching `core/`. Non-deterministic fields (timestamps, random message ids) must be
  excluded from the record or the comparison is flaky; the record keeps reasons, versions and ids
  derived from the fixed sequence only.

### P2: The vocabulary grows by appending; routes can name the new kinds (R4; AC15, AC16 route half; allowlisted vocabulary tests)
- **Files:** [`core/contracts.py`, `modules/brain/module.yaml`, `tests/test_contracts.py`, `tests/test_input_ownership.py`, `tests/test_declared_triggers.py`]
- **Description:** Append `"reward", "manual", "speech", "timer"` to `EVENT_KINDS`
  (`core/contracts.py:51`) after `cue`, indices 10–13; leave `PLATFORM_NOTICE_KINDS` (`:76`) and
  `CHAT_FAMILY_KINDS` (`:99`) untouched so `input_family_of_event` (`:1410`) returns `None` for the
  new kinds (A1). The canonical `event_kind` schema in `core/triggers.py` already reads
  `list(EVENT_KINDS)`, so it widens with no edit. Append the four kinds to the brain route
  `match.kinds` enum in `modules/brain/module.yaml` (around line 320). The shipped `kick`, `youtube`,
  `streamerbot`, `watch` manifests are not edited (their narrowed enums stay).
- **Dependencies:** [P1]
- **Tests:** AC15 in `test_declared_triggers.py` (first 10 equal the phase-7 tuple literal, last 4,
  length 14, 7 notice kinds, `input_family_of_event` is `None` for a `channel.chat.message` of each
  new kind); AC16 route half: a brain route `match.kinds: [manual]` validates at startup. Allowlisted
  adaptations: `test_contracts.py::test_event_kind_vocabulary_is_the_nine_names_in_order` (14 kinds,
  first 10 unchanged, last `timer`) and
  `test_input_ownership.py::test_cue_is_the_tenth_kind_and_not_a_platform_notice` (`cue` at index 9,
  last kind `timer`). P1's AC16 comparison still passes.
- **Risks:** Any code that indexes `EVENT_KINDS[-1]` or treats "not a notice and not message" as
  `cue`/`watch_tick` would change behaviour; grep `EVENT_KINDS` usages in `core/` and `modules/`
  before editing. The brain's route-safety classification counting the new kinds as viewer-reachable
  is the intended conservative reading (A1), not a bug.

### P3: Declared-type contract shapes and their structural validation (R1; AC2 structural rows, AC8 pattern length)
- **Files:** [`core/contracts.py`, `tests/test_declared_triggers.py`]
- **Description:**
  - `TriggerTypeDeclaration` (`core/contracts.py:1908`) gains optional fields, all defaulting to the
    generic shape so the four `BUILTIN_TRIGGER_TYPES` construct unchanged: `kinds: tuple[str, ...] =
    ()`, `match: Mapping | None = None`, `probability_parameter: str | None = None`, `hook: bool =
    False`, `hook_timeout_seconds: float | None = None`, and a derived `declared` property (true when
    any of `kinds`/`match`/`hook` is set). `__post_init__` validates a declared type: name matches
    `[a-z][a-z0-9_]*`, ≤ 32 characters, not one of the four generic names; `kinds` non-empty and each
    in `EVENT_KINDS`; exactly one of `match` / `hook: true`; `hook_timeout_seconds` in (0, 10] iff
    `hook`; the match tree is a closed node set `all`, `any`, `not`, `equals`, `in`, `gte`, `lte`,
    `matches` with operand leaves `{field}`, `{param}` or a literal (string, number, boolean, list ≤
    64 scalars), depth ≤ 8, ≤ 64 nodes; a `matches` literal pattern ≤ 128 characters; every `param`
    path resolves through declared `properties` (object segments only); a non-required parameter (or
    one under a non-required object) is refused under `not`; `probability_parameter` names a
    top-level **required** property whose schema is a number bounded within [0, 1]. Each
    `ContractError` names the sub-path (`kinds[0]`, `match.all[1].in[0].field`, `name`,
    `probability_parameter`, `hook_timeout_seconds`) and echoes no value.
  - `TriggerSpec` (`:2016`) gains `fields: Mapping[str, str] = {}` (dotted payload path → scalar type
    `string`/`integer`/`number`/`boolean`) and checks every declared type's `field` operands against
    the canonical normalized fields (`kind`, `text`, `platform`, `channel_id`, `message_id`,
    `author.id`, `author.display_name`) plus `fields`. The match tree is frozen (`_frozen_mapping`) so
    it cannot be mutated after load.
  - These are new optional constructor fields; see the caller table.
- **Dependencies:** [P2]
- **Tests:** Construction-level versions of the nine AC2 rows (name `keyword`, 33-char name, empty
  kinds, kind `banana`, both match and hook, operator `regex`, field `secret.token`, unknown param,
  depth 9), a 65-element literal list, 65 nodes, a 141-character `matches` pattern (AC8), a
  `{param: window.nope}` path (AC30's refusal), a non-required probability parameter, a
  `hook_timeout_seconds` of 0 and 10.5 (AC12); each error names the sub-path and contains no
  configured string. The AC1 `badge_tier` declaration constructs.
- **Risks:** A default added in the wrong place could make a generic declaration count as declared
  and route it to the new evaluator; the `declared` flag is computed from the three discriminating
  fields only. Depth/node counting on a malicious tree must itself be bounded (iterative walk with an
  early stop at 65 nodes).

### P4: The loader parses and diagnoses the new manifest keys (R1; AC1, AC2, AC3 load half)
- **Files:** [`core/loader.py`, `tests/test_declared_triggers.py`, `tests/fixtures/modules/declared/badge/module.yaml`, `tests/fixtures/modules/declared/badge/__init__.py`]
- **Description:** `_TRIGGER_KEYS` (`core/loader.py:225`) gains `fields`; `_TRIGGER_TYPE_KEYS`
  (`:226`) gains `kinds`, `match`, `probability_parameter`, `hook`, `hook_timeout_seconds`.
  `_manifest_triggers` (`:2107`) passes them to `TriggerTypeDeclaration` and `fields` to
  `TriggerSpec`. For a declared type, every `ContractError` is re-raised as a load diagnostic whose
  field is `triggers.<input>.types[i].<sub-path>` (the input = the manifest name), through the
  existing `_field_error`/`_sanitised_reason` path so no configured value is echoed; a non-generic
  name with neither `match` nor `hook` is refused at `triggers.<input>.types[i].name`. Generic-type
  diagnostics keep their existing `triggers.types[i]` wording (pre-existing loader tests assert it).
  Load happens before any transport opens, so a refused declaration opens 0 transports. The fixture
  module `tests/fixtures/modules/declared/badge` (a minimal input that opens nothing) carries the AC1
  declaration; refusal fixtures are built in-test by writing manifests to `tmp_path`.
- **Dependencies:** [P3]
- **Tests:** AC1 (the `badge_tier` manifest with `triggers.fields: {badge.tier: string}` loads);
  AC2 (the nine fixture declarations, each refused with 0 transports opened — asserted with a
  transport-counting fake — and a diagnostic containing `triggers.<input>.types[i]` and the
  sub-path); AC3 load half (neither match nor hook → `triggers.<input>.types[0].name`;
  non-required probability parameter → `triggers.<input>.types[0].probability_parameter`; no
  diagnostic contains the configured value). `tests/test_loader.py` passes unmodified.
- **Risks:** Two diagnostic prefixes (`triggers.types[i]` for generic, `triggers.<input>.types[i]`
  for declared) is a deliberate split to keep pre-existing loader tests exact; documented in the
  guide's diagnostics section. A fixture module directory under `tests/fixtures/modules` must not be
  discovered by the shipped-manifest counts (they scan `modules/` only; verify).

### P5: The registry accepts declared types and validates their policies (R1, R2 scoping; AC1 policy, AC3 policy half, AC6)
- **Files:** [`core/triggers.py`, `tests/test_declared_triggers.py`]
- **Description:** `TriggerRegistry.register` (`core/triggers.py:469`) stops refusing every
  non-generic name (`:497`): a declaration with `declared` true is accepted; a non-generic name that
  is not declared keeps the existing "is not a trigger type this runtime can evaluate" refusal.
  `validate_policy` (`:528`) / `_validate_executable` (`:593`) validate a declared rule's parameters
  against the declared `parameter_schema` (diagnostic `triggers.<input>.policy.rules[i].parameters.<key>`,
  value-free) and against the kernel checks (probability value in [0, 1] when the parameter is the
  probability parameter); `_validate_builtin_rule` (`:1288`) is untouched for generic rules. A
  per-call override naming a declared type for an input that did not declare it raises
  `ContractError` naming that input, before any rule runs (existing override path, `:840`). The
  registry also keeps, per input, a hook slot table for its hooked types (empty until P7).
- **Dependencies:** [P4]
- **Tests:** AC1 (`badge_tier{tiers: ["gold"]}` validates); AC3 policy half (a parameter outside the
  declared schema is refused naming `triggers.<input>.policy.rules[0].parameters.<key>`, no value
  echoed); AC6 (an override naming `badge_tier` on an event from a second fixture input raises
  `ContractError` naming that input; `rule_evaluations` delta 0). `tests/test_triggers.py`
  unmodified passes (AC10).
- **Risks:** Accepting declared names must not accept a *generic* name with extra declared keys
  (refused in P3). Policy resolution ("configured replaces default entirely") is untouched; a test
  asserts a configured declared policy replaces the module default with no merge.

### P6: Declared-rule evaluation, the wildcard matcher, probability draws and `draws` (R2; AC4, AC5, AC7, AC8, AC10, AC35, AC36)
- **Files:** [`core/triggers.py`, `tests/test_declared_triggers.py`]
- **Description:**
  - `_evaluate_rule` (`core/triggers.py:1155`) dispatches a declared type to a new
    `_evaluate_declared(declaration, parameters, normalized, rng, counter)` which: returns
    `<type>_kind_mismatch` when the kind is outside `kinds`; evaluates the frozen tree iteratively over
    the normalized payload — scalar comparisons false on an absent field or a JSON-type mismatch
    (`bool` is never a number), `in` false when the left value is absent or not a scalar, else true iff
    the list holds a same-type equal element (strings case-sensitive), a comparison whose `param`
    operand is absent is true, `equals` case-sensitive, `matches` per D5; then, when a
    `probability_parameter` is declared, draws exactly one value whether or not the tree matched
    (`<type>_probability_miss` when tree matched and draw ≥ probability). Reasons are `<type>_match`,
    `<type>_no_match`, `<type>_kind_mismatch`, `<type>_probability_miss`, bounded by
    `_bounded_reason`. The normalized view (`_Normalized`, `:612`) exposes the payload read-only for
    `field` paths; no module code runs, no transport opens.
  - `_wildcard_match(pattern, text) -> (bool, steps)` per D5; the engine adds `steps` to
    `counts["wildcard_steps"]`.
  - `TriggerDecision` (`:366`) gains `draws: tuple[Mapping[str, Any], ...] = ()` — its only shape
    change — validated in `__post_init__` to entries of exactly the five keys `rule`, `parameter`,
    `probability`, `draw`, `ordinal` with scalar values. The engine counts values served by its random
    source (`ordinal` = count before the draw, per engine; an `rng` override counts from its own
    zero) and appends one entry per evaluated declared probability rule, in policy order. The
    dedup window stores the decision object, so a redelivery returns the same `draws` and draws
    nothing (`:858`). The generic `probability` evaluator (`:1192`) records nothing, so generic
    decisions keep `draws == ()`.
  - `decision_trace_payload(decision)` per D6.
- **Dependencies:** [P5]
- **Tests:** AC4 (gold/silver/absent/`7`, kind `cue`, two-tier list, `Gold`); AC5 (self-authored,
  internal trace, non-schema-2 rejected by their existing reasons with `rule_evaluations` delta 0; a
  redelivery: 0 evaluations, 0 draws); AC7 (100 events, 50/50, exactly 100 draws; replay identical;
  `probability_miss`); AC8 (`hi*bot?` matches `Hi there BOTs`, not `hi bot`; 128 `*` over 10 000
  characters decides with no timeout path); AC35 (values `0.9, 0.1, 0.42`, decisions and the third
  `draws` literal; re-seeded replay equal; redelivery same `draws` 0 draws; generic-only incl.
  `probability` → `()`; no entry carries payload/text/other parameter); AC36 (the 63×`*a`+`*b`
  pattern over 5 000 and 10 000 `a`: no match, steps ≤ (n+1)×129, steps(10 000) ≤ 2×steps(5 000)+129,
  `counts["wildcard_steps"]` delta equals reported steps); a unit table for the matcher (empty
  pattern, `*` only, `?` at ends, Unicode case folding of `ß`/`İ` keeps the step bound). AC10:
  `tests/test_triggers.py` unmodified passes.
- **Risks:** Short-circuiting tree evaluation is allowed (only the *draw* must be unconditional);
  the draw must be taken after the tree and before the combination to keep "one draw per evaluation".
  Treating Python `True` as integer 1 in `gte`/`equals` would violate the JSON-type rule — explicit
  `type(x) is bool` checks. The greedy-with-backtrack wildcard algorithm is O(n·m) and would pass the
  bound but not AC36's linear growth if implemented recursively; D5 avoids both.

### P7: Hooked types — async evaluation, bounded hook, hard veto, supply through the module context; admission untouched (R3, R2 admission; AC9, AC11, AC12, AC13, AC14, AC34)
- **Files:** [`core/triggers.py`, `core/runtime.py`, `tests/test_declared_triggers.py`]
- **Description:**
  - `TriggerEngine.__init__` (`core/triggers.py:680`) gains `sleeper` per D3.
    `TriggerEngine.supply_hook(module, type_name, hook)` and the registry's slot table per D2/D4:
    refused (`ContractError`, value-free) unless `type_name` is a hooked type in `module`'s own spec
    and `hook` is a coroutine function.
  - `async def evaluate_async(event, *, context, policy, clock, rng)` per D1: shares the invariant,
    override-validation, dedup and resolution code with `evaluate` (refactored into private helpers,
    no behaviour change); for each rule in policy order a hooked rule is evaluated only when the
    event's `metadata.source` is the declaring input and its kind is in `kinds` (else
    `kind_mismatch`/not eligible, 0 invocations); the hook receives a read-only
    (`MappingProxyType`, deep-frozen) view of the event and the rule's parameters, invoked at most
    once, raced against `sleeper(hook_timeout_seconds)`; outcomes `True` → match, `False` → no match,
    any other value or exception → `<type>_hook_error`, timeout → `<type>_hook_timeout` (the hook
    task is cancelled and its eventual result ignored), no hook → `<type>_hook_unavailable`. After all
    rules are evaluated and **before `_combine`** (`:1268`), the first failed hook outcome in policy
    order rejects the decision with its reason, whatever the combination or whether the policy is the
    default (R3 veto). The decision is recorded in the dedup window and counted like any other; a
    hook failure never raises out of `evaluate_async`.
  - `core/runtime.py`: `_OwnedTriggers` (`:1434`) forwards `evaluate_async` through the same
    ownership guard as `evaluate` and forwards `supply_hook` bound to its module; `ModuleContext`
    (`:1664`) gains `supply_trigger_hook(type_name, hook)` calling the engine with the context's own
    module name (D4). No admission, budget or authorization code is touched.
- **Dependencies:** [P6]
- **Tests:** AC11 (`True`, `False`, `1`, `"yes"`, `None`, raise → match, no_match, 4× hook_error;
  each counted and retained in the window); AC12 (0.5 s bound, never-returning hook →
  `hook_timeout` after `ManualClock.advance(0.5)`, a following event evaluated normally; 0 and 10.5
  refused at load — reuses P3/P4); AC13 (0 invocations for an invariant-rejected event, a dedup
  redelivery, an out-of-kinds event, another input's event; 1 for an eligible one); AC14 (a second
  fixture module supplying the first module's hooked type is refused; that type then decides
  `hook_unavailable`, same with no hook); AC34 (`none_of` with raise/timeout/no hook;
  `any_of[keyword{hi}, <t>]` with text `hi` and a raising hook → `hook_error`, same as default policy;
  0 admissions); AC9 (an accepted declared decision with a full fixture `AdmissionScheduler` is not
  admitted with the scheduler's own refusal; with a granting-rule-free configuration the run is
  offered 0 writes, read from the existing authorization view); synchronous `evaluate` on a hooked
  policy decides `hook_unavailable` without calling the hook (D1).
- **Risks:** Cancelling a timed-out hook task must not leak an "exception was never retrieved"
  warning — the task's result is consumed in a done-callback. Refactoring `evaluate` into shared
  helpers risks changing generic behaviour; AC10 and P1's AC16 comparison run in this step. Ordering
  on ManualClock: the hook race uses the same sleeper the test advances, so an advance of exactly the
  bound must fire the timeout (no epsilon).

### P8: Channel points on Twitch (R5; AC17, AC18, AC19, AC20; allowlisted twitch type list)
- **Files:** [`modules/twitch/module.yaml`, `modules/twitch/__init__.py`, `tests/test_twitch.py`, `tests/test_examples.py`]
- **Description:**
  - Manifest: `notices.kinds` enum and the generic `event_kind` enum (`modules/twitch/module.yaml:172`)
    gain `reward`; `triggers.fields` declares `reward.id` (string), `reward.title` (string),
    `reward.cost` (integer); `triggers.types` gains the declared type `reward` (kinds `[reward]`,
    schema: required `reward_ids` array of strings, optional `min_cost`/`max_cost` integers ≥ 0,
    rule `{all: [{in: [{field: reward.id}, {param: reward_ids}]}, {gte: [{field: reward.cost},
    {param: min_cost}]}, {lte: [{field: reward.cost}, {param: max_cost}]}]}` — absent bounds are true
    per R2). Default policy unchanged.
  - Code: `_NOTICE_KINDS`/validator (`modules/twitch/__init__.py:275`, `_validate_notices` `:395`)
    accept `reward`; activation adds one subscription `channel.channel_points_custom_reward_redemption.add`
    version `1`, condition `{broadcaster_user_id: <configured broadcaster>}` iff `reward` is listed;
    a refused/unanswered one is one value-free `module.degraded` naming capability `reward`, chat
    continues (same shape as the follow refusal, `:84`). A notification is normalized into a
    schema-2 `reward` event (`message_id` = redemption id, author = user id/display name, text =
    trimmed `user_input` or the reward title, `payload.reward = {id, title, cost}`); missing
    redemption id / reward id / integer cost (bool is not an integer), or a user id containing `:`,
    publishes nothing and increments `counts["invalid"]`. Decision and admission go through the
    existing twitch ingestion path (`:2426`); its trace uses `decision_trace_payload`.
- **Dependencies:** [P7]
- **Tests:** AC17 (unset → 0 reward subscriptions; `[reward]` → exactly 1 with type, version,
  condition); AC18 (refused → exactly 1 `module.degraded` naming `reward`, chat still published, no
  token/credential in payload); AC19 (`"  hello  "` → `hello`; empty → `Hydrate`; same id twice → 1
  decision; 3 malformed + `a:b` → 0 events, `counts["invalid"]` +4); AC20 (the four
  `reward{r1,100,1000}` rows; `reward{[r1]}` at cost 0 and 100 000). Allowlisted
  `test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` twitch type list
  becomes 5 names. Existing `test_twitch.py` tests pass unmodified; P1's AC16 comparison passes (no
  shipped profile lists `reward`).
- **Risks:** Adding a subscription unconditionally would change every profile's subscriptions
  (AC16) — gated strictly on `reward` in `notices.kinds`. A redemption must not enter the chat-notice
  path (it is not a platform notice, A1). Fulfil/refund is out of scope; no Helix write is added.

### P9: The `operator` input — manifest, settings, request core, page and overlay, lifecycle (R6; AC21 page/overlay, AC22, AC23, AC24; allowlisted counts 19→20)
- **Files:** [`modules/operator/module.yaml`, `modules/operator/__init__.py`, `pyproject.toml`, `tests/test_operator.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`, `tests/test_config_ui.py`]
- **Description:**
  - Manifest v2 (modelled on `modules/watch/module.yaml`): `produces: [channel.chat.message]`,
    `lifecycle.roles: [input]`, `credentials: [token]`, `settings_validator: validate_settings`;
    settings `buttons` (list of `{id, label, prompt_text, cooldown_seconds (0–3600, default 2),
    rect?: {x, y, w, h} percent}`), `destination {platform, channel_id}`, `surfaces {page, hotkey,
    overlay}` (each default `false`), `listen {host (default 127.0.0.1), port, allow_non_loopback
    (default false)}`, `token` (`${...}` credential), `overlay {image?}`; `triggers.fields:
    {button.id: string, button.surface: string}`; types: declared `button` (kinds `[manual]`,
    required `button_ids` array, rule `{in: [{field: button.id}, {param: button_ids}]}`) and generic
    `event_kind` narrowed to `[manual]`; default policy `event_kind{kinds: [manual]}`.
  - `validate_settings`: no surface enabled, token empty or < 32 characters, a button without
    `prompt_text`, a non-loopback host without `allow_non_loopback` → diagnostics naming the field,
    no value echoed; run by the loader before any listener.
  - Request core `OperatorCore.handle(method, path, headers, body) -> (status, body)`, socket-free:
    constant-time token compare (`hmac.compare_digest`), accepted `Host` set (the listen address),
    on press an `Origin` other than the page's own (when sent) refused; unknown button, surface
    disabled, cooldown (per button, on the injected clock, last *accepted* press) refused; every
    refusal increments a named count (`refused_token`, `refused_host`, `refused_origin`,
    `refused_unknown_button`, `refused_cooldown`, `refused_surface`), none carries a header value. An
    accepted press builds one schema-2 `manual` event (`payload.button = {id, surface}`, text =
    `prompt_text`, author `operator:<id>` with the label, a fresh `message_id`
    `operator:<id>:<uuid4 hex>`, destination), decides it with `context.triggers.evaluate` and admits
    an accepted one through the scheduler resolved like watch's (`("admission", "runs")` service
    fallback), trace via `decision_trace_payload`. GET `/` lists buttons, GET `/overlay` renders the
    image with absolutely positioned buttons from `rect`, POST `/press` is the press endpoint (used by
    page, overlay and the A8 command); all three take the token as a query/header parameter and the
    pages post it back without logging.
  - Lifecycle: `prepare` validates and binds nothing; `start_inputs` opens the aiohttp listener
    through the `_server_factory` seam (default: `aiohttp.web.AppRunner` + `TCPSite`, access log
    disabled); `stop_inputs` closes it and the core refuses every later press. No module-level
    import of `core.runtime`, `core.loader`, `core.lifecycle` (AC40's last clause).
  - `pyproject.toml`: add `"modules.operator" = ["module.yaml"]` (spec target; phase-1 precedent).
- **Dependencies:** [P8]. P8 edits the same allowlisted test,
  `test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` (its twitch type list
  becomes 5). P9 then edits the module-name set and the count of that test on top of P8's edit. The
  explicit dependency makes this order binding rather than a convention. P8 itself depends on P7, so
  the kernel is still finished first.
- **Tests:** `tests/test_operator.py` with a fake `_server_factory` counting opened listeners and
  handing requests to the core: AC21 (page and overlay halves: 0 listeners before `start_inputs`, a
  press from each produces a `manual` event with the stated fields and is admitted through a fake
  scheduler; after `stop_inputs` the listener is closed and presses give 0 events); AC22 (no token,
  wrong token, wrong Host, foreign Origin, unknown button, second press 1 s later → 0 events and a
  refusal count each; 2 s later accepted; no token or header value in traces, diagnostics, or a
  captured `logging` record); AC23 (five diagnostics naming the field, 0 listeners; a profile without
  `operator` opens 0); AC24 (default policy accepts `manual`; `button{[hype]}` accepts `hype`,
  rejects `other` with `rejected:button_no_match`; no `streamerbot` enabled). Allowlisted count
  adaptations 19→20 in `test_examples.py` (module-name set + count), `test_manifest_presentation.py`,
  `test_profiles.py` (both tests), `test_config_ui.py` (`test_ac15_...`); each changes only the
  constant and adds `operator` to the expected set.
- **Risks:** The token in a page URL could leak via `Referer` — pages set `Referrer-Policy:
  no-referrer`. aiohttp's default access log would log the query string (token) — disabled
  explicitly and asserted (AC22). The scheduler resolution must reuse watch's service pattern
  (memory: `core/main.py` builds `scheduler=None`, brain publishes `("admission", "runs")`), or
  presses are dropped in the assembled runtime. Counts change in two steps (20 here, 21 in P11); the
  allowlist names 19→21 and the intermediate value is the same adaptation, flagged in the report.

### P10: The press command `python -m modules.operator press` (R6, A8; AC40, AC21 hotkey third)
- **Files:** [`modules/operator/__main__.py`, `tests/test_operator.py`]
- **Description:** `main(argv=None, *, opener=None) -> int`: argparse `press <button> [--profile
  PATH]` (default `config.yaml`); loads the profile with `yaml.safe_load`, reads
  `modules.operator.settings.listen` and the `token` reference, resolves only a `${NAME}` token from
  `os.environ` (no other interpolation, no loader); sends one POST to
  `http://<host>:<port>/press` with JSON `{button, surface: "hotkey"}` and the token header via
  `urllib.request` (`opener` defaults to `urllib.request.build_opener().open`, timeout 5 s); exit 0
  when the input answered accepted, 2 when it answered a refusal, 3 on `URLError`/`OSError`/timeout,
  64 on a usage or profile error (missing button, unreadable/invalid YAML, missing listen/token).
  Messages to stdout/stderr are fixed strings and never contain the token. The module imports only
  the standard library and `yaml`; importing it does not import `core.runtime`, `core.loader`,
  `core.lifecycle` (and `modules/operator/__init__.py` imports nothing from them at module level).
- **Dependencies:** [P9]
- **Tests:** AC40 (fake opener: exactly 1 POST to the configured address's `/press` naming `hype`,
  surface `hotkey`, the token; 0 on accepted; 2 for each core refusal — unknown button, cooldown,
  wrong token, `hotkey` disabled — with 0 events, the fake opener routing to P9's request core; 3 on
  connection error and on timeout; 64 with no button and with an unreadable profile; captured
  stdout/stderr never contain the token; the `sys.modules` purge-and-import check leaves the three
  core modules absent); AC21 hotkey third (a press via `main([...], opener=<core-backed fake>)`
  produces the third `manual` event with surface `hotkey`, completing AC21 with P9's two).
- **Risks:** `python -m modules.operator` executes `modules/operator/__init__.py` first, so a
  module-level core import there would break A8 — guarded by the AC40 import test. A fake opener
  that does not exercise the real URL/headers would be pass-by-mock; the test asserts on the
  `Request` object the command built (URL, method, body, header names) and routes that to the real
  core.

### P11: The `timer` input (R8; AC29, AC30, AC31; allowlisted counts 20→21)
- **Files:** [`modules/timer/module.yaml`, `modules/timer/__init__.py`, `pyproject.toml`, `tests/test_timer.py`, `tests/test_examples.py`, `tests/test_manifest_presentation.py`, `tests/test_profiles.py`, `tests/test_config_ui.py`]
- **Description:**
  - Manifest v2: `produces: [channel.chat.message]`, `lifecycle.roles: [input]`,
    `settings_validator: validate_settings`; settings `channels` (list of `{destination {platform,
    channel_id}, prompt_text}` non-empty text); `triggers.fields: {timer.minute_of_day: integer,
    timer.sequence: integer}`; declared type `interval` (kinds `[timer]`, schema: required
    `every_minutes` integer 1–1440, required `probability` number 0–1, optional `window`
    `{start_minute, end_minute}` integers 0–1439 both required inside; `probability_parameter:
    probability`; rule `{all: [{gte: [{field: timer.minute_of_day}, {param: window.start_minute}]},
    {lte: [{field: timer.minute_of_day}, {param: window.end_minute}]}]}` — no `not`, so the optional
    window is allowed); default policy `interval{every_minutes: 30, probability: 0.25}`.
  - `validate_settings`: channel fields; window start < end is a policy check, so `prepare` checks
    each configured channel's **resolved** policy (`context.triggers.registry.resolve(input,
    channel_id)`): exactly one `interval` rule, `start_minute < end_minute`; a violation reports
    `module.degraded` with a value-free reason naming `triggers.timer.policy...` / the channel index,
    then raises (coordinator precedent). Out-of-range `every_minutes`/`probability` are refused by
    P5's schema validation at load.
  - Loop, modelled on `modules/watch/__init__.py` `_emit_tick`/`_admit` (`:690`): started in
    `start_inputs`, one supervised task per channel sleeping `every_minutes × 60` on the injected
    clock's sleeper; each firing builds the schema-2 `timer` event (text = `prompt_text`,
    `payload.timer = {sequence, minute_of_day}` with `minute_of_day` from an injected local-time
    seam `_local_minute` defaulting to `time.localtime`, author `timer:<channel_id>`, `message_id`
    `timer:<platform>:<channel_id>:<sequence>`, destination), decides it, traces with
    `decision_trace_payload`, admits an accepted one through the watch-style scheduler resolution.
    `stop_inputs` cancels the tasks; nothing fires outside the span.
  - `pyproject.toml`: add `"modules.timer" = ["module.yaml"]`.
- **Dependencies:** [P10]
- **Tests:** `tests/test_timer.py` on `ManualClock` and the recorded random source: AC29 (2
  channels, `every_minutes: 10, probability: 1`, 60 min → 12 events, sequences 1–6 per channel, ids,
  texts, all accepted); AC30 (100 events with 0.25 → 100 draws, accepted = draws < 0.25; window rows
  599/600/660/661; no window at 0 and 1439; the shipped declaration registers with no hook; a
  `{param: window.nope}` fixture refused at load naming the path); AC31 (`every_minutes` 0 and 1441,
  `probability` 1.5, window 700>600, 0 and 2 `interval` rules → diagnostics naming input and field;
  60 min after `prepare` before `start_inputs` → 0 events; 60 min after `stop_inputs` → 0).
  Allowlisted counts 20→21 and `timer` added to the expected module set.
- **Risks:** Drift: computing the next firing as "now + interval" after a slow admission accumulates
  delay; the loop schedules from `start + k × interval`. Host local time is an injected seam so
  tests never depend on the machine's timezone. `prepare`-time policy checks depend on trigger
  configuration having been applied before `prepare` (loader `_apply_trigger_configuration` runs
  after registration at load — verify ordering against `core/loader.py:1375` before relying on it).

### P12: `audio_input` becomes an input — manifest, settings, the `wake_word` hook (R7 manifest/settings/hook; AC25, AC27 hook rows, AC26/AC27 diagnostics; allowlisted manifest tests)
- **Files:** [`modules/audio_input/module.yaml`, `modules/audio_input/__init__.py`, `tests/test_audio_input.py`, `tests/test_examples.py`]
- **Description:**
  - Manifest: `produces: [channel.chat.message]`, `lifecycle.roles: [input]`, settings `listen`
    (`enabled` default false, `source`, `pre_roll_seconds` 0–5 default 2, `speaker` default
    `streamer`, `destination`, `retry_seconds` 1–300 default 5, `max_seconds` integer default 10)
    and `wake_word.detector` (`{kind: command, argv}` | `{kind: http, endpoint, api_key?}`, `api_key`
    a credential); `triggers.fields: {wake_word.detected: boolean, wake_word.phrase: string}`; types
    generic `keyword`, generic `event_kind` narrowed to `[speech]`, hooked `wake_word` (kinds
    `[speech]`, empty schema, `hook: true`, `hook_timeout_seconds: 2`); default policy
    `wake_word{}`. `audio.capture` declaration untouched.
  - `validate_settings` (`modules/audio_input/__init__.py:295`) gains, only when `listen.enabled` is
    true: `wake_word.detector` required (diagnostic names `wake_word.detector`), `listen.source`
    names a configured `command` source, `listen.max_seconds` integer in 1..module `max_seconds`,
    `listen.pre_roll_seconds` in 0–5 and `< listen.max_seconds`, `destination` valid; diagnostics name
    the key, echo no value. With `listen` absent/disabled nothing new is checked.
  - `async def _wake_word_hook(view, parameters)`: `True` iff `payload.wake_word.detected is True`,
    `False` iff it is `False`, raises otherwise; reads nothing else. `prepare` (`:1118`) supplies it
    via `context.supply_trigger_hook("wake_word", ...)` when listening is enabled and registers the
    listen loop's supervised-task owner; it starts no process.
- **Dependencies:** [P11]
- **Tests:** AC25 (listen absent / `enabled: false`: 0 detector or source invocations over 60 s of
  `ManualClock`; existing `audio.capture` tests pass unmodified); AC27 hook rows (a candidate with
  `{detected: false}` → `rejected:wake_word_no_match`, without `wake_word` →
  `rejected:wake_word_hook_error`, a never-returning replacement hook → `hook_timeout` after 2 s, no
  hook → `hook_unavailable` and 0 runs admitted; no `wake_word.detector` → diagnostic naming it, 0
  processes); AC26 diagnostic rows (`max_seconds: 0`, above module max, `pre_roll_seconds: 6`,
  `pre_roll ≥ max` → diagnostics naming the key, 0 processes). Allowlisted:
  `test_audio_input.py::test_manifest_declares_an_on_demand_read_with_no_role` (produces, role,
  settings keys; `audio.capture` assertions stay exact) and the `audio_input` declaration keys in
  `test_examples.py`.
- **Risks:** Taking the `input` role makes the coordinator call `start_inputs`/`stop_inputs` on the
  module; with listening disabled both must be no-ops so phase-7 behaviour (AC25, AC16) is unchanged.
  `produces: [channel.chat.message]` must not trigger the ownership guard: `speech` belongs to no
  family (P2), verified by a test publishing a `speech` event through the module's owned bus.

### P13: The listen pipeline — verdict, candidate, acquisition, transcription, event (R7 order, detectors, A5; AC26, AC27 detector rows, AC28)
- **Files:** [`modules/audio_input/__init__.py`, `tests/test_audio_input.py`]
- **Description:**
  - Detectors behind one interface `await detector.wait() -> Verdict | None` (raises
    `DetectorFailure`): `CommandDetector` runs `argv` through the existing `_subprocess_runner` seam
    with stdin closed/empty and **no deadline**; exit 0 with trimmed stdout ≤ 64 chars = detected,
    exit 1 = no verdict, anything else (incl. signal, > 64 chars, start failure) = failure.
    `HttpDetector` sends one request (no audio, optional `api_key` header) through a new
    `_detector_session` seam with no request timeout; only `{"detected": true, "phrase": str≤64}`
    or `{"detected": false}` are verdicts, everything else (status ≠ 200, bad JSON, refused
    connection) is a failure; exceptions are mapped to fixed codes, never stringified.
  - One iteration, strictly in order: (1) await the detector; (2) on detected, build the candidate
    (schema-2 `speech`, `metadata.source` = this input, `payload.wake_word = {detected: true,
    phrase}`, text = phrase, author `voice:<speaker>`, `message_id` `voice:<speaker>:<uuid4 hex>`,
    destination) and `await context.triggers.evaluate_async(candidate)`; trace via
    `decision_trace_payload`; the candidate is never published; (3) only if accepted, invoke the
    named source once via the existing `CommandSource`/`CommandRecording` path
    (`modules/audio_input/__init__.py:723`) with `AUDIO_INPUT_PRE_ROLL_SECONDS` and
    `AUDIO_INPUT_MAX_SECONDS` in its environment; validate the WAV with `wav_data_layout` (`:561`):
    duration by header ≤ `listen.max_seconds` and size ≤ `max_bytes`, else a counted source failure
    (no trim/pad); transcribe once through the existing transcription settings (`_Transcription`,
    `:486`); (4) on a non-empty transcription publish one schema-2 `speech` event (text =
    transcription, no `payload.wake_word`, candidate's author/`message_id`/destination/source) and
    admit it through the watch-style scheduler resolution **without** calling the engine. The WAV
    bytes are held in a local variable and released after transcription; nothing is written to the
    attachment store or disk. Counts: `detector_failures`, `source_failures`,
    `transcription_errors`, `empty_transcriptions`, `candidates`, `published`.
- **Dependencies:** [P12]
- **Tests:** With fake runner/detector/source/transcription: AC26 (keyword `nova` → candidate
  accepted with source 0× and transcription 0× at decision time; then source 1× with
  `AUDIO_INPUT_PRE_ROLL_SECONDS=2`, transcription 1×, 1 `speech` event with the transcription,
  author, candidate's `message_id`, destination, 0 further rule evaluations and 0 hook invocations;
  `banana` → `keyword_no_match`, 0/0/0; transcription failure and empty → 0 events + count; WAV over
  `max_bytes` → counted, 0 transcriptions; *observed bounds* with the marker-tone fake source:
  `max_seconds: 8` env and ≤ 8 s header, 8.5 s WAV → source failure, pre-roll 0 → offset 0, late
  verdict with pre-roll 2 → marker at 0.5 s and phrase inside, pre-roll 1 → no marker; held
  transcription → detector invoked exactly once until first publish, then 3 events in detection
  order, 4 invocations, 0 after `stop_inputs` — the last two need P14's loop and are completed
  there); AC27 detector rows (command exit 0 `hey nova` → 1 candidate `accepted:wake_word_match`;
  exit 1, exit 2, 65-char → 0 candidates, source 0×, the last two counted; HTTP detected → 1
  accepted; `{"detected": "yes"}` and 500 → 0, counted; command stdin empty; HTTP request carries no
  audio; 0 hook invocations for published events); AC28 (5 accepted: attachment store 0 entries, 0
  files written (a `tmp_path` cwd scan), no bus-history/audit record/candidate/event contains WAV
  bytes or their base64, detector got no audio, transcription got each of the 5 WAVs once).
- **Risks:** Calling the engine on the transcribed event would double-decide and expose it to the
  hook — the publish path has no reference to the engine (asserted by AC26's 0 evaluations).
  Reading the WAV duration from the header must reject a header that lies about data length
  (`wav_data_layout` already validates). The HTTP detector with no timeout can hold a connection
  forever; that is the spec's intent (R7) and `stop_inputs` (P14) is its only bound.

### P14: Listen lifecycle, restart backoff and two-state health (R7 lifecycle/supervision/restart; AC26 ordering tail, AC37, AC38, AC39)
- **Files:** [`modules/audio_input/__init__.py`, `tests/test_audio_input.py`]
- **Description:**
  - `start_inputs` starts the loop as a supervised task (registered in `prepare`), whose first act is
    one detector invocation; `stop_inputs` sets a stopping flag, cancels the loop, terminates an
    in-flight detector process / closes the HTTP request, terminates an in-flight source
    (`CommandRecording.kill`, `:845`) discarding partial output, and releases an untranscribed
    segment; no invocation starts afterwards. A startup that never reaches `start_inputs` started
    nothing because `prepare` starts nothing.
  - Restart scheduler on the injected sleeper: after (c) — a declined candidate or a published event
    — wait 1 s and reset `n = 0`; after an unproductive settlement (no-detect, detector failure,
    source failure, transcription error, empty transcription) increment `n` and wait
    `min(retry_seconds × 2^(n−1), 300)`. Only one detector, acquisition, transcription in flight.
  - Health: state `healthy` at start; detector failure in `healthy` → `degraded` + exactly one
    `module.degraded` (`wake_word_detector_failed`, value-free); further failures report nothing;
    the first verdict (detected or exit 1) in `degraded` → `healthy` + exactly one `module.ready`;
    source/transcription outcomes never change the state. A detector terminated by `stop_inputs` is
    not a failure. Health is reported through the module's supervision surface (memory: a degraded set
    in `start_inputs` can be overridden by the coordinator's own `module.ready` — the first report
    can only happen after the first invocation settles, which is after `start_inputs` returns;
    verified by AC37/AC39 ordering).
- **Dependencies:** [P13]
- **Tests:** AC26 ordering tail (1 invocation until first publish; 3 events in detection order; 4
  invocations; 0 after `stop_inputs`); AC37 (after `prepare` before `start_inputs`: 0 invocations, 0
  processes, 0 candidates, 0 transcriptions, 0 `speech`; after: 1 detector, 0 source; another module's
  failed `prepare` → 0 for `audio_input`; `stop_inputs` terminates an in-flight detector (recorded)
  and an in-flight acquisition, 0 candidates/transcriptions follow, 600 s → 0 invocations); AC38 (exit
  1 every time, 3 600 s → 17 invocations at 0, 5, 15, 35, 75, 155, 315 then every 300 s, 0
  candidates/sources/transcriptions/events; detecting on exactly 4 → 4 sources and 4 transcriptions
  under accepting, 0 under declining; a transcription error → no second transcription and no
  invocation before 5 s); AC39 (the (a)/(b)/(c) timing rows at 4.9/5, 9.9/10, 0.9/1 s; error after
  (c) → 5 s; detector exit 2 and source failure back off alike; 20 failures ≤ 300 s apart and exactly
  1 `module.degraded`; exit 1 from healthy reports none; recovery sequence 3 fail/1 verdict/2 fail →
  degraded, ready, degraded with no duplicates; same with a detected verdict; source/transcription
  failure after the verdict adds none; HTTP connection refused backs off alike; a detector pending
  3 600 s is neither terminated nor counted).
- **Risks:** The coordinator-drain tie (memory): a `stop_inputs` that waits on process termination
  must resolve its state in `finally` so a coordinator timeout still leaves no process running.
  `module.ready` re-reported by the coordinator after `start_inputs` (memory P14 blocker) could
  break AC39's exact sequence; the step asserts the health stream and stops and flags if the
  coordinator interleaves its own report. Off-by-one in `2^(n−1)` is pinned by AC38's literal times.

### P15: Operator guide (EN normative, FR translation) and README (R9; AC32 docs half)
- **Files:** [`docs/triggers.md`, `docs/triggers.fr.md`, `README.md`, `tests/test_docs_bilingual.py`, `tests/test_declared_triggers.py`]
- **Description:** `docs/triggers.md`: generic vs declared types; the match vocabulary with A2
  (wildcard, 128 characters, step bound), A3 (one probability parameter), A4 (field catalog); hooked
  types (D1/D2 bounds, fail-closed veto); the sentence "A configured policy replaces the default
  entirely."; one YAML example per family — `reward{reward_ids, min_cost, max_cost}`, operator
  `button{button_ids}` with the three surfaces and the OS-level hotkey binding of
  `python -m modules.operator press <button>`, spoken `keyword` and `wake_word` both over the
  detector's phrase, `interval{every_minutes, probability, window}`; continuous listening stated as
  out of scope and a future, deliberately expensive plugin; the manual real-instance trial procedure
  pointing at `docs/campaigns/phase8/trial-record.md`. `docs/triggers.fr.md` translates it and states
  that the English text wins. `README.md`: capability list and layout table gain `operator`, `timer`,
  listening and channel points, each "proven against fakes only". `tests/test_docs_bilingual.py`
  (allowlisted) adds the `triggers` pair to the declared set.
- **Dependencies:** [P14]
- **Tests:** AC32 docs half in `test_declared_triggers.py` (pair discovered; 4 family examples, each
  parsed with `yaml.safe_load` and validated through the loader against the shipped manifests; the
  replace-the-default sentence; the out-of-scope statement). Allowlisted
  `test_docs_bilingual.py::test_the_discovered_pairs_are_exactly_the_declared_guides`.
- **Risks:** An example that drifts from the manifests misleads operators — the test loads each
  example. The FR file must keep the same headings structure the bilingual test checks.

### P15a: AC33 conversion — repository facts without `git`, `grep` or a child interpreter (R9; AC33; D8)
- **Files:** [`tests/conftest.py`, `tests/test_agentic_loop.py`, `tests/test_capture.py`, `tests/test_chat_context_module.py`, `tests/test_users.py`, `tests/test_proxy.py`, `tests/test_hygiene.py`, `tests/test_input_ownership.py`, `tests/test_web.py`, `tests/test_phase5_docs_status.py`, `tests/test_automation_bridge.py`, `tests/test_viewer_memory.py`, `tests/fixtures/baselines/f900f27/`, `tests/fixtures/baselines/7c29434/`]
- **Description:** These ledger nodes start `git`, `grep` or `python` only to read facts about the
  repository. Each is converted to an in-process reader in `tests/conftest.py`:
  - `files_containing(root, needle, ignore_case)` is a pure-Python `rglob` that skips
    `__pycache__`. It replaces `grep -r`/`grep -c` in the `*_names_no_platform` tests of
    `test_agentic_loop`, `test_capture`, `test_chat_context_module`, `test_users` and `test_proxy`.
  - `tracked_files(pattern)` parses the git index (`.git/index` format v2–v4). It follows a worktree's
    `.git` file to its `gitdir`. It replaces `git ls-files` in `test_phase5_docs_status` (two ac37
    nodes) and in `test_automation_bridge`.
  - `ignored(path)` matches `.gitignore` patterns with `fnmatch`, with directory and negation rules.
    It replaces `git check-ignore` in `test_hygiene::test_n8_...`.
  - For "zero changed lines since the phase base" (`test_input_ownership::test_ac2_...` against
    `f900f27`, `test_web::test_ac24_...` against `7c29434`): the step writes the four shipped
    `*.example` files *as they were at that commit* to
    `tests/fixtures/baselines/<commit>/`. They are taken with `git show` at authoring time, outside
    the suite, and the commit hash is recorded in a `SOURCE` file. The test then asserts byte
    equality, which is the same claim as `git diff --numstat` reporting 0 lines.
  - Tests that ran `pytest` in a child process are read through `ast` instead. These are
    `test_input_ownership::test_ac44_...`, which counts ownership rejections in the integration
    scenarios, and the `--collect-only` node in `test_automation_bridge`. The ac44 node imports the
    scenario coroutines of `test_integration.py` and runs them in-process with a counting subscriber.
    The collect node parses the test file's functions with `ast`.
  - `test_viewer_memory::test_ac19_cli_...` ran `python -X importtime`. It now walks the
    module-level imports of `modules/viewer_memory/__main__.py` and its in-repo transitive imports
    with `ast`, and asserts that no network client is reachable, as before.
  Each converted node is removed from `AC33_PENDING`.
- **Dependencies:** [P15]
- **Tests:** Each converted node keeps its assertions and passes under the guard. Two negative
  checks prove the readers are sensitive:
  - A temporary file containing `twitch` under `tmp_path` is found by `files_containing`.
  - A fixture that differs by one byte from the shipped profile fails the equality check.
  The ledger lost exactly the converted ids.
- **Risks:** An index parser that misses extension records or the split index would list the wrong
  files. The parser is checked against a `git ls-files` list recorded at authoring time and committed
  as a fixture. A baseline fixture copied from the wrong commit makes the "zero changed lines"
  claim vacuous, so `SOURCE` records the hash and the step report quotes `git show <hash>:<file> |
  sha256sum` next to the fixture's digest.

### P15b: AC33 conversion — child-process tests of the shipped runners over fake processes (R9; AC33; D8)
- **Files:** [`tests/conftest.py`, `tests/test_audio_input.py`, `tests/test_audio_output.py`, `tests/test_capture.py`, `tests/test_config_ui.py`, `tests/test_shutdown.py`]
- **Description:** `tests/conftest.py` gains `FakeOSProcess`, a scripted stand-in for
  `asyncio.subprocess.Process` and `subprocess.Popen`. It has a pid, a process group, a script of
  stdout/stderr chunks and an exit code, and modes such as "ignore terminate", "never write", "leave a
  child holding stdout" and "flood stdout". It records every `send_signal`/`terminate`/`kill`/`os.killpg`
  addressed to it and the `start_new_session`/`env`/`shell` options it was given. A `fake_spawn`
  factory returns one per call. These tests are converted to it, each through the seam its subject
  already has:
  - `test_audio_input` (4 `test_the_shipped_runner_*` nodes) through `_subprocess_runner`. The
    shipped `RecorderProcess` wrapper still runs; only `asyncio.create_subprocess_exec` beneath it is
    the fake, and `os.killpg` is monkeypatched to route to the fake's group.
  - `test_audio_output::test_the_subprocess_runner_feeds_a_real_player_its_stdin` through `spawn`.
  - `test_capture` (5 `run_command`/`command_source` nodes and the shell-script background-capture
    node) through `spawn`/`_subprocess_runner`.
  - `test_config_ui` (the 15 `ac30`/`ac31`/`ac32`/`ac35`/`ac39_ac40`/`d9`/child-environment nodes)
    through `Supervisor(popen=...)`. The fake child publishes the status record the shipped child would
    write, which `_apply_ui`'s `body` scripts already describe. Each body becomes a fake script.
  - `test_shutdown` (the two `test_cli_*` nodes) now calls `core.main.main(argv)` in-process. They
    assert the same deadline and exit code, and that no non-daemon thread is left
    (`threading.enumerate()`), which stands in for "the process exits".
  The `mkfifo`/`os.open` helpers that only fed real children are removed together with the child
  scripts. Converted nodes leave `AC33_PENDING`.
- **Dependencies:** [P15a]
- **Tests:** Each converted node passes under the guard with its assertions kept. These include the
  process-group kill reaching the child, the kill at the deadline, bounded stdout, `shell=False` with
  the literal argv `;echo x`, `$HOME`, `a b`, and an inherited environment plus the status variable.
  New negative checks prove the fake is not trivially green: a runner that forgets `killpg` fails the
  wrapper test, and a supervisor that waits without a bound fails the ignore-terminate test on the
  ManualClock.
- **Risks:** This step loses the most fidelity. A fake process cannot prove that the OS delivers a
  group kill, so `summary.md` (P16) names these nodes as "fake-OS only" (D8). A fake that is too
  obliging would make the runners look correct, which is why the negative checks above exist. Signal
  and reap ordering must be scripted from what the runner actually calls, not from what the old
  child did.

### P15c: AC33 conversion — HTTP over an in-memory client and unreachable endpoints as fake sessions (R9; AC33; D8)
- **Files:** [`tests/conftest.py`, `tests/test_kick.py`, `tests/test_audio_input.py`, `tests/test_clips.py`, `tests/test_input_ownership.py`, `tests/test_integration.py`, `tests/test_main.py`, `tests/test_profiles.py`, `tests/test_youtube.py`]
- **Description:**
  - `tests/conftest.py` gains `InMemoryClient(app)`, a socket-free replacement for
    `TestClient(TestServer(app))`. It runs the application's startup and cleanup hooks. Each
    `request(method, path, headers, body)` builds an `aiohttp.test_utils.make_mocked_request`
    carrying the body in a `StreamReader` and awaits the application's handler. It returns status,
    headers and body with the same accessors the 31 `test_kick.py` nodes use. `SignedWebhookSender.post`
    (`tests/conftest.py:2320-2420`) posts through it.
  - The "unreachable endpoint" nodes connect to `127.0.0.1:1` or a closed port. These are
    `test_audio_input::test_the_real_transport_against_a_closed_port_...`, `test_clips::test_ac12_...`,
    `test_input_ownership` ac14/ac5, `test_integration` ac27/r8, `test_main::test_the_accepted_limits_...`,
    `test_profiles` ac31/ac43 and `test_youtube::test_ac39_...`. They now monkeypatch
    `aiohttp.ClientSession`, or use the module's own session seam where it has one (kick
    `_session_factory`), with a `FakeSession` that raises `aiohttp.ClientConnectorError` on that URL.
    The production transport code still runs, as before. Only the connection beneath it is the fake.
  Converted nodes leave `AC33_PENDING`.
- **Dependencies:** [P15b]
- **Tests:** The 31 kick nodes keep their assertions (signature, timestamp, duplicate id, status
  codes) and pass under the guard. A new negative check shows a wrong signature still gets `403` over
  `InMemoryClient`. The unreachable-endpoint nodes still see a degraded, value-free outcome bounded
  by the sleeper.
- **Risks:** `make_mocked_request` bypasses aiohttp's HTTP parser, so header-size limits, chunked
  bodies and `Host` parsing are not exercised. The step lists any kick assertion that depended on
  the parser and keeps it through a direct call to `aiohttp.http_parser.HttpRequestParser` with the
  raw bytes. Patching `aiohttp.ClientSession` is process-wide while the test runs, which is
  acceptable because `monkeypatch` restores it.

### P15d: AC33 conversion — the Streamer.bot fake instance over in-memory WebSockets (R9; AC33; D8)
- **Files:** [`tests/test_automation_bridge.py`, `tests/conftest.py`]
- **Description:** `FakeInstance` (`tests/test_automation_bridge.py:940-990`) serves a loopback
  `web.TCPSite`. It is rebuilt on `MemoryWebSocketPair` (`tests/conftest.py:2073`). Each connect
  attempt hands the module one `client` end and runs `FakeInstance._handle` on the `server` end. The
  connection, disconnection, held-acknowledgement and withheld-inventory behaviour is unchanged.
  The streamerbot module creates its own `aiohttp.ClientSession()`
  (`modules/streamerbot/__init__.py:2318`), so a fixture monkeypatches `aiohttp.ClientSession` with a
  session whose `ws_connect(url)` asks the matching `FakeInstance` for a pair. That is a dependency
  patch, not the subject: no production change, and no new seam in streamerbot. Refused connections
  (instance stopped) raise `aiohttp.ClientConnectorError`. `url` stays a nominal
  `ws://127.0.0.1:<n>/` so the URL assertions hold. The 78 nodes leave `AC33_PENDING`.
- **Dependencies:** [P15c]
- **Tests:** All 78 nodes pass under the guard with their assertions kept. A new check confirms the
  module's `_writer` probe (`modules/streamerbot/__init__.py:2200`, a `getattr` with a default) still
  sees a live connection on `MemoryWebSocket`. A dropped pair (`drop`, close code 1006) still drives
  the reconnect path.
- **Risks:** `MemoryWebSocket` must offer every method the module calls on a client socket. The step
  lists them with `grep` over `modules/streamerbot/__init__.py` and adds any that are missing
  (`ping`, `exception`) to `MemoryWebSocket`, with its own test. Timing tests that relied on real I/O
  scheduling may need the ManualClock; a test whose assertion would have to change for that is
  flagged, not loosened.

### P15e: AC33 conversion — real listeners, the two-process topology and CLI smoke in-process; the ledger closes (R9; AC33; D8)
- **Files:** [`tests/conftest.py`, `tests/test_stream_control.py`, `tests/test_proxy.py`, `tests/test_proxy_process.py`, `tests/test_installation_doc.py`, `tests/test_profiles.py`, `tests/test_phase5_docs_status.py`, `tests/test_config_ui.py`]
- **Description:**
  - `test_stream_control::test_the_default_factory_frames_the_protocol_over_a_real_loopback_socket`
    drives the default factory's framing over an `asyncio.StreamReader` fed with bytes and a
    recording `StreamWriter` transport.
  - `test_proxy::test_the_real_listener_cuts_an_oversized_binary_frame_...` feeds the raw oversized
    frame into `aiohttp`'s `WebSocketReader`, configured with the listener's shipped `max_msg_size`.
    It asserts the same cut before the module sees the frame.
  - `test_proxy_process::test_ac39_two_process_topology_over_loopback` runs the brain and agent
    runtimes from the same shipped `*.example` profiles in one event loop. They are joined by
    `MemoryWebSocketPair`, the same way `test_proxy.py` already joins them, and the test makes the
    same correlated-run and clean-shutdown assertions.
  - `test_installation_doc` (2 nodes) calls `core.main.main([...])` and `core.config_ui.main(["--help"])`
    in-process with `capsys`. The console-script node resolves `twitch-ia-compagnon-config` through
    `importlib.metadata.entry_points()` and calls it.
  - `test_profiles::test_installed_distribution_...` (allowlisted 19→21) replaces `pip install` into a
    temporary target with two in-process checks. The first reads the package data through
    `setuptools.config.pyprojecttoml.read_configuration` and resolves every shipped manifest in it.
    The second runs `--help` in-process. The 21-manifest count it asserts is unchanged from P11's
    adaptation.
  - `test_phase5_docs_status` (ac36, 2 nodes) ran child interpreters to count sockets. It now
    asserts its socket counter against the guard's refusals in-process.
  - `test_config_ui::test_ac5_the_socket_patch_is_effective` keeps its claim (its patch refuses
    sockets) and asserts it inside the guard.
  After this step `AC33_PENDING` is empty. The step deletes the ledger mechanism (the frozenset and
  the fixture's ledger branch) from `tests/conftest.py`. The only exemption left is P1's operator
  opt-in for a trial node, and it is inert in the default run.
- **Dependencies:** [P15d]
- **Tests:** Every converted node passes under the guard. `tests/test_ac33_isolation.py` drops the
  ledger checks and gains `test_the_guard_has_no_ledger`, which finds no `AC33_PENDING` symbol and
  no ledger branch. The full suite passes.
- **Risks:** The in-process topology no longer proves that two OS processes cooperate, and the
  installed-distribution test no longer proves a real `pip install` works. Both are named in
  `summary.md` as fake-only (D8). The allowlisted `test_profiles` test now changes in mechanism as
  well as constant. That goes beyond the allowlist's "constants only", so it is flagged in the step
  report and checked in P17 (no assertion dropped).

### P16: Phase summary, trial-record template, whole-suite run (R9; AC32 evidence half, AC33)
- **Files:** [`docs/campaigns/phase8/summary.md`, `docs/campaigns/phase8/trial-record.md`, `tests/test_declared_triggers.py`]
- **Description:** `trial-record.md` with `Status: not run` and the fields the trial procedure fills
  (date, versions, per family: what was triggered, decision trace excerpt, outcome). `summary.md`:
  per requirement R1–R9 the commit (short hash of the step that carried it) and the test identifier;
  per family what can and still cannot be triggered (no fulfil/refund, no continuous listening, no
  privileged `manual`/`timer`, hotkey binding is an OS step). Run the full suite once under the
  guard. Record the pass count, the D8 conversion list (166 nodes, 24 files) and, per converted
  node, the fidelity lost ("fake-OS only", "in-process topology", "no real install") in the
  summary.
- **Dependencies:** [P15e]
- **Tests:** AC32 evidence half: `trial-record.md` exists with status `not run`, and `summary.md`
  lists R1–R9 each with a commit hash pattern and a `tests/...::...` identifier that exists.
  AC33 on the **whole** suite, in `test_declared_triggers.py`:
  - `test_ac33_the_whole_suite_runs_under_the_guard` makes three assertions. `_ac33_isolation` is
    autouse and has no ledger left. Every item in `request.session.items` uses the fixture. With the
    environment the run has (CI sets no trial opt-in), the number of items that would run unpatched
    is 0.
  - `test_ac33_no_test_reaches_a_real_spawn_or_socket` fails if any file under `tests/` contains a
    call to a socket-creation or process-spawn API outside `tests/conftest.py`'s guard and fakes and
    `tests/test_ac33_isolation.py`'s `pytest.raises` blocks. It scans with `ast` for `Popen`,
    `create_subprocess_exec`, `os.system`, `TCPSite`, `TestServer` and `create_connection`.
  - The full suite passes, and the CI command `python -m pytest tests/ -q -p no:cacheprovider` is
    green.
- **Risks:** Commit hashes are only known after the orchestrator commits each step; the summary is
  written last and the test checks hash *shape* plus that each named test id is collectable. The
  `ast` scan could flag a legitimate fake that merely *names* `Popen`, for example a type annotation.
  The scan looks for calls only, and the guard and the fakes are excluded by path.

### P17: Full-branch review gate (all of R1–R9; AC1–AC40)
- **Files:** [`docs/campaigns/phase8/full-branch-review.md`]
- **Description:** Before PR delivery, review the whole branch diff `7782e5f..HEAD` as one change,
  not commit by commit. Per-commit reviews miss cross-file bugs by construction: each step can be
  correct in isolation yet broken when its files interact — e.g. P6's `draws` shape consumed by the
  traces of P8/P9/P11/P13, P7's `evaluate_async` refactor against the sync path every shipped input
  still calls, the vocabulary of P2 against the ownership guard that P12 now exercises with a
  producing `audio_input`, the hook supply of P7 against P12's `prepare`, and the counts adapted in
  two steps (P9, P11), and P1's guard against every fake P15a–P15e introduce (a fake that opens a
  socket would only show up when the whole suite runs). The review checks: every requirement and AC mapped to a passing test; the
  allowlist only adapted (diff of each allowlisted test shows constant changes only); no
  `*.example` profile edited; `dependencies` unchanged; no module imports `core.runtime/loader/
  lifecycle` at module level in `modules/operator`; no token/header/audio in any trace; the
  flagged decisions D1–D8; the AC33 conversions of P15a–P15e test by test. For each
  converted node, the old and new `assert` statements are compared: none is removed or loosened, and
  only the transport or fake name changes (shortcut 1, relaxed tests). No converted test mocks its
  own subject (shortcut 10, pass-by-mock). The fake that replaces a real process or socket is a
  *dependency* double. The gate also confirms that the guard has no exemption left, and it checks
  the 11 fake-done shortcuts. Findings are fixed in a
  fix round before delivery; the record is written to `full-branch-review.md`.
- **Dependencies:** [P16]
- **Tests:** Full suite green; P1's AC16 comparison green on the final tree; `tests/test_triggers.py`
  byte-identical to `7782e5f` (`git diff --exit-code 7782e5f -- tests/test_triggers.py`).
- **Risks:** A gate that only re-reads step reports repeats per-commit blindness; it must read the
  integrated diff and run the suite on the final tree.

## Caller enumeration

Search: `grep -rn` over `core/`, `modules/`, `tests/` for `TriggerDecision(`, `TriggerTypeDeclaration(`,
`TriggerSpec(`, `TriggerEngine(`, `.register(`, `.evaluate(`, `fields(TriggerDecision)`. Blind spots:
fixture manifests under `tests/fixtures/modules` reach the loader without a call site; third-party
modules outside the repo (none known) would see only additive optional fields.

| File | Function/Method | Migration Note |
|------|----------------|----------------|
| core/triggers.py | `BUILTIN_TRIGGER_TYPES` (4 `TriggerTypeDeclaration(...)`) | No change: new fields optional, absent on generic types (P3). |
| core/triggers.py | engine's own `TriggerDecision(...)` constructions | `draws` defaults to `()`; declared probability rules pass entries (P6). |
| core/runtime.py:1458 | `_OwnedTriggers.evaluate` builds `TriggerDecision(... REASON_INPUT_NOT_OWNED)` | Unchanged (`draws` default). Gains `evaluate_async`/`supply_hook` forwarders (P7). |
| core/runtime.py:1664 | `ModuleContext` | Gains `supply_trigger_hook` (new, 0 existing callers) (P7). |
| core/loader.py:2107 | `_manifest_triggers` builds `TriggerTypeDeclaration`, `TriggerSpec` | Passes the new keys and `fields` (P4). |
| core/loader.py:1366 | `registry.register(name, declaration.triggers, companion_name=...)` | Signature unchanged; accepts declared types (P5). |
| core/main.py:1295 | `TriggerEngine(...)` | Unchanged: `sleeper` optional and derived (D3). |
| modules/twitch/__init__.py:2426, kick:1626, youtube:1836, streamerbot:1987, watch:707 | `engine.evaluate(event, ...)` | Unchanged sync API; twitch's reward events use the same call (P8). |
| modules/operator, modules/timer (new) | `context.triggers.evaluate` | New callers of the sync API (P9, P11). |
| modules/audio_input (P13) | `context.triggers.evaluate_async`, `context.supply_trigger_hook` | New callers of the new APIs. |
| tests/test_watch.py | `TriggerDecision(...)` | Unchanged: `draws` default. |
| tests/test_triggers.py:819 | `fields(TriggerDecision)` subset assertion | Still holds (`<=`); file not edited (AC10). |
| tests/test_triggers.py, tests/test_twitch.py | `TriggerTypeDeclaration(...)`, `TriggerSpec(...)` | Unchanged: keyword fields keep their meaning. |
| modules/kick, youtube, streamerbot, watch `_trace_decision` | build their own six-key trace payload | Unchanged; they host no declared probability type (D6). |

## Requirement coverage

| Requirement | Steps |
|-------------|-------|
| R1 | P3, P4, P5 |
| R2 | P5, P6, P7 (admission, AC9) |
| R3 | P7, P12 (shipped hook) |
| R4 | P1, P2, P16/P17 (AC16 final) |
| R5 | P8 |
| R6 | P9, P10 |
| R7 | P12, P13, P14 |
| R8 | P11 |
| R9 | P1 (whole-suite guard and ledger), P15, P15a–P15e (AC33 conversions), P16 (AC33 proof), P17 |

## Ordering rationale

P1 comes first because AC16's phase-7 baseline is only truthful when captured before any production
change. P2 appends the vocabulary, which P3's `kinds` validation, the brain routes and every new
input need. The kernel is then built bottom-up along its own data flow: contract shapes (P3) before
the loader that constructs them (P4), registry acceptance and policy validation (P5) before the
engine can evaluate a declared rule (P6), and the synchronous declared path (P6) before the async
hooked path that refactors around it (P7). Every family depends on the finished kernel. Twitch (P8)
comes first because it is the smallest family and exercises declared rules in an existing input.
The operator (P9, then its press command P10, which needs P9's request core) comes before the timer
(P11) so the allowlisted module counts move 19→20→21 in order. P11 also depends on P10 so those
count constants are edited in a fixed sequence. The audio input comes last (P12 → P13 → P14). It
uses the most kernel surface (hooks, `evaluate_async`, the input role), and its three parts build on
each other: settings and hook, then the one-way pipeline, then the lifecycle and backoff that drive
the pipeline. Documentation (P15) is written once every behaviour it documents exists, and its
examples are loaded against the final manifests. The AC33 conversions (P15a–P15e) come after P15:
several of them edit files the feature steps also edit (`test_audio_input.py`, `test_config_ui.py`,
`test_profiles.py`, `test_input_ownership.py`), and running them last keeps each file's edits in one
order. P1 installs the guard first, so every test the phase adds is isolated from its first commit,
and the ledger keeps the 166 pre-existing nodes green until they are converted. The conversions are
chained: P15a, then P15b, P15c, P15d and P15e. Most of them extend `tests/conftest.py` and shrink the
same ledger, and P15e closes it. P16 proves AC33 on the whole suite, so it needs P15e. Twitch
(P8) precedes the operator (P9) by an explicit dependency, because both edit
`test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`. The summary (P16)
needs the step commits. The
full-branch gate (P17) runs last, on the integrated tree.
