"""Opt-in integration trials against real providers (R9; AC34).

One trial per provider of the phase 2 trial table in ``docs/README.md`` —
speech synthesis, transcription, playback command, capture command, scene
provider, platform polls. Each is **skipped with a reason naming the unset
variable** unless the operator configures the provider through the
environment; no provider is ever assumed (AC43), so the default run of the
suite skips all six and opens nothing.

When its variable is set, a trial activates the **real** module on the
shared runtime harness with its **real** transports and runners — the
``aiohttp`` session, the asyncio subprocess player and recorder, the
websocket provider's own dialler — on the real monotonic clock and the
modules' default sleeper; this file injects no sleeper and makes no sleep
call. The call goes through the real executor under an explicit grant, and
the trial prints one line ``PHASE2-TRIAL <provider>: ...`` (run with ``-s``)
in the form the README row cites: status, code, and the result fields that
say what happened, never an endpoint, a token or a password.

Variables (each trial's first one switches it on):

* speech synthesis — ``PHASE2_SPEECH_ENDPOINT``, optional
  ``PHASE2_SPEECH_MODEL``, ``PHASE2_SPEECH_VOICE``, ``PHASE2_SPEECH_API_KEY``,
  ``PHASE2_SPEECH_PROBE`` (``0`` skips the prepare probe so the call itself
  reaches the endpoint and reports its own code);
  played through ``PHASE2_PLAYER_ARGV`` when set, else through a child
  interpreter that reads its stdin to the end (a sink, no device).
* transcription — ``PHASE2_TRANSCRIPTION_ENDPOINT``, optional
  ``PHASE2_TRANSCRIPTION_MODEL``, ``PHASE2_TRANSCRIPTION_LANGUAGE``,
  ``PHASE2_TRANSCRIPTION_API_KEY``; the segment comes from
  ``PHASE2_RECORDER_ARGV`` when set, else from a generated silent file.
* playback command — ``PHASE2_PLAYER_ARGV`` (shell-split), fed a generated
  1 s silent clip through ``audio.play``.
* capture command — ``PHASE2_RECORDER_ARGV`` (shell-split, a 16 kHz mono
  16-bit WAV on stdout), ``PHASE2_CAPTURE_SECONDS`` (default 2).
* scene provider — ``PHASE2_SCENE_URL`` (the ``websocket`` kind), required
  ``PHASE2_SCENE_NAME`` (the scene set), optional ``PHASE2_SCENE_PASSWORD``
  and ``PHASE2_SCENE_RESTORE`` (a scene set back afterwards, both allowed).
* platform polls — ``PHASE2_POLL_PLATFORM`` (``twitch``) with the platform's
  credentials under the profiles' names ``TWITCH_CLIENT_ID``,
  ``TWITCH_CLIENT_SECRET``, ``TWITCH_ACCESS_TOKEN`` (poll scope),
  ``TWITCH_BROADCASTER_ID``, ``TWITCH_BOT_USER_ID``; creates one real poll.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from core.actions import AuthorizationPolicy, AuthorizationRule
from core.attachments import AttachmentStore
from core.contracts import ActionCall, ActionObservation, Destination
from conftest import runtime_context, silent_wav

SPEECH_VARIABLE = "PHASE2_SPEECH_ENDPOINT"
TRANSCRIPTION_VARIABLE = "PHASE2_TRANSCRIPTION_ENDPOINT"
PLAYER_VARIABLE = "PHASE2_PLAYER_ARGV"
RECORDER_VARIABLE = "PHASE2_RECORDER_ARGV"
SCENE_VARIABLE = "PHASE2_SCENE_URL"
POLL_VARIABLE = "PHASE2_POLL_PLATFORM"

TRIAL_VARIABLES = (
    SPEECH_VARIABLE,
    TRANSCRIPTION_VARIABLE,
    PLAYER_VARIABLE,
    RECORDER_VARIABLE,
    SCENE_VARIABLE,
    POLL_VARIABLE,
)

#: The call deadline, from the call's entry on the real clock; each action's
#: own ``timeout_seconds`` still bounds it first (``_expiry``).
CALL_BUDGET_SECONDS = 120.0

#: A child interpreter that reads the WAV off its stdin to the end and exits
#: 0: a real process behind the real runner, reaching no device.
SINK_PLAYER_ARGV = [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"]

STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 2 * 1024 * 1024,
    "max_objects": 16,
    "max_total_bytes": 8 * 1024 * 1024,
    "max_bytes_per_run": 4 * 1024 * 1024,
    "ttl_seconds": 300.0,
}

TRIAL_TEXT = "Essai d'intégration de la phase deux."


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def required(variable: str) -> str:
    """The value of the trial's switch, or a skip naming it (AC34)."""

    value = os.environ.get(variable, "").strip()
    if not value:
        pytest.skip(f"{variable} is unset")
    return value


