---
spec: "phase4-local-config-ui"
version: "1.0"
author: "adversarial-plan"
based-on: "adversarial-spec"
findings-input: true
---

# Implementation Plan

Phase 4 adds a separate local configuration UI (`python -m core.config_ui`) that writes only
an overlay merged over the untouched base file, never reveals secrets, applies changes by a
supervised restart, and generates every module page from the module's manifest. The plan runs
in this order: the shared building blocks (the schema annotation, the overlay module, the
runtime merge, the status publisher), then the manifest presentation metadata, then the UI in
layers (guards, then pages, then Check, then Save, then status reading, then Apply), then the
packaging, the documentation and a full-branch gate.

Decisions this plan makes where the spec leaves the mechanism open. Each is flagged here and
again in the step that implements it.

- **D1 — in-memory overlay seam.** R5 requires Check to validate a draft "through
  `core.main.check_config`" without writing the overlay. `load_config`, `check_config` and `run`
  therefore gain a keyword-only `overlay` (path) and `load_config`/`check_config` also gain a
  keyword-only `overlay_document` (an already-parsed mapping used *instead of* reading the
  overlay file). The UI passes the draft overlay (on-disk overlay plus the page's unsaved edits)
  through that argument. No temporary file is written, and the base path keeps anchoring relative
  paths.
- **D2 — socket-free request core.** AC5 runs the whole UI test module with socket creation
  patched to raise. aiohttp's `TestClient` binds a socket, and a standard asyncio event loop
  creates a self-pipe `socketpair`. So the UI is a pure, synchronous `ConfigUI.handle(UIRequest) ->
  UIResponse` core, where the request and response are plain dataclasses. A thin aiohttp adapter
  (`serve()`) is used only by `__main__`; its bind is never exercised by the tests, but its
  request bridge `_dispatch` is (D8). Supervision uses `subprocess.Popen` (argv list,
  `shell=False`) with an injected clock and wait function, so no test needs a positive `sleep`
  (the hygiene test `test_ac45_the_suite_makes_no_positive_duration_sleep_call` forbids one).
- **D7 — socket-free event loop for Check (findings P1).** R5/AC5/AC23 forbid *creating* a
  socket, and `check_config` is a coroutine whose settings hooks need a real loop
  (`ModuleLoader._await_module` schedules tasks under the startup deadline), so neither a bare
  `coro.send(None)` driver nor `asyncio.run` (which calls `socket.socketpair()` for the selector
  self-pipe) is compliant. `core/config_ui/__init__.py` therefore defines
  `_SocketFreeEventLoop(asyncio.SelectorEventLoop)`:
  - it overrides the CPython self-pipe hooks `_make_self_pipe`, `_close_self_pipe` and
    `_write_to_self` with no-ops, so the loop creates no socket at all;
  - its selector is a `selectors.DefaultSelector` subclass whose `select(timeout)` caps the
    timeout at 10 ms (a `None` timeout becomes 10 ms), so a callback scheduled from another thread
    by `call_soon_threadsafe` (which would normally write to the self-pipe) is still picked up
    within 10 ms — the loop polls instead of being woken;
  - `_run_socket_free(coro)` creates the loop, runs `run_until_complete`, then
    `shutdown_asyncgens`, `shutdown_default_executor` and `close` in a `finally`, mirroring
    `asyncio.run` (Python ≥ 3.10 has no `loop_factory` on `asyncio.run`, hence the manual runner).
  - The private hooks are pinned by a test asserting `asyncio.SelectorEventLoop` still defines
    all three, so an interpreter upgrade that renames them fails loudly instead of silently
    reintroducing a socketpair. This is a flagged decision (private CPython API).
- **D8 — request bridge off the serving loop (findings P2).** `serve()`'s catch-all aiohttp
  handler never calls `ConfigUI.handle` on the serving loop: it converts the aiohttp request with
  the pure `_to_ui_request(...)` and calls `await _dispatch(ui, ui_request, executor)`, which runs
  `ui.handle` through `loop.run_in_executor(executor, ...)` on a dedicated
  `ThreadPoolExecutor(max_workers=4, thread_name_prefix="config-ui")`. Check's
  `_run_socket_free` therefore always runs in a worker thread with no running loop. As a guard,
  the default checker raises `RuntimeError("Check must not run on a running event loop")` when
  `asyncio.get_running_loop()` succeeds in its thread, so a regression that moves `handle` onto
  the serving loop fails the bridge test instead of hanging. Mutating operations (`/save`,
  `/remove`, `/check`, `/apply`) are serialised by one `threading.Lock` in `ConfigUI`, and the
  session table has its own lock, because `handle` is now reached from several threads.
- **D9 — child output never back-pressures (findings P4).** The supervised child's stdout is
  inherited from the UI process (not captured, so it cannot fill a pipe), and its stderr is a
  pipe drained continuously, from `Popen` until EOF, by one daemon reader thread per child. The
  thread reads 4 KiB chunks, forwards each chunk to the UI's own stderr (so the operator still
  sees the runtime's value-free diagnostics, as if it had been started directly) and keeps only a
  bounded tail (the last 8 KiB, a `bytearray` trimmed on each append) for the refused report,
  which shows at most the last 2 KiB after redaction. Draining continues after Apply is
  accepted, for the child's whole life; stopping a child joins its reader thread with a bounded
  timeout after the process has been waited for.
- **D3 — single UI module.** The spec's targets list only `core/config_ui/__init__.py` and
  `__main__.py`, so the whole UI (guards, rendering, check, save, status reader, supervisor)
  lives in `__init__.py`, split into clearly delimited sections. HTML, CSS and a small inline
  script are Python string constants, and the UI ships no data files. `core*` is already matched
  by `[tool.setuptools.packages.find]`, so no package-data line is needed. (The memory rule about
  the package-data line applies to `modules/<name>` packages only.)
- **D4 — status variable name.** The status-file environment variable is
  `TWITCH_IA_COMPAGNON_STATUS_FILE`, exposed as `core.overlay.STATUS_FILE_VARIABLE`. The runtime,
  the UI and `docs/config-ui.md` all read it from that one constant.
- **D5 — health transitions reach the publisher through the bus.** The publisher subscribes to
  the `module.ready` / `module.degraded` traces (`core.contracts.TRACE_MODULE_READY` /
  `TRACE_MODULE_DEGRADED`) that `ModuleHealth` already emits. It does not patch
  `core/runtime.py`, which is not a target.
- **D6 — discovery reuse.** The UI discovers modules and reads their declarations
  (`credentials`, `triggers`, `actions`, `settings_schema`) through `core.loader.ModuleLoader`'s
  existing discovery (`_discover()`, the same private-method pattern
  `core.main._validate_enabled` already uses). No second manifest parser is written.
  `core/loader.py` is not a target and is not edited.

## Steps

### P1: `default` schema annotation in the validator (R3; AC14)
- **Files:** [`core/contracts.py`, `tests/test_manifest_presentation.py`]
- **Description:** Implements R3's validator half.
  - Add `"default"` to `_ANNOTATION_KEYWORDS` (beside `title`, `description`) so `validate_schema` accepts it.
  - After the node's own keywords are checked, validate `schema["default"]` against the node itself, using the module's existing value-validation function, the same one used to validate settings against a schema. A failure raises `SchemaError` labelled `<label>.default`.
  - The check recurses with the existing recursion, so a nested `properties`/`items` node's `default` is checked too.
  - `validate_value`-style checking of *values* must ignore `default` (no constraint effect).
  - Inside `properties`, a key literally named `default` is a property name and never an annotation. This works because keyword scanning happens only at node level, and the properties mapping is iterated as names. Confirm this with the audio_output `voices` schema, whose properties are `allowed` and `default`.
- **Dependencies:** []
- **Tests:** Create `tests/test_manifest_presentation.py` with the contract tests of AC14:
  - `{type: integer, minimum: 1, default: 5}` is accepted.
  - `{type: integer, minimum: 1, default: 0}` and `{type: string, default: 3}` raise with a label ending `.default`.
  - Validating the value `7` gives an identical result with and without `default: 5`.
  - A nested `properties.x.default` violation names `…properties.x.default`.
  - The audio_output manifest's `voices` schema still has exactly the properties `{allowed, default}` and passes `validate_schema`.
  - Run `tests/test_contracts.py` and `tests/test_loader.py` unchanged.
- **Risks:**
  - A `default` validated through the value validator could recurse into `default` again. Guard it so the annotation check validates the value against the node's constraints only.
  - A test in `tests/test_contracts.py` may pin the annotation set or the "supported" keyword list printed in the error message. Grep `supported:` / `_ANNOTATION_KEYWORDS` before the change, and adapt only by adding `default` to an expected listing. If an exact-message assertion breaks, that is an allowlist question: stop and flag it rather than weaken it.

### P2: Shared overlay module — path, read, merge, digest, status-path collision (R2, A1, A5, R7 digest; AC7, AC8)
- **Files:** [`core/overlay.py`, `tests/test_overlay.py`]
- **Description:** Create `core/overlay.py` as the one implementation shared by the runtime and the UI. It names no module and imports only the stdlib and `yaml`. It provides:
  - `STATUS_FILE_VARIABLE = "TWITCH_IA_COMPAGNON_STATUS_FILE"` (D4).
  - `OverlayError(ConfigurationError-compatible message)`. Define it as a plain `RuntimeError` subclass carrying a value-free message, so `core.main` can map it to `ConfigurationError` without importing cycles.
  - `implicit_overlay_path(base: Path) -> Path | None` per A1:
    - Strip a trailing `.example`.
    - Then replace a final `.yaml`/`.yml` with `.local.yaml` in the same directory.
    - Any other name returns `None`.
  - `resolve_overlay_path(base, explicit) -> Path | None`: the explicit `--overlay` wins, otherwise the implicit path.
  - `read_overlay(path) -> Mapping`:
    - An absent file returns `{}`.
    - An empty file or a YAML `null` document returns `{}`.
    - A file that is unreadable, not valid YAML, or not a mapping raises `OverlayError` with `"overlay file <path>: is not readable" / "is not valid YAML" / "must be a mapping"`. The message never quotes the content or the parser excerpt; catch `yaml.YAMLError` and re-raise `from None`.
  - `deep_merge(base, overlay) -> dict` per R2:
    - A mapping merges key by key, recursively.
    - Any other overlay value (scalar, list, null) replaces the base value wholesale.
    - Keys that exist only in the base are kept.
    - Inputs are never mutated; the result is a fresh deep copy.
  - `read_base(path) -> Mapping`: the same parsing `load_config` does today, with the same messages, so both callers share it.
  - `canonical_digest(document) -> str`: SHA-256 of `json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)`.
    - Non-string mapping keys are converted to `str()` before sorting, through a pre-pass, because `sort_keys` fails on mixed key types.
    - The result is lowercase hex.
  - `on_disk_digest(base, overlay_path) -> str` = `canonical_digest(deep_merge(read_base(base), read_overlay(overlay_path)))`.
  - `same_file(a, b) -> bool`: compares `os.path.realpath(os.path.abspath(x))` of both paths, which follows symbolic links whether or not the files exist.
  - `status_path_collision(status, base, overlay) -> str | None`: returns `"base"` / `"overlay"` / `None`. It uses the same resolved comparison as `same_file`.
  - `default_status_path(base) -> Path`: the base file's full name with `.status.json` appended (A5).
- **Dependencies:** []
- **Tests:** Create `tests/test_overlay.py` covering:
  - AC7: the exact merged document, `a: null` replacing, and the input mappings left unmutated.
  - AC8: `config.yaml` → `config.local.yaml`, `presence.yaml.example` → `presence.local.yaml`, `x.yml` → `x.local.yaml`, `config.txt` → `None`, an explicit path winning.
  - `read_overlay`:
    - An absent file and an empty file give `{}`.
    - `[1, 2]` → OverlayError naming the path.
    - Invalid YAML containing `CANARY-OVERLAY` → a message without the canary.
  - `canonical_digest`:
    - Stable under key order.
    - Non-ASCII written verbatim, and hashes the literal UTF-8 bytes.
    - A YAML date and an integer key are stringified.
    - Always 64 lowercase hex characters.
  - `status_path_collision` returns base or overlay for:
    - a path equal to the file;
    - a relative non-normalised spelling (`./sub/../config.local.yaml` with `sub/` present) under `monkeypatch.chdir`;
    - a symlink to the base;
    - a not-yet-existing overlay path.
  - A different name gives `None`.
- **Risks:**
  - `realpath` on a non-existent path whose parent is a symlink still resolves the parent, which is desired. Test it.
  - `default=str` must not hide mappings with non-string keys. The pre-pass converts keys recursively, and the digest must be identical between the runtime (P4) and the UI (P14), which is guaranteed because both call this function.
  - An import cycle `core.main` ↔ `core.overlay`: `core.overlay` must not import `core.main`.

### P3: Runtime merges the overlay; `--overlay` CLI; public limit declaration (R2; AC9, AC10)
- **Files:** [`core/main.py`, `tests/test_overlay.py`]
- **Description:** Non-trivial (API change, branching). Changes to `core/main.py`:
  - **`load_config(config_path, *, environ=None, overlay=None, overlay_document=None)`:**
    - Read the base via `core.overlay.read_base`, which keeps today's exact diagnostics.
    - Resolve the overlay path with `resolve_overlay_path(base, overlay)`. With no path (A1 non-yaml base and no `--overlay`), read the base alone.
    - Read the overlay via `read_overlay`, unless `overlay_document` is given (D1: an in-memory draft used instead of the file). Map `OverlayError` to `ConfigurationError(str(exc))`.
    - `deep_merge` before `${NAME}` resolution and validation.
    - Relative `modules_directory` still resolves from the base file's directory, because `path.parent` is unchanged.
    - Nothing ever writes either file.
  - **`check_config(..., overlay=None, overlay_document=None)` and `run(..., overlay=None)`** pass these through to `load_config`.
  - **`main()`** gains `--overlay PATH` (optional), forwarded to both `check_config` and `run`.
  - **`LIMIT_DECLARATION`:** a public read-only view of the limit-group table, `MappingProxyType` of `MappingProxyType`s (group → field → `"count"`/`"seconds"`), with the kind constants exported as `LIMIT_KIND_COUNT`/`LIMIT_KIND_SECONDS`. `_validate_limits` keeps reading `_LIMITS`, which stays the single source.

  Caller table (from `grep -rn "load_config(\|check_config(\|await run(\|validate_schema(\|_LIMITS" --include=*.py core modules tests`; blind spot: calls through aliases or `getattr`):

  | File | Function/Method | Migration Note |
  |------|----------------|----------------|
  | core/main.py | `run` → `load_config` | Passes `overlay`; default `None` = implicit A1 path |
  | core/main.py | `check_config` → `load_config` | Passes `overlay` and `overlay_document` |
  | core/main.py | `main` → `check_config`, `run` | Forwards `--overlay` |
  | core/main.py | `_validate_limits` → `_LIMITS` | Unchanged; `LIMIT_DECLARATION` wraps the same object |
  | core/config_ui/__init__.py (P10, P12) | `check_config(base, environ=…, overlay=…, overlay_document=draft)`; `LIMIT_DECLARATION` | New callers |
  | tests/test_main.py (29 sites) | `run`, `check_config`, `load_config` | None: tmp_path profiles have no `*.local.yaml` |
  | tests/test_profiles.py (7) | `check_config`, `load_config` | None |
  | tests/test_examples.py (5) | `load_config`, `check_config` | None; `.gitignore` (P17) keeps `*.local.yaml` out of the repo. A developer's stray `config.local.yaml` beside an example would now be merged. This is documented in P18 |
  | tests/test_integration.py (2), tests/test_shutdown.py (2) | `run` | None |
  | core/contracts.py (6 internal) / core/loader.py (1) | `validate_schema` | Covered by P1 |
- **Dependencies:** [P2]
- **Tests:** In `tests/test_overlay.py`, using tmp_path fixture modules:
  - **AC9:**
    - An overlay setting an enabled fixture module's integer below `minimum` makes both `check_config` and `main([... "--check-config"])` return 2, naming the module and the field. Deleting the overlay gives 0.
    - `run` with a valid overlay value hands the module that value; a fixture module records its settings, with an injected `stop_event` and `ready_reporter`.
    - `limits.dedup.max_entries: 0` in the overlay gives 2, naming `limits.dedup.max_entries`.
    - `--overlay PATH` is honoured by `main`.
  - **AC10:** `[1, 2]` and invalid YAML with `CANARY-OVERLAY` return 2, naming the overlay file without the canary. An empty overlay equals no overlay.
  - An `overlay_document` argument overrides the file.
  - `LIMIT_DECLARATION` equals `_LIMITS` by content and is immutable.
  - Run the full `tests/test_main.py`, `tests/test_profiles.py` and `tests/test_examples.py` unchanged.
- **Risks:**
  - Any test that writes a `*.local.yaml` next to a tmp base now changes behaviour. Grep tests for `local.yaml` first.
  - Today's exact error strings (`"configuration file: is not readable"` and similar) are asserted in `tests/test_main.py`, so `read_base` must reproduce them byte-for-byte.
  - Merging before resolution means an overlay may introduce `${NAME}` references, which are resolved normally, as intended.

### P4: Status-record publisher in `run`, with collision refusal (R7, A5; AC33, AC34, AC41 runtime half, AC43)
- **Files:** [`core/main.py`, `tests/test_status_record.py`]
- **Description:** Non-trivial (ordering, atomicity, process boundary). In `core/main.py`, add a private `_StatusPublisher`:
  - **Construction** takes the status path, `started_at` (taken once at the very top of `run`, before `load_config`, as a UTC timestamp string with millisecond precision and a `Z` suffix), the digest (`core.overlay.canonical_digest` of the *merged unresolved* document the process loaded), the enabled module names, and an injectable writer seam for tests.
  - **Obtaining the digest:** `load_config` gains an internal helper `_load_documents(...) -> (merged_unresolved, config)`. `run` uses it to obtain the merged document and the resolved config without reading the files twice. The public `load_config` signature stays as in P3.
  - **`publish()`** writes one JSON object:
    - fields `version: 1`, `pid: os.getpid()`, `started_at`, `published_at` (now, same format), `sequence` (starts at 1 and adds 1 per publish), `digest`, `state: "ready"`, and `modules: {name: {"state": ...}}` for exactly the enabled modules;
    - each module's state is its last `ready`/`degraded` transition: the publisher reads `runtime.context.health.state(name)` for each enabled module at publish time and maps `stopped`/`None` to `degraded` (a decision flagged in a code comment; after readiness every enabled module has reported `ready`, because the coordinator reports readiness only after every `prepare` succeeded);
    - the write is atomic: write to a `NamedTemporaryFile` in the same directory, `flush` + `os.fsync`, then `os.replace`. On any failure the temp file is removed and the previous record is left intact.
  - **Wiring in `run`:**
    - Read `environ.get(STATUS_FILE_VARIABLE)` (the injected `environ` or `os.environ`).
    - When it is set, *before* `load_config` or any other effect, check `status_path_collision(status, base, resolve_overlay_path(base, overlay))`. On a collision, call `report_diagnostic("status_path_collision: status file collides with the <base|overlay> file <path>")` and return 2. Nothing has been opened for writing and no module has started.
    - After `report_ready("ready")`, publish once.
    - Subscribe on `runtime.bus` to `module.ready` and `module.degraded` (D5). The handler publishes a new record when an enabled module's state changes after readiness. It is wrapped so a publish failure reports a value-free diagnostic (`status record: could not be written`) and never stops the runtime.
    - Without the variable, the publisher is never constructed and no file is written.
  - Record content never includes resolved values, only the digest of the *unresolved* document.
- **Dependencies:** [P2, P3]
- **Tests:** Create `tests/test_status_record.py`, using tmp_path fixture modules M, N (and P for AC43) whose entry points expose a hook to drive `context.health.degraded/ready`:
  - **AC33 (publisher side):**
    - Three transitions produce three records, observed in order.
    - Checks per record: `version == 1`, `pid == os.getpid()`, the same `started_at`, a `published_at` timestamp, `sequence` 1 < s2 < s3, `digest == core.overlay.on_disk_digest(base, overlay)`, `state == "ready"`, keys `{M, N}`.
    - Each record also passes the reader classifier. That assertion is added in P14, when the classifier exists, as a follow-up test in `tests/test_status_record.py` listed in P14.
  - **AC34:**
    - Patch `os.replace` to raise on the second publish, then assert that the first record is intact and parseable and that no temp file is left.
    - A reader thread-free check: wrap the writer seam so that, before each `os.replace`, the test parses the current file. It must always be a complete object.
  - **AC41 runtime half:** the variable set to the base path, to the implicit overlay path while that file is absent, and to a relative spelling of an explicit `--overlay` path each give a non-zero exit and a diagnostic containing `status_path_collision`. No fixture module was activated, the base and overlay files are byte- and mtime-identical, and no overlay file was created.
  - **AC43:** the base enables M, N, P and the overlay `[M, N]`. The first record has keys `{M, N}` and the digest of that base plus overlay. Then the overlay is rewritten on disk; N degrades; the next record keeps keys `{M, N}` with N degraded and the same `started_at` and `digest`, with a greater sequence. A run enabling no module publishes `modules: {}`.
  - Without the variable, no file is created in tmp_path.
- **Risks:**
  - Bus subscription semantics: the trace may be published through supervision rather than as a bus event in some runtimes. Verify by reading `Supervision.record_and_emit` first. If the traces are not bus events, fall back to wrapping `runtime.context.health.observe_phase`/`_transition` from `main.py` only, and flag it.
  - A publish on a transition during shutdown (`stopped`) must not publish a `stopped` module state: only ready/degraded changes trigger a publish.
  - `fsync` on tmp filesystems is fine.
  - The `started_at` must be taken before anything else so AC43's "same `started_at`" holds.

### P5: Manifest titles and defaults — group 1: agent_link, audio_input, audio_output, audit (R3; AC12, AC13)
- **Files:** [`modules/agent_link/module.yaml`, `modules/audio_input/module.yaml`, `modules/audio_output/module.yaml`, `modules/audit/module.yaml`, `tests/test_manifest_presentation.py`]
- **Description:**
  - **Titles:** for each listed manifest, add a non-empty `title` to every setting node of `settings_schema`: the root, each `properties` entry recursively, and each `items` sub-schema having `properties`.
  - **Defaults:** for every node whose `description` contains `Default <literal>.` (a bare or backtick-quoted YAML value, or `empty`), add `default:` equal to that literal parsed as YAML, or the empty value of the node's type for `empty`.
  - Descriptions stay unchanged.
  - Extend `tests/test_manifest_presentation.py` with:
    - a module-level `PRESENTED` tuple listing the manifests completed so far;
    - a node walker;
    - a `Default <literal>.` extractor, using the regex ``Default (`[^`]*`|\S+?)\.(\s|$)``, with `empty` mapped by type;
    - the parametrised tests over `PRESENTED`.
- **Dependencies:** [P1]
- **Tests:** For each manifest in `PRESENTED`:
  - AC12: the count of checked nodes is > 0 and every node has a non-empty string title.
  - AC13: every documented default is present and equal; the audio_output `max_text_chars` default is `400`; every declared `default` passes `validate_schema`.
  - Run `tests/test_audio_output.py`, `tests/test_audio_input.py`, `tests/test_audit.py`, `tests/test_examples.py` and `tests/test_loader.py`.
- **Risks:**
  - Other tests compare schema nodes as exact dicts. Grep `settings_schema\]\[.properties.\]` / `== {"type"` in the tests of these modules before editing. Any failure outside the spec's allowlist must be flagged, not weakened.
  - A description with prose like "Default behaviour." is not a literal default; the extractor pattern must not match it. When in doubt, check the prose manually.
  - `audio_output` `voices` has a property named `default`. Do not confuse it with the annotation.

### P6: Manifest titles and defaults — group 2: brain, capture, chat_context, clips (R3; AC12, AC13)
- **Files:** [`modules/brain/module.yaml`, `modules/capture/module.yaml`, `modules/chat_context/module.yaml`, `modules/clips/module.yaml`, `tests/test_manifest_presentation.py`]
- **Description:** Same treatment as P5 for these four manifests: titles on every setting node, and `default` wherever `Default <literal>.` is documented. Append them to `PRESENTED`.
- **Dependencies:** [P5]
- **Tests:** The AC12/AC13 parametrised tests now cover 8 manifests. Run `tests/test_brain.py`, `tests/test_capture.py`, `tests/test_chat_context_module.py`, `tests/test_clips.py` and `tests/test_examples.py`.
- **Risks:** brain's schema is the largest (343 lines) with deep nesting, so the walker must reach `items.properties`. Also check for exact-dict schema assertions in the brain tests.

### P7: Manifest titles and defaults — group 3: kick, moderation, proxy, stream_control (R3; AC12, AC13)
- **Files:** [`modules/kick/module.yaml`, `modules/moderation/module.yaml`, `modules/proxy/module.yaml`, `modules/stream_control/module.yaml`, `tests/test_manifest_presentation.py`]
- **Description:** Same treatment as P5. These manifests carry many documented defaults (stream_control 14, moderation 11), including AC13's ``Default `alert`.`` → `"alert"` and ``Default `[delete_message]`.`` → `["delete_message"]`. Append them to `PRESENTED`.
- **Dependencies:** [P6]
- **Tests:** The parametrised AC12/AC13 tests now cover 12 manifests, plus explicit AC13 examples for the backtick-string and backtick-list defaults. Run `tests/test_kick.py`, `tests/test_moderation.py`, `tests/test_proxy.py`, `tests/test_proxy_process.py`, `tests/test_stream_control.py` and `tests/test_examples.py`.
- **Risks:** A default written in prose whose type does not match the node (e.g. `Default 30.` on a `number` node → `30` validates as number; on a `string` node it would fail). Fix the annotation, never the schema. If the prose and the schema contradict each other, flag it.

### P8: Manifest titles and defaults — group 4: twitch, users, viewer_memory, watch, youtube; allowlisted users test (R3; AC12, AC13; allowlist)
- **Files:** [`modules/twitch/module.yaml`, `modules/users/module.yaml`, `modules/viewer_memory/module.yaml`, `modules/watch/module.yaml`, `modules/youtube/module.yaml`, `tests/test_manifest_presentation.py`, `tests/test_users.py`]
- **Description:**
  - Same treatment as P5 for the last five manifests.
  - Add a completeness test asserting that `PRESENTED` equals the set of the 17 shipped manifest directories.
  - Adapt the allowlisted `tests/test_users.py::test_manifest_is_v2_and_declares_users_read_without_granting_it`. Its exact-dict assertion on `max_channels` becomes: `type == "integer"`, `minimum == 1`, a non-empty `description` and a non-empty `title`, with no other keyword outside `{type, minimum, description, title, default}`. This keeps the original checks; it is the only change the spec's allowlist permits there.
- **Dependencies:** [P7]
- **Tests:**
  - AC12 now covers all 17 (a non-zero count for each), and the completeness test covers `PRESENTED`.
  - AC13 includes a `Default true.` boolean node and a `Default empty.` array node → `[]`. Locate them via the extractor; if these shapes live in earlier groups, the explicit example test references them there.
  - Run `tests/test_twitch.py`, `tests/test_users.py`, `tests/test_viewer_memory.py`, `tests/test_watch.py`, `tests/test_youtube.py` and `tests/test_examples.py`.
- **Risks:**
  - `tests/test_examples.py` `DECLARATION_KEYS` must stay green; the edits are inside `settings_schema` only, per A6.
  - Changing `module.yaml` files can ripple into profile or fixture tests (memory: profile changes ripple into fixture tests). The `*.example` profiles are not edited here, but still run `tests/test_integration.py` and `tests/test_main.py`.

### P9: UI core — CLI, bind policy, token/session, Host/Origin/CSRF guards, startup refusals (R1, R6 startup, A8, A5 collision; AC1–AC6, AC27 startup, AC41 UI half)
- **Files:** [`core/config_ui/__init__.py`, `core/config_ui/__main__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (security boundary, branching). Creates the socket-free core (D2, D3).
  - **`UISettings.from_argv(argv)`** parses:
    - `--config` (required), `--overlay`, `--host` (default `127.0.0.1`), `--port` (default `8765`), `--allow-non-loopback`, the repeatable `--allowed-host`, and `--status-file`;
    - the launch argv after `--`, split off before argparse.
  - **`startup_checks(settings) -> list[str]`** is pure and runs before any bind:
    - non-loopback host without `--allow-non-loopback`, judged via `ipaddress` (`localhost` counts as loopback; `0.0.0.0`/`::` are non-loopback);
    - no resolvable overlay path (A1), with a diagnostic naming the base and saying that `--overlay` is required;
    - overlay equal to base (`same_file`);
    - `status_path_collision` for the explicit or derived status path, with the diagnostic `status_path_collision: … <base|overlay> <path>`, never substituting another path.
  - **`accepted_authorities(host, port, allowed_hosts) -> frozenset[str]`** per A8: lower-cased, IPv6 in brackets, port always explicit in the stored form. The printed URL uses `127.0.0.1` for a wildcard bind.
  - **`ConfigUI`** holds:
    - the token: `secrets.token_urlsafe(32)`, at least 43 characters and 256 bits;
    - the session table: a session cookie `HttpOnly; SameSite=Strict; Path=/`, set on `GET /?token=…` followed by a redirect to `/`;
    - a per-session CSRF token (`secrets.token_urlsafe(32)`), compared with `hmac.compare_digest`;
    - `handle(UIRequest) -> UIResponse`, which runs the guards in this order:
      1. a missing `Host` or one outside the accepted set → 403;
      2. no valid session → 401 with a generic body, no module names or paths;
      3. a state-changing path (`/save`, `/remove`, `/check`, `/apply`) with a non-POST method → 405;
      4. on POST, an `Origin` check (absent → allowed; `null`, another scheme, a path, or an unaccepted authority → 403; `http://h` ≡ `http://h:80`);
      5. a CSRF mismatch → 403.
    - A route table dispatches to page and endpoint handlers. P9 installs placeholders that P10–P15 replace; the base page placeholder already returns 200 so AC3 can run.
  - **`main(argv) -> int`:**
    - `startup_checks`; on failure, print the diagnostics to stderr and return 2 before creating anything;
    - otherwise build `ConfigUI`, print exactly one line `Configuration UI: http://<authority>/?token=<token>` to stdout, then `serve()` (the aiohttp adapter: `web.Application` with one catch-all handler, `web.run_app` on host and port with `print=None`).
  - **Request bridge (D8):** the catch-all handler reads the body, builds a `UIRequest` with the pure `_to_ui_request(method, path, query, headers, body)`, then `await _dispatch(ui, ui_request, executor)`, which runs `ui.handle` in the dedicated `config-ui` thread pool via `run_in_executor`, and converts the `UIResponse` back. `handle` is never called on the serving loop. `ConfigUI` gains the mutation lock and the session-table lock described in D8.
  - **Socket-free loop (D7):** define `_CappedSelector`, `_SocketFreeEventLoop` and `_run_socket_free(coro)` here, in their own delimited section, so both the bridge test (below) and Check (P12) use the one implementation.
  - The UI's logger (`core.config_ui`) never receives the token.
  - `__main__.py` calls `raise SystemExit(main())`.
- **Dependencies:** [P2]
- **Tests:** Create `tests/test_config_ui.py` with an autouse module-scoped fixture (AC5) that patches **socket creation** to raise, exactly as R5/AC5/AC23 state: `socket.socket.__init__` (every Python-level socket object, including the ones `socket.socketpair`, `socket.fromfd`, `socket.create_connection` and `socket.create_server` construct), plus `socket.socketpair`, `socket.fromfd`, `socket.create_connection` and `socket.create_server` themselves, each raising `AssertionError("socket created")` and counting the attempt. Nothing is left unpatched for asyncio: Check uses the socket-free loop of D7 (P12). A fixture self-test proves the patch is effective: `socket.socket()`, `socket.socketpair()` and `asyncio.new_event_loop()` (which needs the self-pipe) all raise inside the module, and the attempt counter is checked to be 0 at module teardown for every test that is not the self-test. Tests:
  - **AC1:** `main(["--config", p, "--host", "0.0.0.0"])`, `192.168.1.10` and `::` return non-zero with a diagnostic, and the patched socket was not called (the patch raises, and `serve` is also monkeypatched to fail the test if reached). `127.0.0.1`, `::1` and `localhost` pass `startup_checks`. With `--allow-non-loopback` a non-loopback host passes and unauthenticated requests still get 401.
  - **AC2:** two `ConfigUI` instances have different tokens of at least 22 URL-safe characters. With `serve` monkeypatched to a no-op, `main` prints the URL exactly once, captured with `capsys`, and `caplog` holds no token.
  - **AC3:** every route without a session returns 401/403 with no module name or setting path in the body. The pages are re-checked in P10/P11 once they exist.
  - **AC4:** a POST without or with a wrong CSRF token → 403 with the overlay bytes unchanged; GET on `/save`, `/remove`, `/check`, `/apply` → 405.
  - **AC6:** the full Host/Origin matrix, including the `0.0.0.0` + `--allowed-host box.lan` case and the printed URL `127.0.0.1:8765`.
  - **AC27 startup:** `--overlay` equal to base → non-zero; `config.txt` without `--overlay` → non-zero, naming the missing overlay path; with `--overlay other.local.yaml` the checks pass.
  - **AC41 UI half:** `--status-file` equal to the base, the overlay, `./sub/../config.local.yaml`, a symlink to the base, and no `--status-file` with `--overlay config.yaml.status.json` each exit non-zero with `status_path_collision`, with no bind, no Popen (monkeypatched to fail), and files byte- and mtime-identical. A non-colliding `--status-file` passes.
  - **Bridge (D8), part 1:** `_to_ui_request` maps method, path, query, headers (lower-cased names) and body exactly; `_dispatch` run on `_SocketFreeEventLoop` (introduced here as a helper for this test; the Check use is P12) returns the same `UIResponse` as a direct `handle` call and runs `handle` on a thread whose name starts with `config-ui`, not the loop thread. No socket is created (the fixture counter stays 0). P12 extends this test to a `/check` request with the real checker.
- **Risks:**
  - The UI test module contains no pytest-asyncio tests (D2): every coroutine it runs goes through `_run_socket_free`, so no fixture-provided loop tries to create a socketpair under the patch.
  - `_SocketFreeEventLoop` and `_run_socket_free` are introduced in P9 (for the bridge test) and reused by P12; defining them once avoids two loop variants.
  - Patching `socket.socket.__init__` module-wide also affects any library the UI imports lazily; if one creates a socket at import time, the test fails loudly, which is the intent.
  - A cookie session over plain http on a LAN bind is sniffable. Accepted: local-only is the documented model (R10).
  - `localhost` resolution must never use DNS; the check is by name only.
  - The argparse `--` split: argparse treats `--` specially, so split `argv` at the first `--` manually before parsing.

### P10: UI configuration model, base page and core-settings page (R4 a/c, R8, R9, A2, A3, A9; AC15, AC20, AC3 pages)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (module boundary: loader discovery; secret policy).
  - **Configuration model:** `ConfigView.load(settings, environ)` builds a snapshot:
    - reads the base (`read_base`) and the overlay (`read_overlay`) raw, and merges them (`deep_merge`);
    - records the overlay's bytes digest and mtime for stale detection (used in P13);
    - resolves `modules_directory` (merged value; `builtin` → `core.main._builtin_modules_directory()`; relative paths from the base directory);
    - discovers modules through `ModuleLoader(EventBus(), dir)._discover()` (D6);
    - collects every referenced `${NAME}` (as a value or as a mapping key), anywhere in base and overlay, with set/unset state from the UI's own environ (A3);
    - computes the **secret set**: the resolved values of those variables, the values of the variables named in `secrets`, and the literals at every declared credential path of every discovered module;
    - computes the **origin** of any setting path: `overlay` if the path exists in the overlay, else `base` if it exists in the base, else `default-not-set`. The value is shown as an `environment reference` when its configured text is `${NAME}`.
  - **Rendering helpers:**
    - `esc()` is used for every configured value, via `html.escape(quote=True)`;
    - a credential or reference value is displayed as `${NAME}` or "literal value configured (hidden)";
    - a final **redaction guard** `_redact(body)` scans every outgoing body, header, log record and report for each secret-set value (length ≥ 1) and replaces it with `[hidden]`. This is defence in depth; the renderers never place values from the secret set in the first place.
    - All CSS and JS are inline constants, forms post to relative paths, and there is no external URL anywhere (R9).
  - **Base page `/`** (R4a):
    - every discovered module (disabled ones included), each with an enabled toggle form (P13 wires the save), its readiness cell (last Check verdict, plus the status-record state from P14 once available), and a link `/module/<name>`;
    - the configuration origin: the base path, the overlay path with exists/absent, and each referenced variable with set/unset;
    - a link `/core`;
    - a local-only statement;
    - a Check button.
  - **Core-settings page `/core`** (R4c):
    - `modules_directory` (editable);
    - every group and field of `core.main.LIMIT_DECLARATION` with its kind and current value (editable);
    - `secrets` read-only: each entry as `${NAME}` with set/unset, and origin overlay when hand-written (A9);
    - `actions` read-only: every rule's `rule_id`, `action_name` (or "any"), destination, principals, natures and granted permissions, with `${NAME}` references left unresolved;
    - both read-only blocks carry the reason "read-only in v1: editing could widen the authorized actions / expose secrets";
    - an "actions not covered by any rule" list: actions declared by *enabled* modules for which no rule has an equal `action_name` or omits it.
  - The UI source never names a module (A2, R4).
- **Dependencies:** [P3, P9]
- **Tests:**
  - **AC15:** for each of the 4 `*.example` profiles, rendered with a fixture environ, the base page lists exactly the 17 discovered modules with enabled states matching `enabled_modules`; each link returns 200 (P11 makes module pages real; P10 asserts 200 via the placeholder, and P11 re-asserts the content). It shows the base path, the overlay path with absent, the referenced variables with set/unset, a `/core` link returning 200, and the local-only statement.
  - **AC20:** the `/core` page of `config.yaml.example`:
    - limit groups and fields match `LIMIT_DECLARATION` exactly (0 missing, 0 extra);
    - the rule count shown equals the merged `actions` count;
    - no `<input>`, `<select>`, `<textarea>` or save form inside the secrets and actions sections, and the reason text is present;
    - with a fixture module declaring action `x.do`, `x.do` is listed as not covered, and adding a covering rule removes it.
  - **AC3 pages:** with the real pages, an unauthenticated GET of `/` and `/core` contains no module name.
  - **A9:** a hand-written overlay `actions` rule appears with origin overlay.
- **Risks:**
  - `_discover()` raises `ModuleLoadError` when one manifest is invalid. The UI must still render the base page with the diagnostic (value-free) instead of crashing.
  - Redacting very short secret values (e.g. `"1"`) would mangle pages. The guard redacts only values ≥ 4 characters. Shorter secrets are still never rendered because the renderers show references only. The minimum length is documented in the code.
  - Reference keys used as mapping keys (the trigger channel key) must be collected as references.

### P11: Generated module pages — settings controls and trigger policies (R4 b, R4 subset, A2, A7; AC16–AC19, AC21 values, AC36 escaping)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (branching schema renderer). Adds `/module/<name>`, which renders the module's `settings_schema` recursively over the subset `type`, `properties`, `required`, `additionalProperties`, `enum`, `items`, `minimum` and `maximum`:
  - **Controls:**
    - string → text input;
    - integer/number → number input with `min`/`max` and step 1 for integer;
    - boolean → checkbox;
    - enum → select with exactly its members;
    - array with `items` → a list editor (JSON text area validated server-side);
    - object with `properties` → a fieldset recursion;
    - object with a schema `additionalProperties` → a named-entries editor;
    - `additionalProperties: false` → no extra-key control.
  - **Each field shows:** its `title` label, the description as help text, the documented default, the current value, its origin (P10), a required marker, and a remove-override control only when the origin is overlay.
  - **"Not editable here" notices** name the setting path and show the unresolved configured text read-only (never a secret value: credential paths show "literal value configured (hidden)"). They cover:
    - an object without `properties` and without a schema `additionalProperties`;
    - an array without `items`;
    - type `null`;
    - the reserved `limits` property;
    - a manifest without `settings_schema`.
  - **Trigger section:** rendered only when the manifest declares trigger types.
    - Per configured channel of `triggers.<module>.channels`, it shows the combination operator (select limited to the module's supported operators) and its rules, each rule's parameters rendered from that type's `parameter_schema` with the same renderer.
    - A channel key that is a `${NAME}` reference is shown as that reference text.
    - An "add channel policy" form offers exactly the declared rule types.
    - A channel defined in the base shows no delete action and the note "the overlay cannot delete a base entry" (A7).
  - Every configured value is escaped text, never an attribute URL (R9).
- **Dependencies:** [P8, P10]
- **Tests:**
  - **AC16:** for each of the 17 modules, the set of setting paths shown (controls plus notices, collected from `data-path` attributes) equals the schema's path set, with `limits` shown as "not editable here"; titles appear as labels and defaults are shown.
  - **AC17:** a fixture module added only as a directory with `module.yaml` gets a page with its titled fields. A static test reads `core/config_ui/*.py` and asserts that none of the 17 module names appears as a string literal (via `ast` string constants).
  - **AC18:** a fixture schema with an object without `properties`, an array without `items` and a `null` field gives three notices naming their paths; an enum offers exactly its members; `min="1" max="10"` is present; the required marker is shown.
  - **AC19:** the twitch page with `config.yaml.example` shows channel `${TWITCH_BROADCASTER_ID}`, `all_of`, one `keyword` rule with `["!ask"]`, and rule types exactly `{probability, audience, keyword}`; a module without trigger types shows no trigger section.
  - **AC21 (value/origin part):** fixture M with base `{a:1, c:"${VAR_C}", d:5}` and overlay `{a:2}` shows `a` = 2 (overlay), `c` as `${VAR_C}` with set/unset and never the value, `d` = 5 (base), and `b` default-not-set with default 5.
  - **AC36 escaping:** the value `https://cdn.example.invalid/x.js"><script>` appears as `&lt;script&gt;` and appears in no `src`/`href`/`action` attribute.
  - **AC3:** an unauthenticated module page → 401 without the module name.
- **Risks:**
  - Setting-path identity for AC16: paths through `items` and `additionalProperties` entries need a stable notation (`a.b[]`, `a.<entry>`); define it once and use it both in the renderer and in the test's schema walker.
  - Deeply nested brain schemas could produce huge pages; acceptable.

### P12: Check — draft validation through `check_config` (R5; AC22, AC38, AC23)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (module boundary into `core.main`). Adds `POST /check` from the base, core and module pages:
  - The draft overlay is the on-disk overlay with the posted unsaved edits applied, using the same path-setting routine Save uses, factored as `_apply_edits(overlay, edits)`.
  - **Unresolved-reference phase:** the draft is merged over the base and scanned for `${NAME}` references in the top-level blocks (excluding `modules` and `secrets`) and in the settings of *enabled* modules (by `enabled_modules` of the merged draft). Every reference whose variable is absent from the UI environ is reported as `<setting path>: ${NAME} is unresolved` (a variable name is not a secret). If any is reported, the result also says "settings phase not reached" and `check_config` is not called.
  - **Settings phase:** otherwise, the default checker calls `_run_socket_free(check_config(base, environ=ui_environ, overlay=overlay_path, overlay_document=draft, diagnostic_reporter=collect))` (D7, defined in P9) and collects every diagnostic. It never calls `asyncio.run`, so no self-pipe `socketpair` is created. Before running, it refuses with `RuntimeError("Check must not run on a running event loop")` if its thread already has a running loop (D8 guard); in production it always runs on a `config-ui` worker thread reached through `_dispatch`. An injectable `checker` seam exists for the AC22 unit cases, but AC38, AC23 and the bridge test use the **real** default checker.
  - **Module pages** show the diagnostics naming that module: the text contains `modules.<name>.` or starts with `<name>:` / `module <name>`, via a shared `_diagnostic_names_module` predicate. The base page shows all.
  - The last Check verdict is stored per UI for readiness (R4).
  - Diagnostics pass through the redaction guard.
- **Dependencies:** [P3, P11]
- **Tests:**
  - **AC22:**
    - 3 unresolved references across two enabled fixture modules → 3 diagnostics with path and variable, plus "settings phase not reached".
    - 0 unresolved and 2 invalid settings in two modules → both diagnostics in one Check.
    - A module page lists only its own; the base page lists all.
  - **AC38:** a passing on-disk config plus a draft below `minimum` → a failure naming the module and setting; a failing on-disk overlay plus a draft fixing it → pass; the overlay bytes are unchanged in both cases.
  - **AC23:** the overlay bytes and mtime are unchanged, `subprocess.Popen` and `os.kill` are monkeypatched to fail if called, and the socket patch stays active.
  - AC38 and AC23 run the real default checker (no seam) under P9's socket-creation patch, and assert the fixture's socket-creation counter is 0 afterwards: Check creates no socket at all, not merely none bound or connected.
  - **D7 pin:** `asyncio.SelectorEventLoop` defines `_make_self_pipe`, `_close_self_pipe` and `_write_to_self`; `_run_socket_free` of a coroutine that awaits `asyncio.sleep(0)`, a `loop.run_in_executor` call (a cross-thread `call_soon_threadsafe` wake-up) and an `asyncio.wait_for` with a timeout completes, closes the loop, and creates no socket.
  - **Bridge (D8), part 2 — the real adapter-to-checker path (findings P2):** on `_SocketFreeEventLoop` (standing in for the aiohttp serving loop, which the tests cannot bind), `await _dispatch(ui, <authenticated POST /check with a valid CSRF token>, executor)` with the real checker returns the AC38 verdict (a pass for a valid draft, the module/setting failure for an invalid one). This proves that Check runs from inside a running loop's handler without the "cannot be called from a running event loop" failure. A companion test calls the default checker directly from a coroutine on a running loop and asserts the D8 `RuntimeError`, proving the guard.
- **Risks:**
  - D7 relies on three private CPython `BaseSelectorEventLoop` methods. The pin test above fails loudly on an interpreter that changes them; the fallback would be the same override under the new names, never re-enabling the socketpair. Flagged in the P19 review summary.
  - The 10 ms selector cap makes the Check loop poll. Check is short-lived (bounded by `core.main`'s startup deadline), so the cost is negligible, and it is the price of having no self-pipe.
  - `check_config` may start the runtime's collaborators; any of them that creates a socket at assembly time would now be caught by the patch. `check_config`'s own docstring (AC40 of an earlier phase) states assembly opens nothing, so a failure here is a real bug to report, not a patch to relax.
  - Diagnostic-to-module attribution is text-based and may miss a module-wide diagnostic phrased differently. Enumerate the diagnostic shapes from `core/loader.py` (`_field_diagnostic`, `_module_diagnostic`) and base the predicate on them.

### P13: Save and remove-override — scope, protections, validation, stale detection, atomic write, audit log (R6, A7, A9; AC24–AC28, AC10 base unchanged)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (irreversible write to an operator file). Adds `POST /save` and `POST /remove`:
  - **Scope whitelist** of the target key paths:
    - `enabled_modules`;
    - `modules.<name>.<setting path>` (a discovered module);
    - `triggers.<input>.channels.<channel>` (a whole channel policy, where `<input>` is a discovered module declaring trigger types);
    - `limits.<group>.<field>` (in `LIMIT_DECLARATION`);
    - `modules_directory`.
    - Everything else is refused: `secrets`, `actions`, any other top-level key.
  - **Protections:** the target is refused if it is a declared credential path or a field whose configured (merged, unresolved) text is `${NAME}`. For an ancestor target, every protected descendant (credential paths, `${NAME}` values, `${NAME}` mapping keys) must keep exactly its current configured text in the new value (for Save) and must not be deleted (for Remove: removing an override that holds a protected descendant is refused when the effective text would change). Otherwise the operation is refused and changes nothing.
  - **Validation** returns per-field diagnostics and writes nothing:
    - a module setting against its schema node, via `core.contracts` value validation;
    - a trigger policy's rules: the declared type, its `parameter_schema`, and the operator in the supported set;
    - a limit's kind (positive int / finite positive number, excluding bool);
    - `modules_directory` a non-empty string;
    - `enabled_modules` a list of distinct discovered names.
  - **Stale detection:** each rendered form carries the overlay fingerprint (SHA-256 of the file's bytes, or `absent`). A Save or Remove whose fingerprint differs from the current file is refused as stale.
  - **Write:**
    - the new overlay = the current overlay with the edit applied; for Remove, the key deleted and emptied parent mappings pruned;
    - `secrets`/`actions` and every other hand-written key are preserved as parsed (A9);
    - serialized with `yaml.safe_dump(sort_keys=False, allow_unicode=True)`;
    - written atomically: a temp file in the overlay's directory, `fsync`, then `os.replace`;
    - the write target is asserted to be the managed overlay path resolved at startup, and never the base.
  - **Log:** each accepted operation emits one `logging` record on `core.config_ui` with time, page, operation and the touched setting paths, never values.
- **Dependencies:** [P12]
- **Tests:**
  - **AC24:** each of the five saves (module setting, disabling a module, `limits.dedup.max_entries: 2048`, `modules_directory: ./other` with the base page then listing that directory's modules, a new `triggers.twitch.channels.chan2` with a `probability` 0.5 rule) produces an overlay with exactly that override plus the previous ones; `load_config` returns the new value; there is one log record with the paths and no value.
  - **AC25:** removing `modules.M.S` restores the base value and prunes `modules.M`/`modules`; a base-defined channel policy offers no delete action and states the reason.
  - **AC26:** each invalid save (out-of-range, wrong type, `max_entries: 0`, `ttl_seconds: "x"`, an empty `modules_directory`, a duplicate or unknown enabled name, an undeclared rule type, `probability: 1.5`, an unsupported operator) returns a per-field diagnostic with the overlay bytes unchanged; a stale save is refused.
  - **AC27:**
    - writes to `secrets`, `actions` (add, change, remove), another top-level key, a credential field or a `${NAME}` field are all refused with no file change;
    - a hand-written `actions` rule keeps the same parsed value after an unrelated save;
    - the ancestor cases for `modules.M.o`: `{k:"${VAR_K}", n:2}` accepted and keeping the reference; `{k:"plain", n:2}` and `{n:2}` refused;
    - the credential-literal ancestor remove is refused;
    - the twitch `${TWITCH_BROADCASTER_ID}` channel-policy save keeps the key as reference text.
  - **AC28:** `os.replace` patched to raise leaves the previous overlay intact and parseable, with no temp file left.
  - **AC10:** after every AC22–AC28 operation, the base bytes are unchanged (asserted in a shared fixture teardown).
- **Risks:**
  - YAML round-tripping loses the operator's comments and formatting in the overlay. Accepted: the overlay is UI-managed, and this is documented in P18.
  - `yaml.safe_dump` of a `${NAME}` string must quote it correctly, and a round-trip must preserve it.
  - The ancestor rule for `${NAME}` mapping keys (channel keys) must compare *key text*, not resolved values.
  - A TOCTOU race between the fingerprint check and the replace is accepted (single local operator).

### P14: Status-record reader, drift and running state (R7 reader side, R4 readiness; AC39, AC40, AC42, AC21 readiness, AC31 display, AC32 display, AC33 reader check)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`, `tests/test_status_record.py`]
- **Description:** Non-trivial (parsing untrusted input, classification branching).
  - **`read_status(path) -> StatusReading`**, where `StatusReading` is one of `absent`, `unusable(reason)` or `usable(record)`:
    - `os.stat` → a regular file ≤ 1 MiB, otherwise unusable;
    - read the bytes; UTF-8 strict decoding;
    - `json.loads` with `object_pairs_hook`, which rejects duplicate keys, and `parse_constant`, which rejects `NaN`/`Infinity`/`-Infinity`;
    - the top level must be a dict holding all 8 required fields;
    - typed checks:
      - integer = `type(v) is int` (excludes bool and float); `version == 1`; `pid` in 1..2147483647; `sequence` in 1..2^53−1;
      - timestamps by regex `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$` plus `datetime` construction for calendar validity;
      - `digest` by `^[0-9a-f]{64}$`;
      - `state == "ready"`;
      - `modules` a dict with non-empty string keys whose values are dicts with `state` in `{ready, degraded}`.
    - Extra keys are ignored.
    - The reason is value-free: the failing check name, the field name and the expected type or set.
    - No coercion, no partial trust.
  - **Liveness:** `os.kill(pid, 0)`: `ProcessLookupError` → dead; success or `PermissionError` → alive. For the UI's own child, `Popen.poll()` is authoritative. Both are behind an injectable `probe` seam.
  - **Drift:**
    - `unknown` for absent, unusable or dead records;
    - otherwise `in sync` if `record.digest == core.overlay.on_disk_digest(base, overlay)`, else `differs`.
  - **Running state:** per module, from the usable, live record's container. A module with no entry shows "not reported by the running process"; entries naming no discovered module are not displayed. The time shown is `published_at`.
  - **Supervision label:** a record whose pid is not the launched child's pid → "not supervised".
  - Every page (base, core, module) shows the record status ("no status record" / "status record unusable: <reason>" / the record time), the drift state and the supervision label. The base page's readiness column combines the last Check verdict and the running state; the module page shows it too.
  - Add the reader-side assertion to AC33 in `tests/test_status_record.py`: each of the 3 published records classifies as usable.
- **Dependencies:** [P4, P13] (P13 because the AC32 display test performs an overlay Save, and because P12, P13 and P14 all edit `core/config_ui/__init__.py` and `tests/test_config_ui.py`, so they must run in that order; P13 transitively brings P11 and P12)
- **Tests:**
  - **AC39:** a record with mode 000 (skipped when running as root, with the reason stated), a directory at the path, non-JSON bytes, `[1,2]`, a missing digest, and pid `"123"` with a matching digest. For each, every page returns 200 and shows "status record unusable" with a reason different from the "no status record" text and containing none of the record's bytes; drift is unknown and no running state is shown.
  - **AC40:**
    - the exact mutation list (8 removals; the version, pid, started_at, published_at, sequence, digest, state and modules mutations; duplicate pid; NaN; 1 MiB + 1 byte), each unusable, never "in sync", pid never displayed;
    - V usable, and V with extra keys usable with the same display;
    - V with a dead pid → unknown (the probe seam returns dead).
  - **AC42:** records with `modules` `{}`, one with an extra `Z`, `sequence` 7, and `started_at` later than `published_at`: all usable and "in sync"; M/N show "not reported by the running process" where they have no entry; Z appears nowhere. With a differing digest → "differs".
  - **AC21 readiness:** M degraded in a record → the base page shows degraded plus the record time; with no record → only the Check verdict plus "no running process is known".
  - **AC31 display:** a record from a live foreign pid (the test process's own pid, which is not a child) shows per-module state labelled "not supervised"; a record at another path is not read; the default path is `config.yaml.status.json`; `--status-file` overrides it.
  - **AC32 display:** a matching digest → "in sync" on all pages; after an overlay save changing a value → "differs"; no record or a dead pid → "unknown". The Apply leg is in P15.
- **Risks:**
  - `json.loads` accepts `1.0` as float and `1` as int. `type(v) is int` correctly rejects `1.0`, `1e0` and `true`.
  - An oversized file must be checked by `stat` *before* reading, with the read also capped at 1 MiB + 1 byte to handle a race.
  - Mode-000 tests fail as root. Add a `pytest.skip` only for `os.geteuid() == 0`, alongside the directory case, which always runs.

### P15: Apply — supervised restart (R7 Apply, A4; AC29, AC30, AC31 signals, AC32 apply, AC39/AC40 apply legs)
- **Files:** [`core/config_ui/__init__.py`, `tests/test_config_ui.py`]
- **Description:** Non-trivial (process boundary, signals, bounded waits). Adds `Supervisor` and `POST /apply`:
  - **Launch argv:** the argv given after `--`, or by default `[sys.executable, "-m", "core.main", "--config", <base>]` plus `["--overlay", <overlay>]` when `--overlay` was given.
  - **Apply sequence:**
    1. Run an on-disk Check (P12 with no edits). On failure, refuse with its diagnostics; nothing is stopped or started.
    2. Stop the child *this UI launched*, if any: `terminate()`, then `wait(timeout=stop_grace)` (default 10 s, injectable), then `kill()` and wait. A record's foreign pid is never signalled (A4).
    3. Start `Popen(argv, shell=False, env={**os.environ, STATUS_FILE_VARIABLE: status_path}, stdout=None, stderr=PIPE, start_new_session=False)` — stdout inherited, so it can never fill a pipe (D9) — and immediately start the child's `_StderrDrain` thread (below) before polling.
    4. Poll within the window (default 60 s, `apply_window` injectable) using an injected `clock` and `wait` (the real `wait` = `threading.Event().wait(interval)`, never `time.sleep`; tests inject a fake clock):
       - **accepted:** a usable status record whose `pid == child.pid`, `state == ready` and `digest ==` the on-disk digest;
       - **refused:** `child.poll()` is not `None`; join the drain thread (bounded, 2 s, so EOF has been read), then report the exit status and the last 2 KiB of the drained stderr tail, passed through the redaction guard and value-free;
       - **unknown:** the window elapsed. An unusable record never counts.
  - **`_StderrDrain` (D9):** a daemon `threading.Thread` per child that loops `os.read(fd, 4096)` on the stderr pipe until EOF, writes each chunk to the UI's `sys.stderr.buffer` (a write failure is ignored for forwarding only; draining never stops early), and appends it to a bounded tail `bytearray` trimmed to the last 8 KiB under a lock. `tail(limit)` returns the decoded (`errors="replace"`) last `limit` bytes. It runs for the child's whole life, including after an accepted Apply, so a chatty runtime never blocks on a full pipe. Stopping a child (step 2 of a later Apply, or UI shutdown) waits for the process, then joins its drain with a bounded timeout; the pipe is closed after the join.
  - The restart report is shown on the page and logged (paths and outcome only).
  - `/apply` requires POST + CSRF (P9).
- **Dependencies:** [P13, P14]
- **Tests:** Fake child scripts written to tmp_path and run with `sys.executable`; each writes the status record via `core.overlay` helpers.
  - **AC29:** a failing on-disk Check → refused; the `Popen` spy was never called and the existing child was not terminated.
  - **AC30:**
    - a child writing a ready record with its own pid and the on-disk digest → accepted;
    - a child exiting 2 after printing a value-free line → refused with status 2 and that line;
    - a child writing nothing within a 1 s window → unknown;
    - an argv element `;echo x` reaches the child literally (the child echoes `sys.argv` into a file).
  - **AC31:** a child ignoring SIGTERM (`signal.signal(SIGTERM, SIG_IGN)`) is killed after the bounded wait, recorded via a spy on `terminate`/`kill` and on the order of calls; a foreign-pid record → `os.kill` spy never called with that pid.
  - **AC32 apply:** after a successful Apply, pages show "in sync".
  - **Pipe backpressure (findings P4):**
    - a child that writes 1 MiB to stderr and 1 MiB to stdout (each far above the 64 KiB Linux pipe capacity) *before* writing its ready record → accepted within the 1 s window, which would be `unknown` if stderr were not drained;
    - a child that, after its ready record, keeps writing 1 MiB to stderr and then writes a marker file → after accepted, the marker file appears within a bounded poll (`Event.wait` loop ≤ 5 s), proving draining continues after acceptance;
    - a child that writes 1 MiB of filler then the line `LAST-LINE` to stderr and exits 2 → refused with status 2, the report contains `LAST-LINE`, and the report's stderr part is ≤ 2 KiB;
    - `_StderrDrain` unit test on an `os.pipe()` pair (no socket): 100 KiB written, tail length stays ≤ 8 KiB and ends with the last bytes written.
  - **AC39/AC40 apply legs:** a child writing only an unusable record, V with `state: "starting"`, or V with another pid → unknown after a 1 s window, never accepted.
  - The waits use a real bounded `Event.wait` with a short interval. The hygiene sleep check forbids `sleep` calls only, and `Event.wait` is not one. Confirm with `test_ac45_the_suite_makes_no_positive_duration_sleep_call`'s implementation before relying on this.
- **Risks:**
  - Real child processes make tests slower and potentially flaky. Keep the windows at ~1 s and the children trivial.
  - `Popen` itself does not create sockets, so the AC5 patch holds.
  - Zombie children: always `wait()` after a kill.
  - The child's stderr may contain a secret if a child misbehaves. Pass the reported tail through the redaction guard and cap it at 2 KiB. Forwarding to the UI's own stderr is equivalent to the operator running the runtime directly (the runtime's diagnostics are value-free by contract), and it goes to the terminal, never into a page or the UI's log records.
  - Forwarded child output lands in pytest's fd capture during tests; that capture is a temp file and cannot back-pressure.
  - A drain thread blocked on a pipe whose write end a grandchild still holds could outlive the child; the join is bounded and the thread is a daemon, so the UI never hangs on it.
  - The spec's shutdown watchdog in `core.main` bounds how long terminate takes. The default `stop_grace` of 10 s exceeds the core shutdown deadline, so the value is aligned in the doc.

### P16: Secret-canary sweep and offline-assets audit (R8, R9; AC35, AC36 URLs, AC2 log)
- **Files:** [`tests/test_config_ui.py`, `core/config_ui/__init__.py`]
- **Description:**
  - Add the end-to-end canary test:
    - a fixture environment gives every variable referenced in the 4 example profiles (including `secrets` entries and the trigger channel key variable) a value `CANARY-ENV-<hex>-<name>`;
    - a fixture overlay puts a literal canary at a declared credential path of an enabled module;
    - a `caplog`/handler captures every `core.config_ui` record.
  - The test drives, through `ConfigUI.handle`:
    - the base page, the core page, all 17 module pages and every JSON endpoint;
    - one Check, one Save, one Remove;
    - an Apply accepted and an Apply refused, using the P15 fake children;
    - and it reads every AC33 status record, re-running P4's publisher with the canary env.
  - It asserts that 0 bodies, headers, log records, restart reports or status records contain any canary, and that credential fields show `${NAME}` or "literal value configured (hidden)".
  - Add the AC36 URL audit: parse every response with `html.parser`; every `src`/`href`/`action` value and every CSS `url(`/`@import` target is relative or points to an accepted authority; the inline script contains no `http://`/`https://` literal to another host.
  - Fix any leak found in `__init__.py`; this is the only reason this step may touch production code.
- **Dependencies:** [P15]
- **Tests:** AC35 and AC36 (attribute and CSS URL audit) as described; AC2's "token in no log record" re-checked over the whole sweep's log capture.
- **Risks:**
  - A canary that is a substring of page chrome is impossible by construction (random hex).
  - The publisher's records hold no resolved values, since the digest is over unresolved text; the check proves it.
  - If a leak is found, fix the renderer rather than relying only on the redaction guard.

### P17: Packaging — UI console script, `.gitignore`, allowlisted pyproject test (R1 console script, R9; AC36 pyproject, AC11)
- **Files:** [`pyproject.toml`, `.gitignore`, `tests/test_main.py`]
- **Description:**
  - Add `twitch-ia-compagnon-config-ui = "core.config_ui:main"` under `[project.scripts]`. `[project].dependencies` stays exactly `aiohttp` and `PyYAML`.
  - Append to `.gitignore`: `*.local.yaml` and `*.status.json`.
  - Adapt the allowlisted `tests/test_main.py::test_pyproject_declares_the_console_script_and_the_build_backend_for_tests`: the scripts assertion becomes an exact equality with the two entries `{"twitch-ia-compagnon": "core.main:main", "twitch-ia-compagnon-config-ui": "core.config_ui:main"}`. This is still exact and not loosened; it is the change the spec allowlists. The build-backend and dependency assertions stay unchanged.
  - `core.config_ui:main` must accept `argv=None` and return an int. P9 defines it that way.
- **Dependencies:** [P9]
- **Tests:**
  - The adapted pyproject test.
  - A new assertion in `tests/test_config_ui.py` is not needed: AC36's dependency half is covered by the existing test.
  - If `tests/test_main.py` has an install test that builds the distribution (the AC43 of a prior phase), run it to confirm `core.config_ui` is packaged.
- **Risks:** An existing install or entry-point test may enumerate the console scripts; grep `console_scripts`/`entry_points` in the tests first.

### P18: Operator documentation (R10; AC37)
- **Files:** [`docs/config-ui.md`, `tests/test_config_ui.py`]
- **Description:** Write `docs/config-ui.md` covering:
  - launching with `python -m core.config_ui --config config.yaml` (and the console script), the options, and a launch argv after `--`;
  - that the UI is **local-only** and does not administer a remote brain over the proxy; the token and the printed URL;
  - the overlay path rule (A1), with `config.yaml → config.local.yaml` and `presence.yaml.example → presence.local.yaml`, and `--overlay`;
  - the merge precedence (R2/AC7 example) and that the overlay cannot delete a base key (A7);
  - the 5 writable blocks (`enabled_modules`, `modules.<name>`, `triggers`, `limits`, `modules_directory`) and the 2 read-only blocks (`secrets`, `actions`), with the reason (6c: a mis-click must never widen authorized actions or expose a secret);
  - that the overlay is rewritten by the UI, so comments are not preserved;
  - the secrets policy (R8);
  - restart and supervision (A4, the stop grace, the 60 s window, the accepted/refused/unknown outcomes);
  - the drift states in sync / differs / unknown, and "status record unusable";
  - the status-file variable `TWITCH_IA_COMPAGNON_STATUS_FILE`, the default status path `<base file name>.status.json`, the collision refusal, and how to make an externally started main process visible (set the variable to the UI's status path; it is then shown "not supervised");
  - that a stray `*.local.yaml` next to a profile is merged by `--check-config` too.
- **Dependencies:** [P16] (P16 and P18 both edit `tests/test_config_ui.py`; P18 runs after P16 so the two edits are ordered, and P16 transitively brings P15)
- **Tests:** An AC37 doc test in `tests/test_config_ui.py` asserts that the file contains:
  - `python -m core.config_ui` and `local-only`/`local only`;
  - both AC8 examples;
  - the AC7 merge example;
  - the "cannot delete" statement;
  - the 5 writable and 2 read-only block names with a reason;
  - the secrets-policy text;
  - `in sync`, `differs`, `unknown`;
  - `core.overlay.STATUS_FILE_VARIABLE`, read from the module (not a literal), and `.status.json`.
- **Risks:** Documentation drifts from the constants. Reading the variable name from `core.overlay` in the test pins it.

### P19: Full-suite regression and full-branch review gate (AC11; all R1–R10)
- **Files:** [`spec.md`, `plan.md`]
- **Description:**
  - **Suite:** run `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` with no `*.local.yaml` present in the repository. Require at least 2,460 passed and at most 18 skipped, plus the tests added in this phase, with no failure outside the two allowlisted tests.
  - **Full-branch review:** review `git diff main...HEAD` as one change set, across all files together. Per-commit reviews miss cross-file bugs by construction: each step can be correct in isolation while the combination is wrong. Examples:
    - P4's digest and P14's drift both depend on P2's `canonical_digest` being fed the *same* merged unresolved document;
    - P12's Check and P13's Save must share `_apply_edits`, or Check validates a different draft from the one Save writes;
    - P9's socket-creation patch must still hold after P12/P15 add event loops and subprocesses (the fixture's creation counter is 0 across the whole module);
    - `serve()`'s handler must reach `handle` only through `_dispatch` (D8): grep `core/config_ui/__init__.py` for any `ui.handle(`/`self.handle(` call inside an `async def`, and for any `asyncio.run(`;
    - every `Popen` in the supervisor must be paired with a started `_StderrDrain` (D9);
    - the redaction guard (P10) must cover the P15 restart reports;
    - the P5–P8 manifest edits must not break P11's path-set equality.
  - **Gate checklist:**
    - every AC1–AC43 has a named test;
    - `core/config_ui` names no module (AC17);
    - no write path other than the overlay, the status record, and the UI's own temp files;
    - no external URL;
    - `[project].dependencies` unchanged;
    - the caller table in P3 still matches a fresh grep of the call sites;
    - `spec.md` targets equal the files touched (`git diff --name-only main...HEAD`, excluding `spec.md`/`plan.md`).
  - Record any deviation (e.g. the D1–D9 decisions, D7's reliance on private CPython loop methods, the P4 fallback if D5 did not hold) in the review summary. `spec.md`/`plan.md` are updated only to record such a deviation, never to relax a requirement.
- **Dependencies:** [P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14, P15, P16, P17, P18]
- **Tests:** The full suite (AC11); the gate checklist above, with each item's evidence (test name or grep output) attached.
- **Risks:**
  - Suite runtime grows with the real subprocess tests in P15. Keep the windows short.
  - A flaky subprocess test must be fixed deterministically (injected clock), never retried or skipped.

## Requirement coverage

| Requirement | Steps |
|---|---|
| R1 | P9 (guards, bind, token, socket-creation patch for AC5), P17 (console script), P16 (token not logged) |
| R2 | P2 (merge, path, read), P3 (runtime and CLI), P13 (base never written) |
| R3 | P1 (validator), P5–P8 (17 manifests), P8 (allowlisted users test) |
| R4 | P10 (base and core pages), P11 (module pages and triggers), P14 (readiness and running state) |
| R5 | P9 (socket-free loop, request bridge), P12 (Check) |
| R6 | P9 (startup refusals), P13 (save and remove) |
| R7 | P2 (digest, collision helper), P4 (publisher, runtime collision), P9 (UI collision), P14 (reader and drift), P15 (Apply) |
| R8 | P10 (secret set, redaction), P16 (canary sweep) |
| R9 | P10/P11 (inline assets, escaping), P16 (URL audit), P17 (dependencies) |
| R10 | P18 |

## Ordering rationale

- **P1 and P2 come first** because everything else depends on them and they depend on nothing: the `default` annotation must be accepted before any manifest carries one, and `core/overlay.py` is the single shared implementation both the runtime and the UI import.
- **P3 follows P2** because the runtime merge is P2's first consumer and it establishes the `overlay`/`overlay_document` API (D1) that the UI's Check needs.
- **P4 comes after P3** because the publisher needs the merged unresolved document that P3's loading path produces, and the collision helper from P2.
- **P5–P8 run after P1**, since manifests with `default` fail validation without it. They are split into four groups so each iteration touches a bounded set of manifests. `PRESENTED` grows monotonically so every intermediate state is green, and P8 closes the set at 17.
- **The UI builds upward:**
  - P9 (guards) first, since every later endpoint relies on it and its socket-free fixture;
  - then P10 (model and pages that need the loader and P3's `LIMIT_DECLARATION`);
  - then P11 (module pages, which reuse P10's model and depend on P8 so AC16's all-17 label assertion sees titled manifests);
  - then P12 (Check, which needs pages to post from and P3's `overlay_document`);
  - then P13 (Save, which reuses P12's `_apply_edits` and validation);
  - then P14 (the reader, which needs P4's record format and P11's pages);
  - then P15 (Apply, which needs Check, Save for the drift scenario, and the reader for the "accepted" outcome).
- **Shared-file ordering.** Steps that edit the same file are chained by explicit dependencies so a sequential dev loop never interleaves them: `core/main.py` (P3 → P4), `tests/test_manifest_presentation.py` (P1 → P5 → P6 → P7 → P8), `tests/test_status_record.py` (P4 → P14), and `core/config_ui/__init__.py` plus `tests/test_config_ui.py` (P9 → P10 → P11 → P12 → P13 → P14 → P15 → P16 → P18). P14 depends on P13 (not only P11) because its AC32 display test saves the overlay; P18 depends on P16 (not only P15) because both edit `tests/test_config_ui.py`.
- **P16 comes after all surfaces exist**, because the canary sweep must cover every page, endpoint, report and record.
- **P17 needs only P9's `main`.** It is placed late to keep the allowlisted `tests/test_main.py` edit next to the finished UI.
- **P18 documents final behaviour and constants**, after P16's last production fix.
- **P19 is the full-branch gate**, last by construction.

## Findings addressed

| Finding | Where addressed |
|---|---|
| P1 (blocker) — socket-creation prohibition weakened | D7; P9 fixture now patches socket *creation* (`socket.socket.__init__`, `socketpair`, `fromfd`, `create_connection`, `create_server`) with a self-test and a zero-creation counter; P12 runs the real checker through the socket-free `_run_socket_free` loop instead of `asyncio.run`, with AC38/AC23 on the real path and a pin test for the private loop hooks |
| P2 — synchronous Check inside the aiohttp handler | D8; P9 bridge (`_to_ui_request`, `_dispatch` via a dedicated thread pool, locks); P12 bridge test drives a real `/check` through `_dispatch` from a running loop, plus a guard test proving Check refuses to run on a running loop |
| P3 — shared-file and Save ordering | P14 dependencies `[P4, P13]`; P18 dependencies `[P16]`; "Shared-file ordering" in the ordering rationale |
| P4 — child output backpressure | D9; P15 stdout inherited, stderr drained continuously by `_StderrDrain` with a bounded tail, and four backpressure tests (1 MiB before ready, output after acceptance, tail on refusal, drain unit test) |
