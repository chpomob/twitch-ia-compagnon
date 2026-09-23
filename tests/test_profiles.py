"""The shipped profiles as a user meets them (R7, R8; AC40, AC41, AC43 —
and phase 2: R8, R9; AC31, AC32, AC33, AC43).

The checks the example suite (``tests/test_examples.py``) does not make:

* ``--check-config`` accepts each of the three profiles with dummy
  environment values and opens 0 sockets, and names the setting path — never
  a value — when a credential is unset (AC40);
* transport selection is by enabled modules alone (decision 7): the server
  profile binds ``screen.capture`` to the ``proxy`` provider, the PC profile
  to the local ``capture`` provider, and a brain profile enabling both fails
  preparation with the ambiguity diagnostic naming the action and both
  providers (AC41);
* the built distribution installs into an empty target with
  ``pip install --no-deps`` and, from a directory outside the checkout,
  ``modules_directory: builtin`` discovers exactly the 11 shipped manifests
  (phase 2's R9, AC33: 8 → 11, allowlisted) and the console script answers
  ``--help`` (AC43). That test skips only when ``pip`` is unavailable,
  naming that reason; a build backend that is not importable, an install
  error or a wrong manifest count is a failure;
* phase 2 (R8, R9): the server profile binds its five allowlisted device
  actions to the ``proxy`` provider and ``stream.poll.create`` to
  ``stream_control`` with no ambiguity (AC32); ``--check-config`` passes
  every profile twice — as shipped, with no speech or transcription
  endpoint, and with those settings configured by reference, their shape
  checked and 0 sockets opened (AC43); with no speech endpoint, or one
  configured but unreachable, ``audio.speak`` is named not ready, value-free,
  with 0 requests and startup succeeds (AC43); the full PC profile with
  every phase 2 endpoint on a closed loopback port and no usable player
  reaches readiness with exactly 3 ``module.degraded`` and still chats
  (AC31); ``pyproject.toml`` ships the three new manifests and no new
  dependency (AC33).

No positive-duration sleep: the checks run on fakes, the install test on
subprocesses that terminate on their own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 floor only
    import tomli as tomllib

import pytest
import yaml

import core.main as application
from core.contracts import TRACE_MODULE_DEGRADED, ActionCall, Destination
from conftest import (
    ManualClock,
    RecordingPlayerRunner,
    events_of,
    wav_bytes,
)
from modules.audio_input import TRANSCRIPTION_DEGRADED_REASON
from modules.audio_output import SYNTHESIS_NOT_CONFIGURED_REASON, SYNTHESIS_PROBE_FAILED_REASON
from modules.capture import SCREEN_CAPTURE_PROVIDER
from modules.proxy import PROVIDER_NAME as PROXY_PROVIDER
from test_examples import (
    ACTION_SCOPES,
    MODULE_NAMES,
    PC_PROFILE,
    PHASE1_ACTIONS,
    PHASE1_READ_ACTIONS,
    PHASE2_ACTIONS,
    PROFILES,
    ROOT,
    SERVER_PROFILE,
    SERVER_PROXY_ACTIONS,
    DeviceEdges,
    ScenePeer,
    _activate_example,
    _assert_the_chat_scenario_ran,
    _chat_scenario_model,
    _close_all,
    _offered_tools,
    _prepare_all,
    _read_yaml,
    _sample_environ,
    _start_profile,
)


SCREEN_CAPTURE = "screen.capture"
#: The credential AC40 unsets, and the diagnostic the check must give.
UNSET_CREDENTIAL = "TWITCH_ACCESS_TOKEN"
UNSET_DIAGNOSTIC = "modules.twitch.access_token: environment reference is unresolved"
#: The names R8 requires a clean environment to discover, in its order, and
#: the three phase 2's R9 ships beside them (allowlisted: 8 → 11), and the
#: phase 3 manifests as each step adds one (allowlisted; P9: ``clips``, 12).
SHIPPED_MANIFESTS = (
    "twitch",
    "brain",
    "audit",
    "chat_context",
    "users",
    "capture",
    "proxy",
    "agent_link",
    "audio_output",
    "audio_input",
    "stream_control",
    "clips",
)
#: The three package-data lines phase 2 adds (R9, AC33).
PHASE2_PACKAGE_DATA = ("modules.audio_output", "modules.audio_input", "modules.stream_control")
CONSOLE_SCRIPT = "twitch-ia-compagnon"


# --------------------------------------------------------------------------- #
# AC40: --check-config on each profile
# --------------------------------------------------------------------------- #


def _count_sockets(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Every ``socket.socket`` constructed from now on, by its arguments.

    The class is replaced in the ``socket`` module, which is where asyncio,
    ``ssl`` and every helper (``create_connection``, ``create_server``,
    ``socketpair``) take it from; the running loop's own self-pipe was
    opened before this patch and is not counted.
    """

    opened: list[tuple[Any, ...]] = []
    real_socket = socket.socket

    class CountingSocket(real_socket):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            opened.append(args)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(socket, "socket", CountingSocket)
    return opened