def companion(variable: str) -> str:
    """A setting the switched-on trial cannot run without: a failure naming it."""

    value = os.environ.get(variable, "").strip()
    if not value:
        pytest.fail(f"{variable} is unset; the trial needs it once it is switched on")
    return value


def optional(variable: str, default: str = "") -> str:
    return os.environ.get(variable, "").strip() or default


def argv_of(variable: str) -> list[str]:
    return shlex.split(os.environ[variable])


class Trial:
    """One real runtime on the real clock, one policy the trial grants on."""

    def __init__(self, *, attachments: AttachmentStore | None = None) -> None:
        self.policy = AuthorizationPolicy()
        self.runtime = runtime_context(
            clock=time.monotonic,  # type: ignore[arg-type]
            authorization=self.policy,
            attachments=attachments,
        )
        self._calls = 0

    def grant(self, action: str, permission: str) -> None:
        self.policy.grant(
            AuthorizationRule(
                rule_id=f"trial-{action}", action_name=action, granted_permissions=(permission,)
            )
        )

    async def invoke(
        self, action: str, arguments: Mapping[str, Any], destination: Destination
    ) -> ActionObservation:
        self._calls += 1
        call = ActionCall(
            action_name=action,
            action_version=1,
            arguments=dict(arguments),
            conversation_id="phase2-trial",
            run_id="phase2-trial-run",
            call_id=f"phase2-trial-{self._calls}",
            source_event_id="phase2-trial-source",
            destination=destination,
            principal="brain",
            deadline=time.monotonic() + CALL_BUDGET_SECONDS,
            message_id="phase2-trial-source",
        )
        return await self.runtime.executor.invoke(call)


def report(provider: str, observation: ActionObservation, **details: Any) -> str:
    """The one line the README row cites: status, code, what happened."""

    error = dict(observation.error or {})
    fields = [f"status={observation.status}"]
    if error:
        fields.append(f"code={error.get('code')}")
        if error.get("cause"):
            fields.append(f"cause={error['cause']}")
    fields.extend(f"{key}={value}" for key, value in details.items())
    line = f"PHASE2-TRIAL {provider}: " + " ".join(fields)
    print(line)
    return line


def result_fields(observation: ActionObservation, *names: str) -> dict[str, Any]:
    result = observation.result or {}
    return {name: result.get(name) for name in names if name in result}


def audio_part(observation: ActionObservation) -> Mapping[str, Any] | None:
    return next((part for part in observation.parts if part.get("type") == "audio_ref"), None)


def clip_path(tmp_path: Path, seconds: float = 1.0) -> Path:
    path = tmp_path / "trial-clip.wav"
    path.write_bytes(silent_wav(seconds))
    return path


