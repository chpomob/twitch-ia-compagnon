# P19F4 — One named exemption for POSIX-only subjects, and behaviour tests that run everywhere

The third loop of P19F3 was rejected on a disagreement this step settles, and the settlement is now written
into the specification as **Amendment A2** (the "Test failure allowlist" section). Read it first.

## The disagreement, and the decision

P19F3 replaced the `posix_only` marker with a fixture that calls `pytest.fail` when native POSIX facilities
are missing. The reviewer refused: on Windows that turns "unsupported" into "red", and the specification's
no-skip rule was being satisfied by making the suite fail on a platform the project does not support.

Amendment A2 decides instead:
- **one named exemption class** — a single marker, with a reason, used only on tests whose *subject* is a
  POSIX-only facility (file modes, owners, per-user directory checks);
- it is **enumerated in one place**, and an invariant test asserts its use count and its node list, so
  adding one is a visible diff;
- every test of platform **behaviour** (the Windows directory and ACL branches, the launcher's per-platform
  contents) must run on any platform by forcing the platform value — no exemption there;
- every other `skip`, `skipif`, `xfail` and `importorskip` stays forbidden.

## What to do

1. Restore the single `posix_only` marker (or an equivalent single name) and apply it only to the tests
   whose subject is a POSIX facility. Remove the `pytest.fail` fixture P19F3 introduced for this purpose.
2. Keep the one `pytest.skip` call only if its test is such a subject; otherwise make it run.
3. Split the 18 uses: for each, say whether its subject is a POSIX facility (exempted) or platform
   behaviour (must run by forcing the platform value). Convert the second class.
4. Add the invariant test: the exempted node list is exactly what the marker covers, the count matches, and
   a scan finds no other skip/xfail/importorskip in `tests/`.
5. The N1 and N2 work P19F3 did stays: the language fallback refuses instead of rendering English, and the
   assertion audit's disposition is unchanged — only the N3 treatment changes.

## Acceptance

- The invariant test passes and would fail if a node were added to or removed from the exemption list.
- No `skipif`/`skip`/`xfail`/`importorskip` exists outside the single exemption class.
- The platform-behaviour tests run on this platform (Linux) with the platform value forced, and their names
  are listed in your reply.
- Full suite green on the project interpreter and the 3.10 venv, with both summary lines.
- Reply with the split (which nodes are exempted and why, which were converted), the invariant test's name,
  the scan's count, and both suite counts.