def _refuse_loop_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """The running loop refuses to open any endpoint for the check's duration."""

    loop = asyncio.get_running_loop()
    for name in ("create_server", "create_connection", "create_datagram_endpoint"):

        async def refuse(*args: Any, _name: str = name, **kwargs: Any) -> Any:
            pytest.fail(f"--check-config must not call loop.{_name}")

        monkeypatch.setattr(loop, name, refuse)


@pytest.mark.parametrize("profile", sorted(PROFILES))
async def test_check_config_accepts_each_profile_and_opens_no_socket(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC40: ``--check-config`` exits 0 on each of the three profiles with
    dummy environment values — the agent's ``BRAIN_URL`` in the ``wss://``
    form the R6 rule accepts off loopback — reports 0 diagnostics and opens
    0 sockets: the whole pre-activation path runs, no transport does."""

    path = PROFILES[profile]
    environ = _sample_environ(path)
    diagnostics: list[str] = []
    _refuse_loop_endpoints(monkeypatch)
    opened = _count_sockets(monkeypatch)

    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=diagnostics.append
    )

    assert status == 0
    assert diagnostics == []
    assert opened == []


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_main_check_config_exits_zero_on_each_profile(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC40 through the command line: ``--config <profile> --check-config``
    with the dummy values exported returns the process status 0."""

    path = PROFILES[profile]
    for name, value in _sample_environ(path).items():
        monkeypatch.setenv(name, value)

    assert application.main(["--config", str(path), "--check-config"]) == 0


@pytest.mark.parametrize("profile", ("pc", "server"))
async def test_check_config_names_the_unset_credential_path_and_no_value(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC40: with ``TWITCH_ACCESS_TOKEN`` unset the check exits 2 on both
    twitch profiles and the one diagnostic names the setting path and no
    value — no dummy value, not even the variable's name as a value."""

    path = PROFILES[profile]
    environ = _sample_environ(path)
    del environ[UNSET_CREDENTIAL]
    diagnostics: list[str] = []
    opened = _count_sockets(monkeypatch)

    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=diagnostics.append
    )

    assert status == 2
    assert diagnostics == [UNSET_DIAGNOSTIC]
    assert all(value not in diagnostics[0] for value in environ.values())
    assert opened == []


@pytest.mark.parametrize("profile", ("pc", "server"))
def test_main_check_config_exits_two_without_the_twitch_token(
    profile: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC40 through the command line: every other dummy value exported and
    ``TWITCH_ACCESS_TOKEN`` unset, the process status is 2 and stderr carries
    the diagnostic naming the setting path and no exported value."""

    path = PROFILES[profile]
    environ = _sample_environ(path)
    del environ[UNSET_CREDENTIAL]
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(UNSET_CREDENTIAL, raising=False)

    assert application.main(["--config", str(path), "--check-config"]) == 2
    stderr = capsys.readouterr().err
    assert UNSET_DIAGNOSTIC in stderr
    assert all(value not in stderr for value in environ.values())


# --------------------------------------------------------------------------- #
# AC43: --check-config with the speech and transcription settings unset, then set
# --------------------------------------------------------------------------- #


#: The speech and transcription settings AC43 ships EMPTY, by module, and the
#: placeholder reference an operator would configure each with.
SPEECH_SETTINGS = {
    "audio_output": ("synthesis", "SPEECH"),
    "audio_input": ("transcription", "TRANSCRIPTION"),
}
SPEECH_FIELDS = ("endpoint", "model", "api_key")


def _speech_variables(module_name: str) -> dict[str, str]:
    group, prefix = SPEECH_SETTINGS[module_name]
    return {field: f"{prefix}_{field.upper()}" for field in SPEECH_FIELDS}


def _with_speech_configured(path: Path, directory: Path) -> Path:
    """*path* with every speech and transcription setting configured by a
    ``${NAME}`` reference — the one change an operator makes (AC43)."""

    config = dict(_read_yaml(path))
    modules = {name: dict(settings) for name, settings in config["modules"].items()}
    for module_name, (group, _prefix) in SPEECH_SETTINGS.items():
        if module_name in modules:
            modules[module_name][group] = {
                **modules[module_name][group],
                **{
                    field: f"${{{variable}}}"
                    for field, variable in _speech_variables(module_name).items()
                },
            }
    config["modules"] = modules
    config["modules_directory"] = str(ROOT / "modules")
    configured = directory / f"configured-{path.name}"
    configured.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return configured


@pytest.mark.parametrize("profile", sorted(PROFILES))
async def test_check_config_passes_with_the_speech_endpoints_unset_then_configured(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC43, AC32 (R9): ``--check-config`` on each profile twice. First as
    shipped: the speech and transcription endpoints, models and keys are
    EMPTY — no provider is assumed — so not one of their variables is set,
    and the check exits 0 with 0 diagnostics and 0 sockets. Then with those
    settings configured by reference and placeholder values exported: the
    check validates their shape only — readiness is decided at ``prepare``,
    which the check never reaches — and still exits 0 with 0 sockets."""

    path = PROFILES[profile]
    config = _read_yaml(path)
    audio = [name for name in SPEECH_SETTINGS if name in config["enabled_modules"]]
    assert bool(audio) == (profile != "server")
    for module_name in audio:
        group, _prefix = SPEECH_SETTINGS[module_name]
        assert {
            field: config["modules"][module_name][group][field] for field in SPEECH_FIELDS
        } == dict.fromkeys(SPEECH_FIELDS, "")

    _refuse_loop_endpoints(monkeypatch)
    opened = _count_sockets(monkeypatch)

    unset = _sample_environ(path)
    for module_name in SPEECH_SETTINGS:
        assert not set(_speech_variables(module_name).values()) & set(unset)
    diagnostics: list[str] = []
    status = await application.check_config(
        path, environ=unset, diagnostic_reporter=diagnostics.append
    )
    assert (status, diagnostics, opened) == (0, [], [])

    configured = _with_speech_configured(path, tmp_path)
    environ = _sample_environ(configured)
    for module_name in audio:
        variables = _speech_variables(module_name)
        assert set(variables.values()) <= set(environ)
        assert environ[variables["endpoint"]].startswith("https://")
    status = await application.check_config(
        configured, environ=environ, diagnostic_reporter=diagnostics.append
    )
    assert (status, diagnostics, opened) == (0, [], [])


# --------------------------------------------------------------------------- #
# AC43: no speech provider assumed — empty or unreachable, named not ready
# --------------------------------------------------------------------------- #


def _degraded_by_module(started: Any) -> dict[str, list[dict[str, Any]]]:
    by_module: dict[str, list[dict[str, Any]]] = {}
    for payload in started.degraded():
        by_module.setdefault(payload["module"], []).append(payload)
    return by_module


def _assert_value_free(reason: str, environ: Mapping[str, str], *extra: str) -> None:
    """No URL, host, port, path or configured value in *reason* (R8)."""

    assert "://" not in reason and "127.0.0.1" not in reason, reason
    assert os.sep not in reason, reason
    for value in [*environ.values(), *extra]:
        assert value not in reason, reason


async def _capture(started: Any) -> Any:
    """One ``audio.capture`` of 3 s on the served channel, through the executor."""

    channel = started.environ["TWITCH_BROADCASTER_ID"]
    call = ActionCall(
        action_name="audio.capture",
        action_version=1,
        arguments={"seconds": 3},
        conversation_id="conversation-ac43",
        run_id="run-ac43",
        call_id="call-ac43",
        source_event_id="source-ac43",
        destination=Destination("twitch", channel, "audio"),
        principal="brain",
        deadline=started.runtime.clock() + 60,
        message_id="source-ac43",
    )
    return await started.runtime.context.executor.invoke(call)


async def _speak(started: Any) -> Any:
    """One ``audio.speak`` on the served channel, through the executor."""

    channel = started.environ["TWITCH_BROADCASTER_ID"]
    call = ActionCall(
        action_name="audio.speak",
        action_version=1,
        arguments={"text": "hello"},
        conversation_id="conversation-ac43",
        run_id="run-ac43",
        call_id="call-ac43-speak",
        source_event_id="source-ac43",
        destination=Destination("twitch", channel, "audio"),
        principal="brain",
        deadline=started.runtime.clock() + 60,
        message_id="source-ac43",
    )
    return await started.runtime.context.executor.invoke(call)


def _assert_speak_named_not_ready(started: Any, environ: Mapping[str, str], *extra: str) -> str:
    """AC43's not-ready shape: startup succeeded, ``audio.speak`` declared
    and unbound, named alone by ``audio_output``'s one ``module.degraded``
    with a value-free reason, ``audio.play`` still ready; the transcription
    named unavailable by ``audio_input`` while ``audio.capture`` is ready."""

    assert started.report.status == 0, (started.report, started.reported)
    registry = started.registry
    assert "audio.speak" in registry.discovered()
    assert registry.bindings("audio.speak") == ()
    ready = set(registry.registered_ready())
    assert "audio.speak" not in ready
    assert {"audio.play", "audio.capture", "stream.scene.set"} <= ready
    degraded = _degraded_by_module(started)
    assert set(degraded) == {"audio_output", "audio_input"}, degraded
    (speech,) = degraded["audio_output"]
    assert speech["capabilities"] == ["audio.speak"]
    _assert_value_free(speech["reason"], environ, *extra)
    (transcription,) = degraded["audio_input"]
    assert transcription["reason"] == TRANSCRIPTION_DEGRADED_REASON
    assert list(transcription.get("capabilities") or []) == []
    return speech["reason"]


async def test_ac43_with_no_speech_endpoint_audio_speak_is_named_not_ready_with_0_requests() -> None:
    """AC43 (R2, R9; arbiter decision 22/09): the shipped PC profile — every
    speech and transcription setting EMPTY — starts through the real
    coordinator: ``audio.speak`` is not bound and is named not ready with a
    value-free reason, the transcription is named unavailable, 0 speech or
    transcription transports are made and 0 requests sent, startup succeeds,
    and a capture without transcription is still ``success`` with its
    ``audio_ref`` (``transcription_status: unavailable``) — still 0
    requests."""

    environ = _sample_environ(PC_PROFILE)
    edges = DeviceEdges(peer=ScenePeer(scenes=("Main", "Break"), current="Main"))
    started = await _start_profile(
        PC_PROFILE, environ, model=_chat_scenario_model(), device_seams=edges
    )
    try:
        reason = _assert_speak_named_not_ready(started, environ)
        # Gate 1 F3: the empty endpoint has its own reason, distinct from the
        # unreachable one below.
        assert reason == SYNTHESIS_NOT_CONFIGURED_REASON
        assert reason != SYNTHESIS_PROBE_FAILED_REASON
        assert edges.requests() == 0
        assert edges.runner.starts == []

        # Gate 1 F1: a call to the unbound action is refused, not an error.
        invocations = started.runtime.context.executor.provider_invocations
        speech = await _speak(started)
        assert (speech.status, speech.error["code"]) == ("refused", "provider_not_ready")
        assert started.runtime.context.executor.provider_invocations == invocations
        assert edges.requests() == 0
        assert edges.runner.starts == []

        observation = await _capture(started)
        assert observation.status == "success", observation.error
        assert observation.result["transcription_status"] == "unavailable"
        (part,) = observation.parts
        assert part["type"] == "audio_ref" and "transcription" not in part
        assert edges.requests() == 0
    finally:
        await started.stop()
        await edges.close()


async def test_ac43_with_an_unreachable_speech_endpoint_audio_speak_is_named_not_ready_too(
    tmp_path: Path,
) -> None:
    """AC43 (arbiter decision 22/09): with the speech and transcription
    endpoints CONFIGURED but UNREACHABLE at ``prepare`` — a closed loopback
    port, the shipped transports, every bound on an injected clock nobody
    advances — the same not-ready naming appears: ``audio.speak`` unbound
    and named with a value-free reason, the transcription unavailable, no
    request answered, and startup succeeds."""

    config = dict(_read_yaml(PC_PROFILE))
    modules = {name: dict(settings) for name, settings in config["modules"].items()}
    modules["audio_output"]["synthesis"] = {
        "endpoint": "http://127.0.0.1:1/speech",
        "model": "configured-speech-model",
        "api_key": "",
    }
    modules["audio_input"]["transcription"] = {
        "enabled": True,
        "endpoint": "http://127.0.0.1:1/transcriptions",
        "model": "configured-transcription-model",
        "api_key": "",
    }
    config["modules"] = modules
    config["modules_directory"] = str(ROOT / "modules")
    path = tmp_path / "unreachable.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    environ = _sample_environ(path)
    edges = DeviceEdges(peer=ScenePeer(scenes=("Main", "Break"), current="Main"))
    seams = edges.seams()
    # The shipped transports, against the closed port.
    del seams["audio_output"]["_synthesis_transport"]
    del seams["audio_input"]["_transcription_transport"]
    started = await asyncio.wait_for(
        _start_profile(path, environ, model=_chat_scenario_model(), device_seams=seams),
        timeout=60,
    )
    try:
        reason = _assert_speak_named_not_ready(
            started, environ, "configured-speech-model", "configured-transcription-model"
        )
        # Gate 1 F3: a configured endpoint that failed its probe is named
        # distinctly from an absent one.
        assert reason == SYNTHESIS_PROBE_FAILED_REASON
        assert reason != SYNTHESIS_NOT_CONFIGURED_REASON
        assert edges.runner.starts == []
    finally:
        await started.stop()
        await edges.close()


# --------------------------------------------------------------------------- #
# AC31: the full PC profile, every phase 2 dependency unavailable
# --------------------------------------------------------------------------- #


async def test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats(
    tmp_path: Path,
) -> None:
    """AC31 (R8): the full PC profile with every phase 2 endpoint on the
    closed loopback port ``127.0.0.1:1`` (speech, transcription, scene
    provider — the shipped transports, every bound on an injected clock
    nobody advances), a non-executable player and recorder, and no poll
    service credentials (polls disabled, plan decision 6) reaches readiness
    through the real coordinator with exactly 3 ``module.degraded`` —
    ``audio_input``, ``audio_output``, ``stream_control`` — each naming its
    unbound actions with a value-free reason; runs the chat scenario with 1
    send; lists none of the five phase 2 actions in its registered-ready
    view; and the brain's authorized tools are the phase 1 read actions
    only. Nothing was played."""

    not_executable = tmp_path / "not-executable"
    not_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    not_executable.chmod(0o644)
    chime = tmp_path / "chime.wav"
    chime.write_bytes(wav_bytes(0.5))

    config = dict(_read_yaml(PC_PROFILE))
    modules = {name: dict(settings) for name, settings in config["modules"].items()}
    modules["audio_output"]["synthesis"] = {
        "endpoint": "http://127.0.0.1:1/speech",
        "model": "configured-speech-model",
        "api_key": "",
    }
    modules["audio_input"]["transcription"] = {
        "enabled": True,
        "endpoint": "http://127.0.0.1:1/transcriptions",
        "model": "configured-transcription-model",
        "api_key": "",
    }
    config["modules"] = modules
    config["modules_directory"] = str(ROOT / "modules")
    path = tmp_path / "degraded.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert config["modules"]["stream_control"]["polls"]["enabled"] is False

    environ = {
        **_sample_environ(path),
        "AUDIO_RECORDER": str(not_executable),
        "AUDIO_PLAYER": str(not_executable),
        "AUDIO_CHIME_PATH": str(chime),
        "SCENE_WEBSOCKET_URL": "ws://127.0.0.1:1",
    }
    clock = ManualClock()
    runner = RecordingPlayerRunner()
    seams = {
        "audio_input": {"_sleeper": clock.sleep},
        "audio_output": {"_sleeper": clock.sleep, "_player_runner": runner},
        "stream_control": {"_sleeper": clock.sleep},
    }
    started = await asyncio.wait_for(
        _start_profile(path, environ, model=_chat_scenario_model(), device_seams=seams),
        timeout=60,
    )
    try:
        assert started.report.status == 0, (started.report, started.reported)
        degraded = _degraded_by_module(started)
        assert sorted(degraded) == ["audio_input", "audio_output", "stream_control"]
        assert sum(len(payloads) for payloads in degraded.values()) == 3
        assert {
            module: sorted(payload["capabilities"])
            for module, (payload,) in degraded.items()
        } == {
            "audio_input": ["audio.capture"],
            "audio_output": ["audio.play", "audio.speak"],
            "stream_control": ["stream.scene.set"],
        }
        for (payload,) in degraded.values():
            _assert_value_free(payload["reason"], environ, str(tmp_path))

        registry = started.registry
        assert not PHASE2_ACTIONS & set(registry.registered_ready())
        assert set(registry.registered_ready()) == PHASE1_ACTIONS
        assert set(registry.discovered()) == PHASE1_ACTIONS | PHASE2_ACTIONS
        # No probe waits on the clock once startup is over.
        assert clock.now == ManualClock().now

        completed = await started.chat_scenario()
        _assert_the_chat_scenario_ran(started, completed)
        assert _offered_tools(started.model) == PHASE1_READ_ACTIONS
        assert runner.starts == []
    finally:
        await started.stop()


# --------------------------------------------------------------------------- #
# AC41: transport selection by enabled modules
# --------------------------------------------------------------------------- #


async def _bindings_after_preparation(path: Path) -> tuple[Any, list[Any], list[str]]:
    environ = _sample_environ(path)
    runtime, activations, diagnostics = await _activate_example(environ, path)
    try:
        await _prepare_all(activations)
    except BaseException:
        await _close_all(activations)
        raise
    return runtime, activations, diagnostics


async def test_server_profile_binds_the_device_actions_to_the_proxy_and_polls_locally() -> None:
    """AC32 (R9), keeping AC41 (R7, decision 7) — replaces
    ``test_server_profile_binds_screen_capture_to_the_proxy_provider``, whose
    ``actions: [screen.capture]`` allowlist R9 extends to the five device
    actions. No enabled manifest serves any of the five — ``capture``,
    ``audio_input`` and ``audio_output`` are not enabled and
    ``stream_control`` runs no scene provider (``kind: none``), so it only
    declares ``stream.scene.set`` — and the proxy declares each from the
    catalog at preparation and binds itself, the one provider, over
    ``*/*/<scope>``; each waits for a pairing to become ready. The same
    ``stream_control`` binds ``stream.poll.create`` for ``twitch/*/poll``
    through the poll service the platform published, ready at once. No
    ambiguity diagnostic, and the brain module took no part in the choice."""

    config = _read_yaml(SERVER_PROFILE)
    assert not {"capture", "audio_input", "audio_output"} & set(config["enabled_modules"])
    assert config["modules"]["proxy"]["actions"] == SERVER_PROXY_ACTIONS
    assert config["modules"]["stream_control"]["scenes"]["provider"]["kind"] == "none"
    assert config["modules"]["stream_control"]["polls"]["enabled"] is True

    runtime, activations, diagnostics = await _bindings_after_preparation(SERVER_PROFILE)
    try:
        registry = runtime.context.actions
        for name in SERVER_PROXY_ACTIONS:
            (binding,) = registry.bindings(name)
            assert binding.provider_name == PROXY_PROVIDER, name
            assert binding.module == "proxy", name
            assert binding.destination.scope == ACTION_SCOPES[name], name
            assert binding.destination.platform == "*" and binding.destination.channel_id == "*"
            assert name in registry.discovered()
            assert name not in registry.registered_ready()
        (poll,) = registry.bindings("stream.poll.create")
        assert poll.module == "stream_control"
        assert poll.destination == Destination("twitch", "*", "poll")
        assert "stream.poll.create" in registry.registered_ready()
        assert diagnostics == []
        assert events_of(runtime.bus, TRACE_MODULE_DEGRADED) == []
        # Every other granted action is served locally, by its own module.
        assert {b.module for b in registry.bindings("chat.write")} == {"twitch"}
        assert {b.module for b in registry.bindings("chat.read")} == {"chat_context"}
        assert {b.module for b in registry.bindings("users.read")} == {"users"}
        assert set(registry.discovered()) == PHASE1_ACTIONS | PHASE2_ACTIONS
    finally:
        await _close_all(activations)


async def test_pc_profile_binds_screen_capture_to_the_local_capture_provider() -> None:
    """AC41 (R7, decision 7): with ``capture`` enabled and no ``proxy``, the
    local module declares ``screen.capture`` at activation and binds itself
    at preparation, ready at once; the same registry, the same brain."""

    config = _read_yaml(PC_PROFILE)
    assert "proxy" not in config["enabled_modules"]

    runtime, activations, diagnostics = await _bindings_after_preparation(PC_PROFILE)
    try:
        registry = runtime.context.actions
        (binding,) = registry.bindings(SCREEN_CAPTURE)
        assert binding.provider_name == SCREEN_CAPTURE_PROVIDER
        assert binding.module == "capture"
        assert binding.destination.scope == "capture"
        assert SCREEN_CAPTURE in registry.registered_ready()
        assert diagnostics == []
    finally:
        await _close_all(activations)


def _brain_profile_with_both_providers(tmp_path: Path) -> Path:
    """The server profile with the PC profile's ``capture`` module enabled
    too, listed before the brain as the PC profile lists it — the one
    change a user would make to serve the screen twice."""

    config = dict(_read_yaml(SERVER_PROFILE))
    enabled = list(config["enabled_modules"])
    enabled.insert(enabled.index("brain"), "capture")
    config["enabled_modules"] = enabled
    config["modules"] = {
        **config["modules"],
        "capture": dict(_read_yaml(PC_PROFILE)["modules"]["capture"]),
    }
    config["modules_directory"] = str(ROOT / "modules")
    path = tmp_path / "both.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


async def test_capture_and_proxy_together_fail_preparation_with_the_ambiguity_diagnostic(
    tmp_path: Path,
) -> None:
    """AC41 (R7): enabling ``capture`` and ``proxy`` with ``actions:
    [screen.capture]`` in one brain profile fails preparation through the
    real loader and the real coordinator; the diagnostic names
    ``screen.capture`` and both providers, ``module.degraded`` carries it,
    the action is never ready and no listener was opened."""

    path = _brain_profile_with_both_providers(tmp_path)
    environ = _sample_environ(path)
    runtime, activations, diagnostics = await _activate_example(environ, path)
    reported: list[str] = []
    coordinator = application._coordinator(activations, runtime, reported.append)

    report = await coordinator.start()
    try:
        assert report.status != 0
        failures = [*report.failures, *report.diagnostics, *reported, *diagnostics]
        ambiguous = [
            message
            for message in failures
            if SCREEN_CAPTURE in message
            and f"'{SCREEN_CAPTURE_PROVIDER}'" in message
            and f"'{PROXY_PROVIDER}'" in message
        ]
        assert ambiguous, failures
        assert all("Ambiguous binding" in message for message in ambiguous)
        assert SCREEN_CAPTURE not in runtime.context.actions.registered_ready()
        degraded = [
            event["payload"]["reason"] for event in events_of(runtime.bus, TRACE_MODULE_DEGRADED)
        ]
        assert any(
            SCREEN_CAPTURE in reason
            and f"'{SCREEN_CAPTURE_PROVIDER}'" in reason
            and f"'{PROXY_PROVIDER}'" in reason
            for reason in degraded
        ), degraded
        # No configured value leaks into any of it.
        for message in [*failures, *degraded]:
            assert all(value not in message for value in environ.values())
            assert str(tmp_path) not in message
    finally:
        await coordinator.stop()


# --------------------------------------------------------------------------- #
# AC43: install into an empty target, discover the shipped manifests
# --------------------------------------------------------------------------- #


def _pip_available() -> tuple[bool, str]:
    """Whether ``<sys.executable> -m pip`` answers ``--version``."""

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout).strip()
    return True, completed.stdout.strip()


def _subprocess_environment(target: Path) -> dict[str, str]:
    """A process environment seeing the installed target and not the checkout."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONSAFEPATH")
    }
    environment["PYTHONPATH"] = str(target)
    # pip must not reach an index: nothing is downloaded (the backend comes
    # from the test environment, dependencies are skipped) and the version
    # self-check is a network request too.
    environment["PIP_NO_INDEX"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return environment


DISCOVERY_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import site
    import sys

    excluded = [os.path.realpath(entry) for entry in sys.argv[1:-1]]
    # The interpreter's own environment stays whatever its location: a
    # virtualenv created inside the checkout (``.venv``) has its
    # site-packages under an excluded root, and the target is installed
    # with ``--no-deps``, so its dependencies come from there.
    preserved = [
        os.path.realpath(entry)
        for entry in (
            sys.prefix,
            sys.exec_prefix,
            sys.base_prefix,
            sys.base_exec_prefix,
            *site.getsitepackages(),
            site.getusersitepackages(),
        )
    ]

    def is_under(real, roots):
        return any(real == root or real.startswith(root + os.sep) for root in roots)

    def is_excluded(entry):
        real = os.path.realpath(entry or os.getcwd())
        return is_under(real, excluded) and not is_under(real, preserved)

    # The checkout (and its copy) is dropped from the path wherever it
    # appears — the empty entry (the working directory) is kept, since that
    # directory is neither — so what is imported is what was installed.
    sys.path[:] = [entry for entry in sys.path if not is_excluded(entry)]

    import core.main as application
    from core.bus import EventBus
    from core.loader import ModuleLoader

    config_path = sys.argv[-1]
    config = application.load_config(config_path)
    loader = ModuleLoader(EventBus(), config["modules_directory"])
    print(json.dumps({
        "core": os.path.realpath(application.__file__),
        "modules_directory": os.path.realpath(config["modules_directory"]),
        "manifests": sorted(loader._discover()),
    }))
    """
)