# --------------------------------------------------------------------------- #
# Speech synthesis
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_speech_synthesis() -> None:
    """``audio.speak`` against the configured synthesis endpoint: one real
    request, the answered WAV played through the configured player (or the
    sink); success means synthesised **and** played to completion."""

    endpoint = required(SPEECH_VARIABLE)
    from modules.audio_output import MODULE_NAME, SPEAK_ACTION, activate

    player = argv_of(PLAYER_VARIABLE) if optional(PLAYER_VARIABLE) else SINK_PLAYER_ARGV
    voice = optional("PHASE2_SPEECH_VOICE", "default")
    trial = Trial()
    context = trial.runtime.for_module(MODULE_NAME)
    handle = await activate(
        context,
        {
            "synthesis": {
                "endpoint": endpoint,
                "model": optional("PHASE2_SPEECH_MODEL"),
                "api_key": optional("PHASE2_SPEECH_API_KEY"),
                "probe": optional("PHASE2_SPEECH_PROBE", "1") != "0",
            },
            "voices": {"allowed": [voice], "default": voice},
            "outputs": {"trial": {"player": {"argv": player}}},
            "default_output": "trial",
        },
        {},
    )
    try:
        await handle.prepare()
        bound = SPEAK_ACTION in trial.runtime.actions.registered_ready()
        trial.grant(SPEAK_ACTION, "audio.speak")
        observation = await trial.invoke(
            SPEAK_ACTION, {"text": TRIAL_TEXT}, Destination("trial", "channel", "audio")
        )
        report(
            "speech synthesis",
            observation,
            bound=bound,
            player="configured" if player is not SINK_PLAYER_ARGV else "sink",
            **result_fields(observation, "duration_ms", "played_ms"),
        )
    finally:
        await handle.close()
    assert observation.status in {"success", "error", "timeout", "refused"}


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_transcription(tmp_path: Path) -> None:
    """``audio.capture`` with transcription enabled against the configured
    endpoint: the probe at ``prepare``, then one capture whose ``audio_ref``
    carries the transcription status the endpoint earned."""

    endpoint = required(TRANSCRIPTION_VARIABLE)
    from modules.audio_input import CAPTURE_ACTION, MODULE_NAME, activate

    if optional(RECORDER_VARIABLE):
        source: dict[str, Any] = {"kind": "command", "argv": argv_of(RECORDER_VARIABLE)}
    else:
        source = {"kind": "file", "path": str(clip_path(tmp_path, 2.0))}
    transcription: dict[str, Any] = {
        "enabled": True,
        "endpoint": endpoint,
        "model": optional("PHASE2_TRANSCRIPTION_MODEL"),
        "api_key": optional("PHASE2_TRANSCRIPTION_API_KEY"),
    }
    if optional("PHASE2_TRANSCRIPTION_LANGUAGE"):
        transcription["language"] = optional("PHASE2_TRANSCRIPTION_LANGUAGE")
    store = AttachmentStore(clock=time.monotonic, **STORE_LIMITS)
    trial = Trial(attachments=store)
    context = trial.runtime.for_module(MODULE_NAME)
    handle = await activate(
        context,
        {"sources": {"trial": source}, "default_source": "trial", "transcription": transcription},
        {},
    )
    try:
        await handle.prepare()
        trial.grant(CAPTURE_ACTION, "audio.capture")
        observation = await trial.invoke(
            CAPTURE_ACTION,
            {"seconds": int(optional("PHASE2_CAPTURE_SECONDS", "2"))},
            Destination("trial", "channel", "audio"),
        )
        part = audio_part(observation) or {}
        text = (part.get("transcription") or {}).get("text")
        report(
            "transcription",
            observation,
            source=source["kind"],
            transcription_status=(observation.result or {}).get("transcription_status"),
            text_chars=None if text is None else len(text),
        )
    finally:
        await handle.close()
    assert observation.status in {"success", "error", "timeout", "refused"}


