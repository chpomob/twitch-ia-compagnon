# P19F3 — The last three: a silent language fallback, an allowlist overrun, and new skips

The gate's second pass resolved the two credential leaks and left three MEDIUM findings. This step closes
them. Nothing else changes.

## N1 — a missing French key silently renders English

`core/config_ui/i18n.py:136`, `:152` and `core/config_ui/wizard.py:1964`. Executed: with `language=fr`
persisted and the key `wizard.screen2.title` removed from the French table, the loader detects the key-set
difference (`"key set differs (missing [...])"`) and the UI nevertheless answers `200` and renders the
**English** title — silently switching an existing French session.

Required: a session whose language is French never renders an English sentence. An incomplete table is a
**startup refusal** with a plain cause (which table, how many keys are missing, nothing else), not a
fallback; and a language switch to a table that does not validate is refused with the same cause, leaving
the session's language unchanged. The English table's completeness is checked the same way.

Tests: the gate's reproduction asserts a non-200 (or a refusal page) and **zero** English sentences
rendered; a complete table still switches languages; a missing key in the French table refuses the switch
and keeps `fr`.

## N2 — existing expectations changed beyond the allowlist

The AST comparison reports eight changed existing test functions, zero deletions, and some changes outside
the allowlist. Re-run that comparison yourself, name every changed function, and for each either restore
the baseline assertion sequence (carrying the change through a faithful fake) or add it to the allowlist
with a one-line reason that points at the requirement forcing it. No loosening: a count, a constant or an
added assertion only.

## N3 — new skips violate the closing invariant

The audit finds one added `pytest.mark.skipif` alias used on 18 new functions, plus one added
`pytest.skip` call. The phase's invariant is that no skip is added. The platform branches are already
exercised on Linux by forcing the platform value, so the skip has no job: remove the alias and the marker
uses, and make those tests run by forcing the platform. If a single case genuinely cannot run anywhere,
say which and why in your reply — but the default suite must contain no new skip.

## Acceptance

- The N1 reproduction renders zero English sentences in a French session and refuses an incomplete table.
- The AST comparison is clean outside the allowlist; the allowlist's new entries (if any) are named with
  their requirement.
- No added `skip`, `skipif`, `xfail` or `importorskip` anywhere in `tests/`.
- Full suite green on the project interpreter and the 3.10 venv:
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider` and
  `~/.hermes/cache/scratch/venv310/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Reply with the N1 reproduction's output, the eight changed functions and their disposition, the skip
  count after the change, and both suite counts.