#: What a pristine copy of the checkout leaves out: build artefacts an earlier
#: build left in the tree (``build/``, ``*.egg-info`` with its recorded
#: ``SOURCES.txt``), which setuptools would otherwise ship again whatever the
#: package-data globs name — the very defect this test guards — plus the
#: test tree, the environments and the VCS data, none of which is packaged.
_NOT_COPIED = ("tests", "build", "__pycache__")


def _ignore_unpackaged(directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in _NOT_COPIED or name.startswith(".") or name.endswith(".egg-info")
    }


def _pristine_checkout(destination: Path) -> Path:
    """The checkout's sources copied to *destination*, without build artefacts."""

    shutil.copytree(ROOT, destination, ignore=_ignore_unpackaged)
    assert (destination / "pyproject.toml").is_file()
    assert sorted(
        child.name for child in (destination / "modules").iterdir()
        if (child / "module.yaml").is_file()
    ) == sorted(SHIPPED_MANIFESTS)
    assert not (destination / "build").exists()
    assert not list(destination.glob("*.egg-info"))
    return destination


def _run_outside_the_checkout(
    argv: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    assert not str(cwd.resolve()).startswith(str(ROOT.resolve()))
    return subprocess.run(
        argv, cwd=str(cwd), env=environment, capture_output=True, text=True, timeout=300, check=False
    )


def test_installed_distribution_discovers_the_shipped_manifests_and_answers_help(
    tmp_path: Path,
) -> None:
    """AC43 (R8), AC33 (R9): ``pip install --no-deps --no-build-isolation
    --target <empty dir> <checkout>`` — one command, network-free because
    the build backend is imported from the test environment — then, from a
    directory outside the checkout with only the target on ``PYTHONPATH``
    and the checkout removed from ``sys.path``, a configuration with
    ``modules_directory: builtin`` discovers exactly the 11 shipped
    manifests (phase 2 adds three: the former count of 8 is allowlisted;
    phase 3's R8 ships ``clips`` from plan step P9 on: 12, allowlisted)
    and the installed console script answers ``--help`` with status 0.

    The checkout is installed from a pristine copy of its sources: pip
    builds in the source tree, and the ``build/`` directory and the
    ``*.egg-info`` it leaves behind would ship the manifests of an earlier
    build whatever the package-data globs name, which is the defect this
    test guards (P6) — and would litter the working tree.

    The test skips only when ``pip`` is unavailable, naming that reason. A
    build backend that cannot be imported (the ``test`` extra of
    ``pyproject.toml`` declares ``setuptools>=69`` for this), an install
    error, a wrong manifest count or a failing launcher is a failure.
    """

    available, detail = _pip_available()
    if not available:
        pytest.skip(f"pip is unavailable for {sys.executable}: {detail}")

    if importlib.util.find_spec("setuptools") is None:
        pytest.fail(
            "the build backend `setuptools` is not importable: install the `test` "
            "extra of pyproject.toml (it declares setuptools>=69), since the "
            "distribution is built without build isolation"
        )

    target = tmp_path / "target"
    target.mkdir()
    assert list(target.iterdir()) == []
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    checkout = _pristine_checkout(tmp_path / "checkout")
    environment = _subprocess_environment(target)

    install = _run_outside_the_checkout(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--target",
            str(target),
            str(checkout),
        ],
        cwd=elsewhere,
        environment=environment,
    )
    if install.returncode != 0:
        pytest.fail(
            "pip install of the checkout failed (the build backend comes from the "
            "test environment: the `test` extra declares setuptools>=69):\n"
            + install.stdout[-2000:]
            + install.stderr[-4000:]
        )

    config_path = elsewhere / "builtin.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "modules_directory": application.MODULES_DIRECTORY_BUILTIN,
                "enabled_modules": [],
                "modules": {},
            }
        ),
        encoding="utf-8",
    )
    # Neither the checkout nor its copy may be what the subprocess imports.
    discovery = _run_outside_the_checkout(
        [sys.executable, "-c", DISCOVERY_SCRIPT, str(ROOT), str(checkout), str(config_path)],
        cwd=elsewhere,
        environment=environment,
    )
    assert discovery.returncode == 0, discovery.stderr
    report = json.loads(discovery.stdout.strip().splitlines()[-1])
    installed = str(target.resolve())
    assert report["core"].startswith(installed + os.sep), report
    assert report["modules_directory"] == str((target / "modules").resolve()), report
    assert report["manifests"] == sorted(SHIPPED_MANIFESTS), report
    assert len(report["manifests"]) == 12
    assert set(report["manifests"]) == set(MODULE_NAMES)

    launcher = target / "bin" / CONSOLE_SCRIPT
    assert launcher.is_file(), sorted(str(p.relative_to(target)) for p in target.rglob("*"))
    # The launcher is not on PATH: it is run through the interpreter from the
    # installed console-script entry point.
    help_run = _run_outside_the_checkout(
        [sys.executable, str(launcher), "--help"], cwd=elsewhere, environment=environment
    )
    assert help_run.returncode == 0, help_run.stderr
    assert "--check-config" in help_run.stdout
    assert "--config" in help_run.stdout


