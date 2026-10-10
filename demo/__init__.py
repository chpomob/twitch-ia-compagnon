"""``python -m demo`` — one companion run, narrated, offline.

The demo exists for the first five minutes of a newcomer's attention: it shows a
viewer's message becoming a written and spoken answer, in one command, with
nothing configured, no account and no key.

What it drives is not a mock-up of the runtime. It drives the scripted
end-to-end scenario of ``tests/test_phase2_scenario.py`` — the same scenario
whose assertions the suite enforces — and narrates the bus events that scenario
publishes. The loader, the admission queue, the trigger engine, the authorization
policy, the executor, the attachment store and the brain loop are the shipped
ones; only the four edges are scripted, and the footer says which:

* the model's two answers (a scripted HTTP session in place of your endpoint),
* Twitch's websocket and Helix replies (a scripted session),
* the microphone (a fixture segment) and the speech synthesis (a fixture wav).

No socket is opened, no credential is read, no model is called.

``--pace SECONDS`` slows the narration for a recording; ``--platform`` picks the
shipped ``twitch`` adapter (default) or the ``fakeplatform`` fixture, which is
the same run on a second platform with ``core/`` and the brain unchanged. The
demo refuses with a sentence, never a traceback, when it runs outside a checkout.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

from . import _harness

EXIT_OK = 0
EXIT_UNAVAILABLE = 3

PLATFORMS = ("twitch", "fake")
"""``twitch`` is the shipped adapter; ``fake`` is the second-platform fixture."""

RULE = "─" * 72

_TRACE_IGNORED = frozenset(
    {
        "brain.run.observation",
        "channel.chat.sent",
    }
)
"""Traces narrated by their own line, never by the generic one."""

_COLOURS = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "grey": "90",
}


@dataclass(frozen=True, slots=True)
class _Painter:
    """Wraps text in SGR codes, or does nothing when colour is off."""

    enabled: bool

    def __call__(self, colour: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_COLOURS[colour]}m{text}\033[0m"


@dataclass(frozen=True, slots=True)
class _Options:
    platform: str
    pace: float
    colour: bool
    root: str | None


class _Narration:
    """The demo's voice: prose it prints itself, and the bus traces it narrates.

    Installed on the bus at the end of the handler chain, so every line reports
    an event the runtime has already resolved rather than one it is about to.
    """

    def __init__(self, options: _Options, *, stream: Any = None) -> None:
        self.paint = _Painter(options.colour)
        self._pace = options.pace
        self._out = sys.stdout if stream is None else stream

    # -- the demo's own prose ---------------------------------------------- #

    def say(self, text: str = "", *, indent: int = 2) -> None:
        print(f"{' ' * indent}{text}", file=self._out)

    def title(self, text: str) -> None:
        self.say()
        self.say(self.paint("bold", text), indent=0)

    def field(self, label: str, value: str, *, indent: int = 2) -> None:
        self.say(f"{self.paint('grey', f'{label:<10} ')}{value}", indent=indent)

    async def stage(self, text: str) -> None:
        """A stage heading, printed after a pause so a recording breathes."""

        await self._breathe()
        self.say()
        self.say(self.paint("bold", text))

    async def pause(self) -> None:
        await self._breathe()

    async def _breathe(self) -> None:
        if self._pace > 0:
            await asyncio.sleep(self._pace)

    # -- the bus handler --------------------------------------------------- #

    async def handle(self, event: Mapping[str, Any]) -> None:
        kind = event.get("type")
        payload = event.get("payload") or {}
        line = None
        if kind == "channel.chat.message":
            author = payload.get("author") or {}
            who = author.get("display_name") or author.get("id") or "?"
            line = self.field(
                "chat",
                f"{self.paint('cyan', str(who))} → "
                f"{self.paint('bold', repr(payload.get('text', '')))}",
            )
        elif kind == "input.trigger.accepted":
            line = self.field(
                "trigger",
                self.paint("green", f"accepted: {_reason(payload)}")
                + f" · input {payload.get('input')}"
                + f" · channel {payload.get('channel_id')}",
            )
        elif kind == "input.trigger.rejected":
            line = self.field(
                "trigger",
                self.paint("red", f"rejected: {_reason(payload)}"),
            )
        elif kind == "brain.admission.accepted":
            line = self.field(
                "admitted",
                f"session {payload.get('viewer_id')} · "
                f"{payload.get('queue_depth')} queued · "
                f"run {self.paint('bold', str(payload.get('run_id')))}",
            )
        elif kind == "brain.admission.rejected":
            line = self.field(
                "refused",
                self.paint("red", f"run not admitted: {payload.get('reason')}"),
            )
        elif kind == "brain.run.started":
            line = self.field(
                "run",
                f"started, {payload.get('total_deadline_seconds')} s deadline"
                + (
                    f", waited {payload.get('waited_seconds')} s"
                    if payload.get("waited_seconds")
                    else ""
                ),
            )
        elif kind == "action.started":
            nature = payload.get("nature")
            line = self.field(
                "call",
                self.paint(
                    "dim",
                    f"{payload.get('action')} [{nature}] → {payload.get('destination')}"
                    f" ({payload.get('provider')})",
                ),
            )
        elif kind == "action.completed":
            # A read's outcome is narrated by its observation, which carries
            # what came back; a write has no observation, so it reports here.
            if payload.get("nature") != "write":
                line = None
            else:
                line = self.field("", self.paint("green", _outcome(payload)))
        elif kind == "brain.run.observation":
            line = self.field("", self.paint("green", _observation(payload)))
        elif kind == "channel.chat.sent":
            line = self.field(
                "chat",
                self.paint("green", f"delivered as {payload.get('message_id')}")
                + f" (route {payload.get('route')})",
            )
        elif kind == "brain.run.completed":
            line = self.field(
                "run",
                self.paint(
                    "green" if payload.get("status") == "success" else "red",
                    f"{payload.get('status')}: {payload.get('reason')}",
                )
                + f" · {payload.get('model_calls')} model calls"
                + f" · {payload.get('action_calls')} action calls"
                + f" · delivery {payload.get('delivery')}",
            )
        elif kind == "module.ready":
            capabilities = payload.get("capabilities") or ()
            line = self.field(
                "ready",
                self.paint("green", str(payload.get("module")))
                + (f" · {', '.join(map(str, capabilities))}" if capabilities else ""),
            )
        elif kind == "module.degraded":
            line = self.field(
                "warn",
                self.paint("yellow", f"{payload.get('module')}: {payload.get('reason')}"),
            )
        elif kind == "module.stopped":
            line = self.field(
                "stopped",
                f"{payload.get('module')} ({payload.get('phase')})",
            )
        elif kind not in _TRACE_IGNORED:
            line = None
        if line is not None:
            await self._breathe()


def _reason(payload: Mapping[str, Any]) -> str:
    """A decision's reason, without the verdict prefix it repeats."""

    reason = str(payload.get("reason") or "no reason")
    for prefix in ("accepted:", "rejected:"):
        if reason.startswith(prefix):
            return reason[len(prefix) :]
    return reason