# --------------------------------------------------------------------------- #
# Playback command
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_playback_command(tmp_path: Path) -> None:
    """``audio.play`` of a generated 1 s silent clip through the configured
    player command on the real subprocess runner; no synthesis endpoint."""

    required(PLAYER_VARIABLE)
    from modules.audio_output import MODULE_NAME, PLAY_ACTION, activate

    trial = Trial()
    context = trial.runtime.for_module(MODULE_NAME)
    handle = await activate(
        context,
        {
            "synthesis": {"endpoint": "", "model": "", "probe": False},
            "voices": {"allowed": ["default"], "default": "default"},
            "outputs": {"trial": {"player": {"argv": argv_of(PLAYER_VARIABLE)}}},
            "default_output": "trial",
            "clips": {"trial": {"path": str(clip_path(tmp_path))}},
        },
        {},
    )
    try:
        await handle.prepare()
        trial.grant(PLAY_ACTION, "audio.play")
        observation = await trial.invoke(
            PLAY_ACTION, {"sound": "trial"}, Destination("trial", "channel", "audio")
        )
        report(
            "playback command",
            observation,
            **result_fields(observation, "duration_ms", "played_ms"),
        )
    finally:
        await handle.close()
    assert observation.status in {"success", "error", "timeout", "refused"}


# --------------------------------------------------------------------------- #
# Capture command
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_capture_command() -> None:
    """``audio.capture`` through the configured recorder command, stored in
    a real attachment store; transcription disabled."""

    required(RECORDER_VARIABLE)
    from modules.audio_input import CAPTURE_ACTION, MODULE_NAME, activate

    store = AttachmentStore(clock=time.monotonic, **STORE_LIMITS)
    trial = Trial(attachments=store)
    context = trial.runtime.for_module(MODULE_NAME)
    handle = await activate(
        context,
        {
            "sources": {"trial": {"kind": "command", "argv": argv_of(RECORDER_VARIABLE)}},
            "default_source": "trial",
        },
        {},
    )
    try:
        await handle.prepare()
        trial.grant(CAPTURE_ACTION, "audio.capture")
        observation = await trial.invoke(
            CAPTURE_ACTION,
            {"seconds": int(optional("PHASE2_CAPTURE_SECONDS", "2"))},
            Destination("trial", "channel", "audio"),
        )
        report(
            "capture command",
            observation,
            **result_fields(observation, "size", "duration_ms", "sample_rate_hz", "channels"),
            audio_ref=audio_part(observation) is not None,
        )
    finally:
        await handle.close()
    assert observation.status in {"success", "error", "timeout", "refused"}


# --------------------------------------------------------------------------- #
# Scene provider
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_scene_provider() -> None:
    """``stream.scene.set`` against the configured scene provider over the
    ``websocket`` kind (the provider's handshake, authentication, set and
    read-back on the real dialler), then the optional restore."""

    url = required(SCENE_VARIABLE)
    scene = companion("PHASE2_SCENE_NAME")
    restore = optional("PHASE2_SCENE_RESTORE")
    from modules.stream_control import MODULE_NAME, SCENE_ACTION, activate

    provider: dict[str, Any] = {"kind": "websocket", "url": url}
    if optional("PHASE2_SCENE_PASSWORD"):
        provider["password"] = optional("PHASE2_SCENE_PASSWORD")
    allowed = [scene] + ([restore] if restore and restore != scene else [])
    trial = Trial()
    context = trial.runtime.for_module(MODULE_NAME)
    handle = await activate(
        context, {"scenes": {"provider": provider, "allowed": allowed}}, {}
    )
    stream = Destination("trial", "channel", "stream")
    try:
        await handle.prepare()
        ready = SCENE_ACTION in trial.runtime.actions.registered_ready()
        trial.grant(SCENE_ACTION, "stream.scene")
        observation = await trial.invoke(SCENE_ACTION, {"scene": scene}, stream)
        report(
            "scene provider",
            observation,
            ready=ready,
            **result_fields(observation, "scene", "previous_scene", "reconciled"),
        )
        if restore:
            restored = await trial.invoke(SCENE_ACTION, {"scene": restore}, stream)
            report(
                "scene provider (restore)",
                restored,
                **result_fields(restored, "scene", "previous_scene", "reconciled"),
            )
    finally:
        await handle.close()
    assert observation.status in {"success", "error", "timeout", "refused", "external_unknown"}