def test_install_test_skips_only_when_pip_is_unavailable() -> None:
    """AC43: the install test has exactly one skip, guarded by the ``pip``
    probe and naming pip; no ``skipif``, no ``xfail`` and no other skip can
    turn a missing backend, an install error or a wrong count into a pass."""

    source = inspect.getsource(
        test_installed_distribution_discovers_the_shipped_manifests_and_answers_help
    )
    skips = [line.strip() for line in source.splitlines() if "pytest.skip(" in line]
    assert len(skips) == 1
    assert "pip is unavailable" in skips[0]
    assert "skipif" not in source and "xfail" not in source
    assert "importorskip" not in source
    assert source.count("pytest.fail(") >= 2
    # The skip is decided by the pip probe alone.
    skip_index = source.index("pytest.skip(")
    assert "_pip_available()" in source[:skip_index]
    assert "find_spec(\"setuptools\")" in source[skip_index:]


# --------------------------------------------------------------------------- #
# AC33: packaging of the three phase 2 manifests
# --------------------------------------------------------------------------- #


def test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency() -> None:
    """AC33 (R9): ``pyproject.toml`` carries the ``modules.audio_output``,
    ``modules.audio_input`` and ``modules.stream_control`` package-data
    lines beside the eight of phase 1 — one per shipped manifest, the 12
    the install test discovers now that phase 3's R8 ships ``clips`` (plan
    step P9, allowlisted running value) — and the runtime dependency list is
    still ``aiohttp`` and ``PyYAML`` only."""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]
    for name in PHASE2_PACKAGE_DATA:
        assert package_data[name] == ["module.yaml"], name
    shipped = sorted(
        child.name for child in (ROOT / "modules").iterdir() if (child / "module.yaml").is_file()
    )
    assert shipped == sorted(SHIPPED_MANIFESTS) == sorted(MODULE_NAMES)
    assert len(shipped) == 12
    assert sorted(key for key in package_data if key.startswith("modules.")) == sorted(
        f"modules.{name}" for name in shipped
    )
    assert sorted(
        re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower()
        for spec in project["project"]["dependencies"]
    ) == ["aiohttp", "pyyaml"]