def _scripted_proposal(scenario: Any) -> str:
    """What the scripted model proposes on its first turn, from the scenario."""

    try:
        function = scenario.SCENARIO[0]["choices"][0]["message"]["tool_calls"][0]["function"]
    except (KeyError, IndexError, TypeError):
        return "a read action"
    return f"{function['name']} {function['arguments']}"


def _outcome(payload: Mapping[str, Any]) -> str:
    status = payload.get("status")
    text = f"← {payload.get('action')} {status}"
    if payload.get("error_code"):
        text += f" ({payload.get('error_code')})"
    return text


def _observation(payload: Mapping[str, Any]) -> str:
    """One read's observation: what came back, in the harness's own numbers."""

    parts = payload.get("parts") or ()
    described = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        text = f"{part.get('type')} {part.get('content_type') or ''}".strip()
        if part.get("duration_ms"):
            text += f" ({_group(part.get('duration_ms'))} ms)"
        described.append(text)
    return (
        f"← {payload.get('action')} {_group(payload.get('observation_bytes'))} B"
        f" · {' + '.join(described) or 'no part'}"
    )


def _group(value: Any) -> str:
    """``96404`` → ``96 404``: a readable size in a terminal line."""

    if isinstance(value, int):
        return f"{value:,}".replace(",", " ")
    return str(value)


def _use_colour(no_colour: bool, force: bool) -> bool:
    if no_colour or os.environ.get("NO_COLOR"):
        return False
    if force:
        return True
    return sys.stdout.isatty()