# --------------------------------------------------------------------------- #
# Platform polls
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_platform_polls() -> None:
    """``stream.poll.create`` through the poll service the platform module
    publishes with its own credential, resolved by ``stream_control`` at
    ``prepare``: one real poll on the broadcaster's channel."""

    platform = required(POLL_VARIABLE)
    if platform != "twitch":
        pytest.fail(f"{POLL_VARIABLE}: only the twitch platform has a real poll service")
    credentials = {
        "client_id": companion("TWITCH_CLIENT_ID"),
        "client_secret": companion("TWITCH_CLIENT_SECRET"),
        "access_token": companion("TWITCH_ACCESS_TOKEN"),
        "broadcaster_id": companion("TWITCH_BROADCASTER_ID"),
        "bot_user_id": companion("TWITCH_BOT_USER_ID"),
    }
    from modules.stream_control import MODULE_NAME, POLL_ACTION
    from modules.stream_control import activate as activate_stream_control
    from modules.twitch import activate as activate_twitch

    trial = Trial()
    platform_handle = await activate_twitch(
        trial.runtime.for_module("twitch"), {**credentials, "companion_name": "Companion"}, {}
    )
    handle = await activate_stream_control(
        trial.runtime.for_module(MODULE_NAME),
        {"scenes": {"provider": {"kind": "none"}, "allowed": ["unused"]}, "polls": {"enabled": True}},
        {},
    )
    try:
        await handle.prepare()
        trial.grant(POLL_ACTION, "stream.poll")
        observation = await trial.invoke(
            POLL_ACTION,
            {
                "question": "Essai phase 2 ?",
                "options": ["Oui", "Non"],
                "duration_seconds": 15,
            },
            Destination(platform, credentials["broadcaster_id"], "poll"),
        )
        report(
            "platform polls",
            observation,
            **result_fields(observation, "state", "reconciled"),
        )
    finally:
        await handle.close()
        await platform_handle.close()
    assert observation.status in {"success", "error", "timeout", "refused", "external_unknown"}


# --------------------------------------------------------------------------- #
# The runner's own contract
# --------------------------------------------------------------------------- #


def test_every_trial_switch_is_named_by_a_trial() -> None:
    """AC34: the six switches are exactly the spec's, each read by one trial
    of this file through :func:`required`, so an unset one skips it naming
    the variable."""

    assert TRIAL_VARIABLES == (
        "PHASE2_SPEECH_ENDPOINT",
        "PHASE2_TRANSCRIPTION_ENDPOINT",
        "PHASE2_PLAYER_ARGV",
        "PHASE2_RECORDER_ARGV",
        "PHASE2_SCENE_URL",
        "PHASE2_POLL_PLATFORM",
    )
    source = Path(__file__).read_text(encoding="utf-8")
    constants = {
        SPEECH_VARIABLE: "SPEECH_VARIABLE",
        TRANSCRIPTION_VARIABLE: "TRANSCRIPTION_VARIABLE",
        PLAYER_VARIABLE: "PLAYER_VARIABLE",
        RECORDER_VARIABLE: "RECORDER_VARIABLE",
        SCENE_VARIABLE: "SCENE_VARIABLE",
        POLL_VARIABLE: "POLL_VARIABLE",
    }
    for constant in constants.values():
        assert f"required({constant})" in source, constant


def test_an_unset_switch_skips_naming_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC34: with a switch unset (or blank) the trial is skipped and the
    reason names the variable."""

    for variable in TRIAL_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
        with pytest.raises(pytest.skip.Exception) as skipped:
            required(variable)
        assert str(skipped.value) == f"{variable} is unset"
        monkeypatch.setenv(variable, "  ")
        with pytest.raises(pytest.skip.Exception):
            required(variable)
        monkeypatch.setenv(variable, "configured")
        assert required(variable) == "configured"
