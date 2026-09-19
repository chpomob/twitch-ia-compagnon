#!/usr/bin/env python3
"""Phase 1 campaign runner: sequential adversarial code loops P1..P23, then the full-branch gate.

DURABLE COPY — lives inside the repository (`docs/campaigns/phase1/scaffolding/`) because the
2026-09-16 reboot wiped /tmp and destroyed the previous runner, step specs and logs.

Roles (user-requested pairing, never switched silently):
  DEV    = Claude via the claude-tmux wrapper (never --model, never `claude -p`)
  REVIEW = Codex via codex-loop-review.sh (git-diff capable)

Policy:
  - Claude-only DEV: if Claude's interactive window or its WEEKLY ceiling is exhausted the runner
    WAITS (no silent switch to another family; DEV_FALLBACK=glm opts in explicitly).
  - `claude -p` must never be used: it is billed per token instead of using the subscription.
    Liveness comes from the free usage API; the interactive probe is the fallback only.
  - The REVIEWER is a hard requirement for every step: probe it first and wait for its reset.
  - Quota-killed step -> wait for the announced reset, retry the SAME step (max attempts per step).
  - DEV inactivity timeout -> refresh the token, drop the tmux server, retry.
  - Anything else -> stop, write the summary (net stop, never a silent provider switch).
  - After the last step, run the full-branch gate (P24): on APPROVE, done; on REQUEST_CHANGES the
    report is written and the runner stops so the findings can be decomposed into fix steps.

Resume: START_AT=P<n> skips the earlier steps.
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/.hermes/plugins/hermes-quota-status"))
try:
    import quota_api                      # free usage API: no message, no billing
except Exception:                         # pragma: no cover - plugin may be absent
    quota_api = None

REPO = "/media/chpo/HDD-papa/twitch-ia-compagnon"
CAMPAIGN = pathlib.Path(f"{REPO}/docs/campaigns/phase1/scaffolding")
STEPS_DIR = CAMPAIGN / "steps"
LOGS = CAMPAIGN / "logs"
LOOP = os.path.expanduser("~/.hermes/skills/adversarial-code-loop/scripts/adversarial_loop.py")
CLAUDE = os.path.expanduser("~/claude-tmux-wrapper/claude-tmux.py")
CODEX_REVIEW = "/home/chpo/.config/adversarial/codex-loop-review.sh"
REFRESH = os.path.expanduser(
    "~/.hermes/skills/adversarial-campaign-orchestration/scripts/refresh-claude-oauth.py")
CREDS = os.path.expanduser("~/.claude/.credentials.json")
CAMPAIGN_LOG = CAMPAIGN / "campaign.log"
SUMMARY = CAMPAIGN / "campaign-summary.md"
GATE_PROMPT_1 = CAMPAIGN / "gate-prompt-1.md"

TEST_CMD = "python3 -m pytest tests/ -q -p no:cacheprovider"
STEP_IDS = [f"P{i}" for i in range(1, 24)]        # P24 is the standalone full-branch gate
DEV_CLAUDE_TUI = f"python3 {CLAUDE} --timeout 2700 --hard-timeout 3900 --cwd {REPO}"
DEV_GLM = "pi -p --provider zai --model glm-5.3 --thinking high"
CLAUDE_LOOP_TIMEOUT = 4800
GLM_LOOP_TIMEOUT = 7200
DEV_FALLBACK = os.environ.get("DEV_FALLBACK", "") == "glm"   # Claude-only unless explicitly allowed
MAX_ATTEMPTS_PER_STEP = 4
WAIT_HOURS = float(os.environ.get("WAIT_HOURS", "14"))       # raise it to survive a weekly-limit wait
PROBE_EVERY = 600


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    CAMPAIGN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CAMPAIGN_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fresh_env():
    """Env carrying the CURRENT credentials.json token.

    The CLI reads CLAUDE_CODE_OAUTH_TOKEN in priority over credentials.json, so a stale exported
    variable silently shadows a fresh file (401 while the file is valid). Never trust the parent env.
    """
    env = {**os.environ}
    try:
        tok = json.load(open(CREDS))["claudeAiOauth"].get("accessToken")
        if tok:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    except Exception:
        pass
    return env


def sh(cmd, cwd=None, timeout=None):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=fresh_env())


def _reset_epoch(m):
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=int(m.group(2)), second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target.timestamp()


def codex_ok():
    """Reviewer liveness. The limit markers are checked FIRST: codex echoes the prompt, so a
    marker-only test (`'CODEX_OK' in output`) is a guaranteed false positive on a failed run."""
    r = sh('cd /tmp && timeout 180 codex exec --dangerously-bypass-approvals-and-sandbox '
           '--skip-git-repo-check --sandbox workspace-write "Reply with exactly: CODEX_OK" < /dev/null',
           timeout=240)
    out = ((r.stdout or "") + (r.stderr or ""))
    low = out.lower()
    m = re.search(r"try again at (\d{1,2}):(\d{2})\s*(AM|PM)", out, re.I)
    if "usage limit" in low or "rate limit" in low or m:
        return False, _reset_epoch(m)
    if "CODEX_OK" in out and "ERROR" not in out:
        return True, None
    return False, None


def claude_ok():
    """Interactive-path liveness.

    NEVER use `claude -p` here: it is billed per token, not covered by the subscription. The free
    usage API is the primary signal; the tmux wrapper probe is the fallback when the API is
    unavailable, and it spends one subscription message.
    """
    if quota_api is not None:
        try:
            q = quota_api.fetch_claude_quota() or {}
        except Exception:
            q = {}
        pct = q.get("session_pct")
        weekly = q.get("weekly_pct")
        if weekly is not None and float(weekly) >= 97.0:
            log(f"claude WEEKLY limit at {weekly}% (resets {q.get('weekly_reset')}) — DEV blocked")
            return False
        if pct is not None:
            if float(pct) < 97.0:
                return True
            log(f"claude session window at {pct}% — waiting for its reset")
            return False
    sh("tmux kill-server 2>/dev/null; true", timeout=15)   # frozen tmux env -> 401 panes
    r = sh(f"printf 'Reply with exactly: CLAUDE_OK' | timeout 260 python3 {CLAUDE} "
           "--max-turns 1 --hard-timeout 180", timeout=300)
    return "CLAUDE_OK" in ((r.stdout or "") + (r.stderr or ""))


def wait_for_dev(deadline):
    while time.time() < deadline:
        ok, reset = codex_ok()
        if not ok:
            when = datetime.datetime.fromtimestamp(reset).strftime("%H:%M") if reset else "?"
            log(f"reviewer (Codex) unavailable — reset {when}; sleeping {PROBE_EVERY//60} min")
            time.sleep(PROBE_EVERY)
            continue
        if claude_ok():
            return "tui"
        if DEV_FALLBACK:
            g = sh(f'{DEV_GLM} "Reply with exactly: GLM_OK"', timeout=300)
            if "GLM_OK" in ((g.stdout or "") + (g.stderr or "")):
                return "glm"
        else:
            sh(f"python3 {REFRESH}", timeout=240)
        log(f"DEV (Claude) unavailable — sleeping {PROBE_EVERY//60} min"
            + ("" if DEV_FALLBACK else " (Claude-only: no fallback)"))
        time.sleep(PROBE_EVERY)
    return None


def tests_pass():
    r = sh(f"timeout 900 {TEST_CMD}", cwd=REPO, timeout=960)
    tail = [l for l in ((r.stdout or "") + (r.stderr or "")).strip().splitlines() if l.strip()][-1:]
    return r.returncode == 0, " ".join(tail)


def step_title(step):
    try:
        first = (STEPS_DIR / f"{step}-spec.md").read_text(encoding="utf-8").splitlines()[0]
        return first.split("—", 1)[1].strip() if "—" in first else first.lstrip("# ").strip()
    except Exception:
        return step


def conventional_message(step):
    """Commit subject for the step.

    A spec may pin it with a line `commit: <subject>` (used for FIX rounds whose type differs from
    the plan step's). Otherwise derive `feat(phase1): <step> — <plan title>`.
    """
    try:
        text = (STEPS_DIR / f"{step}-spec.md").read_text(encoding="utf-8")
    except Exception:
        text = ""
    for line in text.splitlines()[:12]:
        if line.strip().lower().startswith("commit:"):
            return line.split(":", 1)[1].strip()
    title = re.split(r"\s+\(", step_title(step), maxsplit=1)[0].strip()
    return f"feat(phase1): {step} — {title}"


def run_step(step, dev_cmd, loop_timeout):
    spec = STEPS_DIR / f"{step}-spec.md"
    feature = f"phase1-{step.lower()}"
    cmd = (
        f'python3 {LOOP} --spec {spec} --workdir {REPO} --feature {feature} '
        f'--dev-cmd "{dev_cmd}" --review-cmd "bash {CODEX_REVIEW}" '
        f'--test-cmd "{TEST_CMD}" --timeout {loop_timeout} --max-loops 3 --no-arbiter '
        f"--out {CAMPAIGN}/loop-out"
    )
    LOGS.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True,
                       timeout=14400, env=fresh_env())
    out = (r.stdout or "") + (r.stderr or "")
    (LOGS / f"{step}.log").write_text(out, encoding="utf-8")
    return r.returncode, out


def approve(step):
    # Pass the message through a FILE: a title containing backticks would otherwise be mangled by
    # shell command substitution in `git commit -m "..."`.
    msg_file = CAMPAIGN / f"commit-msg-{step}.txt"
    msg_file.write_text(conventional_message(step) + "\n", encoding="utf-8")
    sh(f'git commit --amend -q -F "{msg_file}"', cwd=REPO)
    head = sh("git log --oneline -1", cwd=REPO).stdout.strip()
    ok, tail = tests_pass()
    log(f"{step} APPROVED — {head} — tests: {'green' if ok else 'RED'} ({tail})")
    return ok, tail


def wait_for_codex(deadline):
    while time.time() < deadline:
        ok, reset = codex_ok()
        if ok:
            return True
        when = datetime.datetime.fromtimestamp(reset).strftime("%H:%M") if reset else "?"
        log(f"gate: reviewer unavailable — reset {when}; sleeping {PROBE_EVERY//60} min")
        time.sleep(PROBE_EVERY)
    return False


def run_gate(deadline):
    """Full-branch gate (P24): Codex reviews the whole branch diff, read-only."""
    if not wait_for_codex(deadline):
        return "unavailable", None
    log("gate: running the Codex full-branch review")
    r = sh(f'codex exec --sandbox workspace-write "$(cat {GATE_PROMPT_1})"', cwd=REPO, timeout=10800)
    (LOGS / "gate-1.log").write_text((r.stdout or "") + (r.stderr or ""), encoding="utf-8")
    report = CAMPAIGN / "gate-report-1.md"
    verdict = ""
    if report.exists():
        m = re.search(r"\*\*VERDICT:\s*(APPROVE|REQUEST_CHANGES|REJECT)",
                      report.read_text(encoding="utf-8"))
        verdict = m.group(1) if m else ""
    return verdict or "unparsed", report


def main():
    deadline = time.time() + WAIT_HOURS * 3600
    start_at = os.environ.get("START_AT", "")
    completed, halted = [], None
    log("=" * 60)
    log("PHASE 1 CAMPAIGN — Claude DEV + Codex REVIEW, steps P1..P23 then the full-branch gate")
    sh("git checkout main", cwd=REPO)
    started = not start_at

    for step in STEP_IDS:
        if not started:
            if step == start_at:
                started = True
            else:
                log(f"{step}: already landed — skipping (resume at {start_at})")
                continue
        landed = False
        for attempt in range(1, MAX_ATTEMPTS_PER_STEP + 1):
            dev = wait_for_dev(deadline)
            if not dev:
                halted = f"{step}: no DEV window became available"
                break
            dev_cmd, loop_timeout = (DEV_GLM, GLM_LOOP_TIMEOUT) if dev == "glm" \
                else (DEV_CLAUDE_TUI, CLAUDE_LOOP_TIMEOUT)
            sh("tmux kill-server 2>/dev/null; true", timeout=15)   # frozen tmux env -> 401 panes
            log(f"{step}: DEV={dev} (attempt {attempt}/{MAX_ATTEMPTS_PER_STEP}) — launching the loop")
            rc, out = run_step(step, dev_cmd, loop_timeout)
            log(f"{step} loop exit={rc}")
            if rc == 0:
                ok, tail = approve(step)
                if ok:
                    completed.append(step)
                    landed = True
                else:
                    halted = f"{step}: post-merge tests RED ({tail})"
                break
            low = out.lower()
            if "429" in out or "usage limit" in low or "rate limit" in low:
                log(f"{step}: DEV rate-limited — waiting for a window, then retrying")
                continue
            if "DEV exited 3" in out or "timed out" in low:
                log(f"{step}: DEV inactivity timeout — refreshing the token and retrying")
                sh(f"python3 {REFRESH}", timeout=240)
                continue
            halted = f"{step}: loop failed for a non-quota reason — see logs/{step}.log"
            break
        if halted:
            break
        if not landed and not halted:
            halted = f"{step}: exhausted {MAX_ATTEMPTS_PER_STEP} attempts"

    gate_verdict = None
    if not halted:
        gate_verdict, report = run_gate(deadline)
        log(f"gate verdict: {gate_verdict} — report: {report}")

    ok, tail = tests_pass()
    head = sh("git log --oneline -1", cwd=REPO).stdout.strip()
    SUMMARY.write_text(
        "# Phase 1 campaign summary\n\n"
        f"- steps approved: {len(completed)} — {', '.join(completed) or 'none'}\n"
        f"- halted: {halted or 'no'}\n"
        f"- full-branch gate: {gate_verdict or 'not reached'}\n"
        f"- main HEAD: {head}\n"
        f"- final test run: {'green' if ok else 'RED'} — {tail}\n"
        f"- log: {CAMPAIGN_LOG}\n", encoding="utf-8")
    log(f"SUMMARY written to {SUMMARY}")
    return 0 if (not halted and gate_verdict == "APPROVE") else 1


if __name__ == "__main__":
    sys.exit(main())
