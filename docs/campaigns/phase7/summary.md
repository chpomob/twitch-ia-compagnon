# Phase 7 — summary: web knowledge, evidence ledger and allowlist audit

Step P11 of `docs/campaigns/phase7/plan.md` (R10; AC26's summary half).
Authority: `docs/campaigns/phase7/spec.md` (R1–R10, AC1–AC26), `docs/campaigns/phase7/brief.md`
and its binding decisions, `docs/design-v2.md`.

- Pre-phase tree: `7c29434` (`docs(phase7): the approved specification, …`).
- Evidence head: `174ab51` (P10, the last code commit on `main` before this summary). Every commit
  hash below is a `main` commit. A gate fix round (P12) that adds a closing commit updates this
  ledger before the final gate pass.
- The gate half of AC26 (the full-branch review ending `APPROVE`, with an executed and failing
  counterexample for R4, R7 and R8) is P12's, not this document's.

## Result in one paragraph

The companion has a `web` module with two read actions, `web.search` (Wikipedia by default with no
key, Brave as an option) and `web.read` (one public page as bounded plain text). Every requirement
R1–R9 is carried by at least one commit on `main` and named tests in `tests/test_web.py` that pass
at the evidence head; the scripted check below confirms that every cited test id is collected and
every cited commit exists. The full suite is green (see [Suite count](#suite-count)). The allowlist
audit finds **no added `skip`/`xfail` marker and no loosened assertion** in an existing test; it
also finds **two existing tests outside the allowlist** whose assertions changed (both in P1, neither
loosened), plus three environment-conditional `pytest.skip` calls inside one new test — recorded in
[the audit](#allowlist-audit-git-diff-7c29434----tests) for the gate, not hidden.

## Requirement ledger (R1–R9)

Commits are the plan steps: `a518ab2` P1 (manifest, settings, lifecycle), `c3e3a57` P2 (network
guard), `7858b08` P3 (extractor, charsets), `6356326` P4 (guarded transport), `9a77c85` P5
(`web.read`), `e44493f` P6 (`web.search`), `c7346aa` P7 (cache), `05938f9` P8 (untrusted marker
through the brain), `fe4f058` P9 (cross-surface R7 scan), `174ab51` P10 (guides, README). The
commit named against a test is the one that introduced it (`git log -S "def <name>("`). A bare
function name covers every parametrization.

| Req. | Commit(s) | Test id(s) (`tests/test_web.py::…`) | ACs |
|---|---|---|---|
| R1 one module, two reads, provider-neutral | `a518ab2`, `9a77c85`, `e44493f` | `tests/test_web.py::test_ac1_the_shipped_manifests_discover_19_modules_with_web`, `tests/test_web.py::test_ac1_both_actions_are_reads_on_web_with_closed_bounded_arguments`, `tests/test_web.py::test_ac1_the_manifest_declares_no_grant_and_only_the_v2_keys`, `tests/test_web.py::test_ac1_the_result_schemas_are_provider_neutral_and_require_the_marker` (`a518ab2`); `tests/test_web.py::test_ac1_with_no_rule_the_read_provider_is_never_invoked`, `tests/test_web.py::test_ac2_invalid_url_arguments_are_refused_with_no_request` (`9a77c85`); `tests/test_web.py::test_ac2_invalid_query_arguments_are_refused_with_no_request`, `tests/test_web.py::test_ac3_the_default_profile_searches_the_english_wiki_once_with_no_key`, `tests/test_web.py::test_ac3_brave_sends_one_request_with_the_key_and_none_to_wikipedia`, `tests/test_web.py::test_r1_a_backend_is_a_name_a_request_builder_and_a_parser` (`e44493f`); `tests/test_web.py::test_ac3_brave_with_an_unset_key_reference_names_brave_api_key`, `tests/test_web.py::test_ac3_a_refused_brave_profile_never_echoes_the_key`, `tests/test_web.py::test_ac4_activate_prepare_and_ready_open_no_connection` (`a518ab2`) | AC1–AC4 |
| R2 bounded, plain search results | `e44493f`, `7858b08` | `tests/test_web.py::test_ac5_mediawiki_results_are_bounded_plain_and_in_order`, `tests/test_web.py::test_r2_a_wikipedia_title_is_percent_encoded_as_wikipedia_serves_it`, `tests/test_web.py::test_ac6_a_brave_result_without_a_url_is_dropped_and_counted`, `tests/test_web.py::test_ac6_a_brave_answer_that_is_not_the_documented_shape_is_malformed`, `tests/test_web.py::test_r2_a_malformed_single_result_is_dropped_never_presented`, `tests/test_web.py::test_r2_an_answer_cut_by_the_byte_bound_is_malformed_not_partial` (`e44493f`); `tests/test_web.py::test_r3_character_references_decode_and_surrogates_are_replaced` (`7858b08`, the shared markup/entity path) | AC5, AC6 |
| R3 bounded text of one public page | `7858b08`, `9a77c85` | `tests/test_web.py::test_ac7_the_basic_page_keeps_three_paragraphs_and_no_skipped_byte`, `tests/test_web.py::test_ac7_the_header_charset_decodes_latin1`, `tests/test_web.py::test_ac7_the_meta_charset_decodes_latin1_when_the_header_names_none`, `tests/test_web.py::test_ac7_noscript_and_template_content_is_absent_however_nested`, `tests/test_web.py::test_ac8_a_20000_byte_text_is_cut_to_8192_valid_utf8_bytes` (`7858b08`); `tests/test_web.py::test_ac8_a_20000_byte_page_is_cut_to_8192_valid_utf8_bytes`, `tests/test_web.py::test_ac8_a_1000_byte_page_is_whole_and_not_truncated`, `tests/test_web.py::test_ac8_an_unreadable_content_type_is_refused_with_0_reads`, `tests/test_web.py::test_r3_each_readable_content_type_is_read` (`9a77c85`) | AC7, AC8 |
| R4 one network guard at every hop | `c3e3a57`, `6356326`, `9a77c85`, `e44493f` | `tests/test_web.py::test_ac9_every_literal_is_refused_blocked_address_with_no_resolution`, `tests/test_web.py::test_ac9_a_name_with_one_private_answer_is_refused`, `tests/test_web.py::test_r4_the_explicit_table_refuses_embedded_and_reserved_addresses`, `tests/test_web.py::test_ac10_unsupported_schemes_and_urls_are_refused_before_any_lookup`, `tests/test_web.py::test_ac12_allowed_hosts_is_an_exact_case_insensitive_match` (`c3e3a57`); `tests/test_web.py::test_ac10_a_rebinding_name_is_connected_only_at_its_checked_address`, `tests/test_web.py::test_ac11_a_redirect_to_loopback_is_refused_after_one_connection`, `tests/test_web.py::test_ac11_four_redirects_over_a_limit_of_three_make_four_connections`, `tests/test_web.py::test_ac11_every_redirect_hop_is_re_checked_in_full`, `tests/test_web.py::test_ac13_no_proxy_from_the_environment_is_ever_reached`, `tests/test_web.py::test_ac13_a_3_mib_body_is_consumed_to_the_bound_plus_one_and_truncated`, `tests/test_web.py::test_ac13_a_body_that_never_arrives_times_out_at_the_setting`, `tests/test_web.py::test_r4_the_pinned_resolver_answers_only_the_pinned_addresses` (`6356326`); `tests/test_web.py::test_ac9_every_literal_is_refused_blocked_address_end_to_end`, `tests/test_web.py::test_ac10_a_rebinding_name_never_reaches_loopback_end_to_end`, `tests/test_web.py::test_ac11_redirects_end_to_end`, `tests/test_web.py::test_ac12_allowed_hosts_end_to_end`, `tests/test_web.py::test_ac13_a_whole_1_mib_html_body_with_1000_bytes_of_text_is_not_truncated`, `tests/test_web.py::test_ac13_a_20000_byte_text_plain_body_yields_exactly_8192_bytes` (`9a77c85`); `tests/test_web.py::test_ac12_a_search_reaches_its_provider_outside_the_allowlist`, `tests/test_web.py::test_r4_the_provider_endpoint_passes_the_address_guard`, `tests/test_web.py::test_r4_a_provider_redirect_off_origin_drops_the_key_and_is_rechecked` (`e44493f`) | AC9–AC13 |
| R5 validated bounds, cache bounded three ways | `a518ab2`, `9a77c85`, `c7346aa` | `tests/test_web.py::test_ac14_every_r5_default_is_the_schema_default_and_in_range`, `tests/test_web.py::test_ac14_check_config_refuses_each_out_of_range_setting_naming_it`, `tests/test_web.py::test_r5_the_hook_refuses_each_field_by_name_and_value_free` (`a518ab2`); `tests/test_web.py::test_ac17_a_call_beyond_the_concurrency_bound_is_resource_busy`, `tests/test_web.py::test_r5_a_failed_call_releases_its_slot` (`9a77c85`); `tests/test_web.py::test_ac15_an_identical_search_within_the_ttl_is_served_from_the_cache`, `tests/test_web.py::test_ac15_an_entry_older_than_the_ttl_is_never_served`, `tests/test_web.py::test_ac15_a_ttl_of_zero_disables_the_cache`, `tests/test_web.py::test_ac15_a_failure_is_never_served_from_the_cache`, `tests/test_web.py::test_ac16_the_cache_holds_at_most_max_entries_least_recent_evicted`, `tests/test_web.py::test_ac16_stored_content_stays_within_max_bytes_over_20_reads`, `tests/test_web.py::test_ac16_an_entry_is_accounted_as_the_whole_observation`, `tests/test_web.py::test_ac16_a_read_and_a_search_of_the_same_string_are_distinct_entries` (`c7346aa`) | AC14–AC17 |
| R6 honest outcome classification | `6356326`, `9a77c85`, `e44493f` | `tests/test_web.py::test_r6_a_transport_failure_is_transport_failed_value_free` (`6356326`); `tests/test_web.py::test_ac18_each_http_status_row_of_a_read`, `tests/test_web.py::test_ac18_a_connection_reset_is_error_transport_failed_value_free`, `tests/test_web.py::test_r6_a_body_the_extractor_cannot_parse_is_error_malformed_response`, `tests/test_web.py::test_r6_one_classifier_answers_both_actions` (`9a77c85`); `tests/test_web.py::test_ac18_each_http_status_row_of_a_brave_search`, `tests/test_web.py::test_ac18_a_well_formed_empty_mediawiki_list_is_a_success_with_0_results`, `tests/test_web.py::test_ac18_a_stalled_provider_body_is_timeout_timed_out`, `tests/test_web.py::test_ac18_the_twelve_rows_walked_once_only_the_last_is_a_success` (`e44493f`) | AC18 |
| R7 no query, URL path/query, key or page text recorded | `6356326`, `9a77c85`, `e44493f`, `c7346aa`, `fe4f058` | `tests/test_web.py::test_r7_a_fetched_answer_never_renders_its_url_or_body` (`6356326`); `tests/test_web.py::test_r7_the_read_trace_carries_counts_and_lengths_only` (`9a77c85`); `tests/test_web.py::test_r7_the_search_trace_carries_counts_and_lengths_only` (`e44493f`); `tests/test_web.py::test_r7_the_cache_never_renders_a_key_or_an_entry` (`c7346aa`); `tests/test_web.py::test_ac19_nothing_web_emits_or_records_carries_the_query_url_key_or_text`, `tests/test_web.py::test_ac19_a_search_for_wikipedia_records_the_query_only_as_fixed_vocabulary`, `tests/test_web.py::test_ac19_the_scan_fails_on_a_query_injected_into_a_log_line`, `tests/test_web.py::test_ac19_the_fake_texts_share_no_8_byte_run_with_the_fixed_vocabulary`, `tests/test_web.py::test_ac20_debug_lengths_adds_byte_lengths_and_nothing_else`, `tests/test_web.py::test_r7_the_settings_repr_holds_no_configured_value` (`fe4f058`) | AC19, AC20 |
| R8 fetched text is untrusted data, grants nothing | `a518ab2`, `05938f9` | `tests/test_web.py::test_ac1_the_result_schemas_are_provider_neutral_and_require_the_marker` (`a518ab2`); `tests/test_web.py::test_r8_every_result_key_of_both_schemas_sorts_after_advisory`, `tests/test_web.py::test_ac21_ac22_an_injection_page_is_framed_data_and_grants_nothing`, `tests/test_web.py::test_ac21_the_framing_check_fails_on_page_text_in_a_text_part` (`05938f9`) | AC21, AC22 |
| R9 on and off by configuration; documented | `a518ab2`, `174ab51` | `tests/test_web.py::test_r9_pyproject_ships_the_web_manifest` (`a518ab2`); `tests/test_web.py::test_ac23_the_grant_fragment_checks_with_exactly_two_web_rules`, `tests/test_web.py::test_ac23_deleting_the_two_rules_leaves_the_brain_no_web_tool`, `tests/test_web.py::test_ac23_the_brave_fragment_checks_only_with_the_key_exported`, `tests/test_web.py::test_ac23_the_french_guide_carries_the_same_fragments`, `tests/test_web.py::test_ac24_the_english_guide_is_normative_and_the_french_one_defers`, `tests/test_web.py::test_ac24_the_settings_table_equals_the_manifest`, `tests/test_web.py::test_ac24_the_readme_lists_the_capability_and_links_the_guide`, `tests/test_web.py::test_ac24_pyproject_declares_exactly_two_runtime_dependencies`, `tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base` (`174ab51`) | AC23, AC24 |

R10 (for completeness; not part of AC26's R1–R9 mapping): the socket guard
`tests/test_web.py::test_the_socket_guard_refuses_and_records_every_non_loopback_surface` (`a518ab2`),
`tests/test_web.py::test_ac25_activate_and_prepare_with_no_seams_open_nothing` (`6356326`),
`tests/test_web.py::test_ac25_both_guides_document_the_operator_trial_and_its_record`,
`tests/test_web.py::test_ac25_every_activation_here_passes_the_transport_seam`,
`tests/test_web.py::test_ac25_no_other_test_file_imports_the_web_module` (`174ab51`); this summary
(P11) and the full-branch review (P12).

## What the companion can now do

- **Look things up.** Once the operator inserts the two grant rules, the brain may call
  `web.search`: on Wikipedia by default (the configured language's wiki, no key, no account), or on
  Brave Search when the operator configures a key. It gets at most `max_results` results, each a
  title, a URL and a plain-text excerpt of at most `max_excerpt_bytes` bytes.
- **Read a public page.** `web.read` fetches one `http(s)` page — HTML, XHTML or plain text — and
  returns its final URL after redirects, its title and at most `max_text_bytes` bytes of extracted
  text, with `truncated` saying whether a bound cut it.
- **Cite its source.** Every search result and every read carries the URL it came from, so the
  companion can name where an answer was found.
- **Safely by construction.** Every request goes through one network guard (public addresses only,
  pinned connection, every redirect re-checked, no proxy from the environment, streamed byte bound,
  one call deadline); every bound is a validated setting; one bounded TTL cache serves both actions;
  the query, URL paths and query strings, the key and page text are never recorded; fetched text
  reaches the model framed as untrusted data and grants nothing.
- **On and off by configuration alone.** Two rules turn it on, deleting them turns it off; no code
  change, no shipped profile changed.

## What it still cannot do, or what is not proven

- **No rendering, no JavaScript.** A page whose content is built by scripts yields only what its
  HTML carries; nothing is rendered and no script runs. Only `text/html`, `application/xhtml+xml`
  and `text/plain` are read — PDFs and every other type are refused.
- **No summarisation by the module.** It returns bounded extracted text and excerpts as they are;
  any summary is the model's, from that text. No content is filtered, rewritten or scored.
- **The real providers are proven against fakes only.** Wikipedia and Brave are exercised by the
  suite against fake transports and recorded-shape fixtures; no test contacts a real provider.
- **No socket added.** This phase adds no socket of any kind; the suite's only connections are the
  pre-existing loopback ones in `test_kick.py`. The socket guard's own self-test exercises the
  patched `connect`/`connect_ex` on a stand-in object, never a kernel socket.
- **NAT64 and 6to4 are not specially handled.** `64:ff9b::/96` and `2002::/16` addresses are not
  unwrapped to the IPv4 they embed; they are judged by the platform's `is_global` (so
  `64:ff9b::7f00:1` is not refused as loopback). The guide states this limit.
- **Real-socket pinning is proven by unit checks and the operator trial only**, never by a test
  against a real connection.
- **No trial record yet.** The real-instance operator trial (Wikipedia; Brave when a key exists) is a
  manual procedure in the guide; `docs/campaigns/phase7/web-trial-record.md` does not exist yet. The
  phase does not depend on it (R10); until it exists, the providers are proven against fakes only.

## Enabling Brave (repeated verbatim from `docs/web-knowledge.md`, "Enabling Brave")

Brave is optional; Wikipedia keeps working without it. To switch `web.search` to Brave:

1. Obtain a key: subscribe to the Brave Search API at `https://brave.com/search/api/` and copy the
   subscription key from its dashboard.
2. Export it in the environment the companion runs in — never write it in the profile:

   ```bash
   export BRAVE_API_KEY=…
   ```

3. With the grant fragment already inserted, insert this fragment (same insertion rule): it sets
   `provider: brave` and `brave_api_key: ${BRAVE_API_KEY}`.

```yaml
modules:
  web:
    provider: brave
    brave_api_key: ${BRAVE_API_KEY}
```

4. Validate, and read the exit status: `0` is accepted; non-zero names `brave_api_key` when the
   variable is not exported.

   ```bash
   python -m core.main --config my-config.yaml --check-config
   ```

The key is a credential: it is redacted wherever it would appear, the configuration UI shows only
whether the reference is set, and the module sends it only in the request header to the Brave
endpoint. To go back to Wikipedia, delete both lines — `provider: brave` and
`brave_api_key: ${BRAVE_API_KEY}`. Setting only `provider: wikipedia` keeps the key reference, which
is still resolved at startup: once `BRAVE_API_KEY` is no longer exported, startup and
`--check-config` refuse the profile naming `brave_api_key`.

(The guide's `<!-- web-knowledge:brave-fragment -->` anchor is left out here so that the AC23 test,
which extracts the fragment by that anchor from the guides, has exactly one source.)

## Suite count

`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` at the evidence head plus this summary:

```
4324 passed, 26 skipped in 302.84s (0:05:02)
```

The step's floor is 3,124 passed / 18 skipped (the count recorded at the start of the phase); the
pass count is above it. The 26 skips in this environment are all pre-existing and environmental:
re-run with `-rs`, they are 6 `test_phase2_trials.py`, 6 `test_phase3_trials.py` and 8
`test_phase5_trials.py` opt-in real-instance trials (variables unset), 5 `test_capture.py` cases
(Linux process groups, `/proc`, pidfd) and 1 `test_profiles.py` case (this virtualenv has no `pip`,
so `test_installed_distribution_discovers_the_shipped_manifests_and_answers_help`, one of the
allowlisted tests, skips here). The same 26 reasons skip on the `7c29434` tree run with the same
virtualenv (that run, from a `git archive` extraction, adds only 2 skips and 3 failures of tests
that need a git work tree). `tests/test_web.py` alone: 416 passed, 0 skipped. Phase 7 adds no skip
that fires here.

## Evidence check (scripted)

The check reads this file, extracts every cited `tests/…::name` id and every backticked commit
hash, and verifies each id is collected by `pytest --collect-only` (a parametrized test matches by
its function name) and each hash names a commit (`git cat-file -e <hash>^{commit}`). Run from the
repository root:

```bash
.venv/bin/python - <<'EOF'
import re, subprocess, sys
text = open("docs/campaigns/phase7/summary.md", encoding="utf-8").read()
ids = sorted(set(re.findall(r"tests/test_\w+\.py::\w+", text)))
hashes = sorted(set(re.findall(r"`([0-9a-f]{7,40})`", text)))
collected = subprocess.run(
    [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
     *sorted({i.split("::")[0] for i in ids})],
    capture_output=True, text=True).stdout.splitlines()
names = {line.split("[")[0] for line in collected if "::" in line}
missing = [i for i in ids if i not in names]
bad = [h for h in hashes
       if subprocess.run(["git", "cat-file", "-e", f"{h}^{{commit}}"]).returncode != 0]
print(f"test ids cited: {len(ids)}, not collected: {len(missing)} {missing}")
print(f"commits cited: {len(hashes)}, missing: {len(bad)} {bad}")
sys.exit(1 if missing or bad else 0)
EOF
```

Output at the evidence head:

```
test ids cited: 109, not collected: 0 []
commits cited: 11, missing: 0 []
```

## Allowlist audit (`git diff 7c29434 -- tests`)

15 files, +6,062 / −15 lines: `tests/test_web.py` (new, +5,462), 9 new fixture files under
`tests/fixtures/web/` (each with its source in `PROVENANCE.md`), and 5 existing test files.
Method: every `+` line searched for `skip`, `xfail`, `importorskip`; every `-` line read (all 15
shown by `git diff -U0 7c29434 -- tests`, none in `tests/test_web.py`, which is new), and each
touched existing test compared before/after.

**No `skip`/`xfail` marker added, no existing test skipped.** The `+` lines matching `skip` are
test names and docstrings (`…_skipped_byte`, `…_skipped_element_…`), `# fmt: skip` formatter
comments, a JSON-scanner helper named `skip`, and — the one real hit — three
environment-conditional `pytest.skip(...)` calls inside the new
`tests/test_web.py::test_ac24_the_shipped_profiles_have_zero_changed_lines_since_the_phase_base`
(P10), taken only when `git` is missing, the tree is not a work tree, or `7c29434` is absent from
the clone. None fires here (the test passes; `tests/test_web.py` has 0 skips). Recorded
for the gate: it is a new test, not a loosened one, but it is a skip path a shallow clone would take.

**No deleted assertion, no loosened assertion.** The 15 removed lines are 8 count equalities
replaced by the same equality with the new count (18 → 19 manifests, 14 → 16 actions), 5
docstring/comment lines extended, the `GUIDES` tuple (extended by `web-knowledge`), and the module-name regex of
`test_a2` (below).

**Allowlisted tests (6 in the specification), each changed and citing phase 7 R1/R9:**

| Test | Change |
|---|---|
| `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | 18 → 19 manifests, 14 → 16 declared actions; module constants (`MODULE_NAMES`, `EXPECTED_MANIFESTS`, `DECLARATION_KEYS`, `DECLARED_ACTIONS`, `SECRET_SETTINGS`) gain exact `web` entries; every equality still exact |
| `tests/test_manifest_presentation.py::test_presented_covers_every_shipped_manifest` | 18 → 19; `PRESENTED` gains `web` |
| `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | 18 → 19; `SHIPPED_MANIFESTS` gains `web` |
| `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | 18 → 19 manifests and package-data lines; the dependency assertion unchanged |
| `tests/test_config_ui.py::test_ac15_the_base_page_lists_every_module_and_the_origin` | 18 → 19 |
| `tests/test_docs_bilingual.py::test_the_discovered_pairs_are_exactly_the_declared_guides` | `GUIDES` gains `web-knowledge` (P10) |

**Outside the allowlist — recorded for the gate (P12), not hidden.** Two existing tests changed in
P1 that the specification's allowlist does not name. Neither is loosened in its count or set
equalities; both carry a comment or docstring citing phase 7 R1/R9 (plan P1):

| Test | Step | Change | Loosened? |
|---|---|---|---|
| `tests/test_examples.py::test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario` | P1 | catalog size 14 → 16 and the exact catalog set gains `web.search`, `web.read` — it fails for the same reason as the allowlisted count tests (a 19th manifest declaring two actions) | no — still exact |
| `tests/test_config_ui.py::test_a2_the_ui_source_names_no_module` | P1 | the bare-word module-name match now excludes `web` (the word is also aiohttp's `web` package and "web page"), and a new pattern `WEB_MODULE_FORMS` checks every form that would name the module (`"web"`, `'web'`, `modules.web`, `modules/web`, `web.search`, `web.read`, `/module/web`) in every string token and the whole source | no assertion removed; for `web` alone the check is narrower than a bare `\bweb\b` would be (a bare word `web` in a comment is no longer caught) — for the gate to judge |

Either the gate accepts these as consequences of the 19th manifest, or the allowlist in `spec.md` is
amended to name them; P11 cannot do either (its only file is this summary).