def _parse(argv: Sequence[str] | None) -> _Options:
    parser = argparse.ArgumentParser(
        prog="python -m demo",
        description=(
            "Drive one scripted companion run — viewer message, brain, tools, "
            "chat and speech delivery — with no credential and no network."
        ),
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        default=PLATFORMS[0],
        help="the platform edge to drive: the shipped twitch adapter, "
        "or the fakeplatform fixture (default: twitch)",
    )
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="pause between narrated lines, for a recording (default: 0)",
    )
    parser.add_argument(
        "--no-colour",
        "--no-color",
        dest="no_colour",
        action="store_true",
        help="never emit ANSI escapes (also honoured through NO_COLOR)",
    )
    parser.add_argument(
        "--colour",
        "--color",
        dest="colour",
        action="store_true",
        help="emit ANSI escapes even when the output is not a terminal "
        "(for a recording: asciinema, script(1), a pipe)",
    )
    parser.add_argument(
        "--repository-root",
        default=None,
        metavar="PATH",
        help="the checkout holding tests/ (default: the demo's own)",
    )
    args = parser.parse_args(argv)
    if args.pace < 0:
        parser.error("--pace must not be negative")
    return _Options(
        platform=args.platform,
        pace=args.pace,
        colour=_use_colour(args.no_colour, args.colour),
        root=args.repository_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse *argv*, drive the scenario, narrate it; the process exit code."""

    options = _parse(argv)
    narration = _Narration(options)
    try:
        scenario = _harness.load_scenario(options.root)
    except _harness.DemoUnavailable as refusal:
        print(f"demo: {refusal}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    _banner(narration, options)
    try:
        return asyncio.run(_drive(scenario, options, narration))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


def _banner(narration: _Narration, options: _Options) -> None:
    paint = narration.paint
    narration.say()
    narration.say(paint("bold", "twitch-ia-compagnon") + "  ·  offline demo", indent=0)
    narration.say(paint("grey", RULE), indent=0)
    narration.say("A viewer's message becomes a written and spoken answer.")
    narration.say("No socket is opened, no credential is read, no model is called:")
    narration.say("the four edges (model, platform, microphone, speech) are scripted.")
    narration.field("platform", f"{options.platform} — the shipped runtime, "
                                "scripted transports")


async def _drive(scenario: Any, options: _Options, narration: _Narration) -> int:
    """The four acts, with the narration installed on the harness for the run."""

    with _narrating(scenario, narration):
        return await _acts(scenario, options, narration)


@contextlib.contextmanager
def _narrating(scenario: Any, narration: _Narration) -> Iterator[None]:
    """Install *narration* on every bus the harness builds, for this run only.

    The scenario builds its own runtime context, so the seam is the context
    builder. It is wrapped and restored rather than replaced cumulatively: two
    runs in one process — two tests, or a script calling the entry point twice —
    must not leave the first narration attached to the second run's bus, where
    it would print to a stream that no longer exists and fail the publication.
    """

    origin = scenario.build_context

    def observed(**kwargs: Any) -> Any:
        context = origin(**kwargs)
        # Last in the chain: every line reports a resolved event, after the
        # modules' own handlers have run (the audit module sits at order 90).
        context.bus.subscribe("**", narration.handle, order=200)
        return context

    scenario.build_context = observed
    try:
        yield
    finally:
        scenario.build_context = origin


async def _acts(scenario: Any, options: _Options, narration: _Narration) -> int:
    """The four acts: bring up, a viewer speaks, the brain runs, shut down."""

    clock = scenario.ManualClock()

    await narration.stage("ACT 1 — bring the companion up (the real loader and coordinator)")
    brain = await scenario.start_brain_side(
        _platform_key(scenario, options.platform),
        clock=clock,
        audio=scenario.AudioSeams.build(clock),
    )
    shipped = len(list(scenario.SHIPPED_MODULES.glob("*/module.yaml")))
    narration.field("modules", ", ".join(sorted(brain.handles)))
    narration.field(
        "",
        f"{len(brain.handles)} of the {shipped} shipped — prepared in activation "
        "order, gated at the readiness barrier",
    )
    narration.field(
        "brain",
        "its own prepare-time probe verified: "
        + ", ".join(sorted(brain.brain.verified_capabilities)),
    )
    narration.field("clock", "manual: nothing sleeps, no wall-clock second is spent")

    await narration.stage("ACT 2 — a viewer speaks up")
    narration.field("script", "the model's two answers are scripted:")
    narration.field("", _scripted_proposal(scenario))
    narration.field("", f"then {scenario.ANSWER!r}")
    narration.field("script", "so is the microphone: a fixture segment the scripted")
    narration.field("", f"transcription turns into {scenario.TRANSCRIPT!r}")
    narration.field("delivery", "chat.write then audio.speak — an ordered list the")
    narration.field("", "operator writes; the model never picks it")
    await narration.pause()

    await narration.stage("ACT 3 — the brain runs its bounded loop")
    try:
        run = await scenario.run_scenario(brain)
    except Exception:
        # The scenario asserts its own run; a failure here is a failure of the
        # demo, and the diagnostics are what says why. Print them before the
        # traceback rather than hiding them behind it.
        narration.field(
            "diagnostics",
            "; ".join(brain.diagnostics) if brain.diagnostics else "none reported",
        )
        raise
    _summary(narration, scenario, brain, run)

    await narration.stage("ACT 4 — shutdown (one global deadline, then close)")
    report = await brain.stop()
    await brain.close()
    narration.field(
        "report",
        f"status {report.status} · {len(report.failures)} failures · "
        f"{len(report.diagnostics)} shutdown diagnostics",
    )
    counters = brain.context.supervision.snapshot()
    raised = {name: value for name, value in counters.items() if value}
    narration.field(
        "counters",
        f"the {len(counters)} counters are all zero"
        if not raised
        else ", ".join(f"{name}={value}" for name, value in sorted(raised.items())),
    )
    narration.field(
        "diagnostics",
        "none" if not brain.diagnostics else "; ".join(brain.diagnostics),
    )
    _footer(narration)
    return EXIT_OK


def _summary(narration: _Narration, scenario: Any, brain: Any, run: Any) -> None:
    """What the run produced, read from the harness's own result object."""

    paint = narration.paint
    completed = run.completed
    part = run.capture.parts[0]
    transcription = part["transcription"]
    second = brain.session.requests()[1]
    carried = scenario.carries_audio_part(second)
    narration.say()
    narration.say(paint("bold", "outcome"))
    narration.field("run", f"{run.record.run_id} — {paint('green', run.record.status)}")
    narration.field(
        "loop",
        f"{completed['turns']} model turns, {completed['action_calls']} action calls, "
        f"{len(completed['deliveries'])} deliveries, {completed['audio_omitted']} audio omitted",
    )
    narration.field(
        "capture",
        f"{_group(part['size'])} B stored ({_group(part['duration_ms'])} ms), "
        f"transcript {transcription['text']!r}"
        + ("" if not transcription["truncated"] else " (truncated)"),
    )
    narration.field(
        "model",
        "turn 1 text only, turn 2 "
        + (
            paint("green", "carried the audio back into the loop")
            if carried
            else "did NOT carry the audio"
        ),
    )
    narration.field(
        "delivery",
        f"chat.write → {run.sends[0]!r}"
        if run.sends
        else "chat.write → nothing left",
    )
    narration.field("", f"audio.speak → playback {run.speak.result['playback']}, "
                        f"{_group(run.speak.result['played_ms'])} ms played")
    narration.field(
        "store",
        f"{brain.store.object_count} attachments left: the lease was released with the run",
    )


def _platform_key(scenario: Any, requested: str) -> str:
    """The harness's key for a CLI choice: ``fake`` is the fixture platform."""

    return scenario.FAKEPLATFORM if requested == "fake" else requested


def _footer(narration: _Narration) -> None:
    paint = narration.paint
    narration.say()
    narration.say(paint("grey", RULE), indent=0)
    narration.say(paint("bold", "what was real"))
    narration.say("the loader, the admission queue, the trigger engine, the authorization")
    narration.say("policy, the executor, the attachment store, the brain loop — the shipped")
    narration.say("core, unmodified. The brain named no platform and no audio module.")
    narration.say(paint("bold", "what was scripted"))
    narration.say("the model's two answers, the platform's replies, the microphone,")
    narration.say("the speech synthesis, the audio player.")
    narration.say()
    narration.say("So this proves the runtime drives a full turn; it proves nothing about")
    narration.say("the live APIs, which have never been called. See the README.")
    narration.say(paint("grey", RULE), indent=0)
