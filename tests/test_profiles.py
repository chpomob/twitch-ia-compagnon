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
* the distribution's package data, read as the build backend reads it,
  ships exactly the shipped manifests, ``modules_directory: builtin``
  discovers them (phase 2's R9, AC33: 8 → 11, allowlisted; 21 today) and the
  console script answers ``--help`` (AC43) — in-process since phase 8's R9
  (AC33, plan P15e), which superseded the ``pip install`` into an empty
  target. A wrong manifest set or a missing script is a failure;
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
  dependency (AC33);
* phase 3 (R1, R6, R8; plan step P22): ``--check-config`` accepts the
  presence profile (``presence.yaml.example``) with its own environment and
  opens 0 sockets (AC6), and refuses with exit 2, naming the field, each
  temporary variant of it with an oversized or malformed presence setting
  (AC2) or watch setting (AC30).

No positive-duration sleep: the checks run on fakes. No process is started
(phase 8 R9, AC33).
"""

from __future__ import annotations

import asyncio
import copy
import fnmatch
import importlib.metadata
import importlib.util
import inspect
import os
import re
import socket
import sys
import warnings
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
from core.bus import EventBus
from core.loader import ModuleLoader
from core.contracts import TRACE_MODULE_DEGRADED, ActionCall, Destination
from conftest import (
    ManualClock,
    RecordingPlayerRunner,
    UnreachableSession,
    events_of,
    wav_bytes,
)
from modules.audio_input import TRANSCRIPTION_DEGRADED_REASON
from modules.audio_output import SYNTHESIS_NOT_CONFIGURED_REASON, SYNTHESIS_PROBE_FAILED_REASON
from modules.capture import SCREEN_CAPTURE_PROVIDER
from modules.proxy import PROVIDER_NAME as PROXY_PROVIDER
from test_presence_pack import PRESENCE_PROFILE, presence_environ
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
#: phase 3 manifests as each step adds one (allowlisted; P9: ``clips``, 12;
#: P10: ``viewer_memory``, 13; P14: ``moderation``, 14; P16: ``watch``, 15;
#: P18: ``kick``, 16; P20: ``youtube``, 17), and phase 5's automation bridge
#: (R10, plan P12, allowlisted: 17 → 18), and phase 7's web knowledge (R1, R9,
#: plan P1, allowlisted: 18 → 19), and phase 8's operator input (R6, plan P9,
#: allowlisted: 19 → 20) and timer input (R8, plan P11, allowlisted: 20 → 21).
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
    "viewer_memory",
    "moderation",
    "watch",
    "kick",
    "youtube",
    "streamerbot",
    "web",
    "operator",
    "timer",
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
# Phase 3 (R1, R6, R8; plan step P22): --check-config on the presence profile
# --------------------------------------------------------------------------- #


async def test_check_config_accepts_the_presence_profile_and_opens_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6 (check-config clause), R8: ``--check-config presence.yaml.example``
    with the profile's own environment exits 0, reports 0 diagnostics and
    opens 0 sockets."""

    environ = presence_environ(tmp_path)
    diagnostics: list[str] = []
    _refuse_loop_endpoints(monkeypatch)
    opened = _count_sockets(monkeypatch)

    status = await application.check_config(
        PRESENCE_PROFILE, environ=environ, diagnostic_reporter=diagnostics.append
    )

    assert status == 0, diagnostics
    assert diagnostics == []
    assert opened == []


def test_main_check_config_exits_zero_on_the_presence_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6 through the command line: ``--config presence.yaml.example
    --check-config`` with the profile's environment exported returns 0."""

    for name, value in presence_environ(tmp_path).items():
        monkeypatch.setenv(name, value)

    assert application.main(["--config", str(PRESENCE_PROFILE), "--check-config"]) == 0


def _presence_variant(directory: Path, change: Any) -> Path:
    """The presence profile with *change* applied to a copy of its settings,
    written next to *directory* with its modules directory made absolute."""

    config = copy.deepcopy(dict(_read_yaml(PRESENCE_PROFILE)))
    config["modules_directory"] = str(ROOT / "modules")
    change(config["modules"])
    path = directory / "presence-variant.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _set_persona(modules: dict[str, Any]) -> None:
    modules["brain"]["persona"] = "p" * 2001


def _seventeen_routes(modules: dict[str, Any]) -> None:
    routes = modules["brain"]["routes"]
    routes.extend(
        {"name": f"extra-{index}", "match": {"kinds": ["message"], "command": f"!x{index}"}}
        for index in range(17 - len(routes))
    )
    assert len(routes) == 17


def _duplicate_route_name(modules: dict[str, Any]) -> None:
    routes = modules["brain"]["routes"]
    routes.append({"name": routes[0]["name"], "match": {"kinds": ["follow"]}})


def _long_command(modules: dict[str, Any]) -> None:
    modules["brain"]["routes"][1]["match"]["command"] = "!" + "c" * 32


def _watch(**changes: Any) -> Any:
    def change(modules: dict[str, Any]) -> None:
        modules["watch"].update(changes)

    return change


#: AC2 (check-config half): each presence variant and the field it names.
PRESENCE_VARIANTS = {
    "persona-2001": (_set_persona, "persona", 2001),
    "17-routes": (_seventeen_routes, "routes", None),
    "duplicate-name": (_duplicate_route_name, "routes[9].name", None),
    "command-33": (_long_command, "routes[1].match.command", 33),
}
#: AC30 (check-config half): each watch variant and the field it names.
WATCH_VARIANTS = {
    "interval-14": (_watch(interval_seconds=14), "interval_seconds"),
    "ticks-241": (_watch(max_ticks_per_hour=241), "max_ticks_per_hour"),
    "ticks-100-at-60": (
        _watch(max_ticks_per_hour=100, interval_seconds=60),
        "max_ticks_per_hour",
    ),
    "active-59": (_watch(max_active_seconds=59), "max_active_seconds"),
    "5-channels": (
        _watch(channels=[f"twitch/channel-{index}" for index in range(5)]),
        "channels",
    ),
}


async def _check_variant(path: Path, environ: Mapping[str, str]) -> tuple[int, list[str]]:
    diagnostics: list[str] = []
    status = await application.check_config(
        path, environ=environ, diagnostic_reporter=diagnostics.append
    )
    return status, diagnostics


@pytest.mark.parametrize("variant", sorted(PRESENCE_VARIANTS))
async def test_ac2_check_config_refuses_each_oversized_presence_setting_naming_it(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2 (R1; check-config half, the validator half is
    ``tests/test_brain.py``): a temporary presence profile with a
    2001-character persona, 17 routes, two routes with the same name or a
    33-character command exits 2 with one diagnostic naming the brain and
    the field — never the configured value — and opens 0 sockets."""

    change, field_name, _length = PRESENCE_VARIANTS[variant]
    path = _presence_variant(tmp_path, change)
    opened = _count_sockets(monkeypatch)

    status, diagnostics = await _check_variant(path, presence_environ(tmp_path))

    assert status == 2
    assert len(diagnostics) == 1, diagnostics
    assert diagnostics[0].startswith("module 'brain':"), diagnostics
    assert f"field {field_name!r}:" in diagnostics[0], diagnostics
    assert "p" * 50 not in diagnostics[0] and "c" * 32 not in diagnostics[0]
    assert opened == []


@pytest.mark.parametrize("variant", sorted(WATCH_VARIANTS))
async def test_ac30_check_config_refuses_each_invalid_watch_setting_naming_it(
    variant: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC30 (R6; check-config half, the validator half is
    ``tests/test_watch.py``): ``interval_seconds: 14``,
    ``max_ticks_per_hour: 241``, ``max_ticks_per_hour: 100`` with
    ``interval_seconds: 60``, ``max_active_seconds: 59`` and 5 channels each
    make ``--check-config`` on the presence profile exit 2 with one
    diagnostic naming the watch module and the field, and open 0 sockets."""

    change, field_name = WATCH_VARIANTS[variant]
    path = _presence_variant(tmp_path, change)
    opened = _count_sockets(monkeypatch)

    status, diagnostics = await _check_variant(path, presence_environ(tmp_path))

    assert status == 2
    assert len(diagnostics) == 1, diagnostics
    # The bounds the manifest schema declares are refused by the loader under
    # the `settings.` path, the cross-checks by the module's own hook.
    assert diagnostics[0].startswith("module 'watch': field "), diagnostics
    assert re.search(rf"field '(settings\.)?{re.escape(field_name)}':", diagnostics[0]), diagnostics
    assert "channel-4" not in diagnostics[0]
    assert opened == []


async def test_the_presence_variants_differ_from_the_profile_only_by_their_change(
    tmp_path: Path,
) -> None:
    """The variants above are the shipped profile plus one change: the same
    profile rewritten with no change passes ``--check-config``, so each exit
    2 is the change's."""

    path = _presence_variant(tmp_path, lambda modules: None)
    status, diagnostics = await _check_variant(path, presence_environ(tmp_path))
    assert (status, diagnostics) == (0, [])


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC43 (arbiter decision 22/09): with the speech and transcription
    endpoints CONFIGURED but UNREACHABLE at ``prepare`` — a closed loopback
    port, the shipped transports, every bound on an injected clock nobody
    advances — the same not-ready naming appears: ``audio.speak`` unbound
    and named with a value-free reason, the transcription unavailable, no
    request answered, and startup succeeds. AC33: the closed port is an
    ``aiohttp.ClientSession`` refusing the connection beneath the shipped
    transports."""

    attempts = UnreachableSession.install(monkeypatch, "http://127.0.0.1:1/")
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
        assert set(attempts) == {
            "http://127.0.0.1:1/speech",
            "http://127.0.0.1:1/transcriptions",
        }
    finally:
        await started.stop()
        await edges.close()


# --------------------------------------------------------------------------- #
# AC31: the full PC profile, every phase 2 dependency unavailable
# --------------------------------------------------------------------------- #


async def test_ac31_the_full_pc_profile_degrades_three_modules_and_still_chats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    only. Nothing was played. AC33: the closed port is an
    ``aiohttp.ClientSession`` refusing the connection beneath the shipped
    transports."""

    attempts = UnreachableSession.install(monkeypatch, "http://127.0.0.1:1/", "ws://127.0.0.1:1")
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
        assert "ws://127.0.0.1:1" in attempts
        assert set(attempts) <= {
            "ws://127.0.0.1:1",
            "http://127.0.0.1:1/speech",
            "http://127.0.0.1:1/transcriptions",
        }
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


def _setuptools_table() -> dict[str, Any]:
    """The ``[tool.setuptools]`` table the build backend builds the
    distribution from.

    Read through ``setuptools.config.pyprojecttoml.read_configuration`` —
    the backend's own reader, without expansion — and checked equal to the
    table ``tomllib`` reads from the same file. A backend that cannot be
    imported is a failure, never a fallback: the ``test`` extra of
    ``pyproject.toml`` declares ``setuptools>=69`` for this.
    """

    if importlib.util.find_spec("setuptools") is None:
        pytest.fail(
            "the build backend `setuptools` is not importable: install the `test` "
            "extra of pyproject.toml (it declares setuptools>=69)"
        )
    # Before 3.12 setuptools' distutils shim turns itself off once anything
    # looked ``pip`` up (``tests/test_installation_doc.py`` does), and its
    # first import then fails; the stdlib ``distutils`` those versions carry
    # serves the read as well. 3.12+ has no stdlib copy and no such switch.
    with pytest.MonkeyPatch.context() as patch, warnings.catch_warnings():
        if sys.version_info < (3, 12):
            patch.setenv("SETUPTOOLS_USE_DISTUTILS", "stdlib")
            warnings.simplefilter("ignore")
        from setuptools.config.pyprojecttoml import read_configuration

    pyproject = ROOT / "pyproject.toml"
    table = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["setuptools"]
    read = read_configuration(str(pyproject), expand=False)["tool"]["setuptools"]
    assert read["package-data"] == table["package-data"]
    assert read["packages"] == table["packages"]
    return dict(read)


def _packages(include: list[str]) -> list[str]:
    """The packages ``packages.find`` selects under the checkout: every
    directory with an ``__init__.py``, reached through packages only, whose
    dotted name an *include* pattern matches."""

    found: list[str] = []

    def walk(directory: Path, prefix: str) -> None:
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or not (child / "__init__.py").is_file():
                continue
            name = f"{prefix}{child.name}"
            if child.name == "__pycache__" or not name.replace(".", "_").isidentifier():
                continue
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in include):
                found.append(name)
            walk(child, f"{name}.")

    walk(ROOT, "")
    return found


def _packaged_manifests() -> dict[str, set[Path]]:
    """Every ``module.yaml`` the distribution ships, by the package-data key
    whose glob selects it, resolved against the package's directory."""

    table = _setuptools_table()
    package_data: dict[str, list[str]] = table["package-data"]
    packages = _packages(table["packages"]["find"]["include"])
    shipped: dict[str, set[Path]] = {}
    for key, globs in package_data.items():
        if key not in packages:
            pytest.fail(f"package-data names {key}, which packages.find does not ship")
        directory = ROOT.joinpath(*key.split("."))
        shipped[key] = {
            path.resolve()
            for pattern in globs
            for path in directory.glob(pattern)
            if path.name == "module.yaml"
        }
    return shipped


def test_installed_distribution_discovers_the_shipped_manifests_and_answers_help(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC43 (R8), AC33 (R9): the distribution ships exactly the shipped
    manifests — 17 was phase 3's final value, AC40 (R8), pinned by plan step P23
    (allowlisted): phase 2's 11 (its three superseded phase 1's 8) plus
    ``clips``, ``viewer_memory``, ``moderation``, ``watch``, ``kick`` and
    ``youtube`` — superseded by phase 5's R10 (plan step P12, AC41,
    allowlisted): 18 with the automation bridge — superseded by phase 7's R1,
    R9 (plan step P1, allowlisted): 19 with ``web`` — superseded by phase 8's
    R6 (plan step P9, allowlisted): 20 with ``operator`` — superseded by phase
    8's R8 (plan step P11, allowlisted): 21 with ``timer`` — a configuration
    with ``modules_directory: builtin`` discovers them, and the console script
    answers ``--help`` with status 0.

    Phase 8's R9 (AC33, plan P15e, allowlisted) supersedes the ``pip install
    --target`` into an empty directory and the child interpreters run from
    there: no test starts a process. Two in-process checks replace them. The
    package data is read as the build backend reads it (``[tool.setuptools]``
    of ``pyproject.toml``) and every glob is resolved against its package —
    the packaged manifests are exactly the shipped ones, each per-module line
    selecting its own, so a build ships them whatever an earlier build left
    in the tree (P6) — and the builtin directory discovers exactly those.
    The console script is the one the installed distribution's entry points
    name, called with ``--help``. What is lost (a real install) is recorded
    as fake-only in the phase summary (D8).
    """

    shipped = _packaged_manifests()
    manifests = {
        ROOT / "modules" / name / "module.yaml": name for name in SHIPPED_MANIFESTS
    }
    packaged = set().union(*shipped.values())
    if packaged != {path.resolve() for path in manifests}:
        pytest.fail(f"the package data ships {sorted(map(str, packaged))}")
    for name in SHIPPED_MANIFESTS:
        own = (ROOT / "modules" / name / "module.yaml").resolve()
        assert shipped[f"modules.{name}"] == {own}, name
    assert sorted(manifests.values()) == sorted(SHIPPED_MANIFESTS)
    assert len(packaged) == 21
    assert set(manifests.values()) == set(MODULE_NAMES)

    config_path = tmp_path / "builtin.yaml"
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
    config = application.load_config(config_path)
    directory = Path(config["modules_directory"]).resolve()
    assert directory == (ROOT / "modules").resolve(), directory
    discovered = sorted(ModuleLoader(EventBus(), str(directory))._discover())
    assert discovered == sorted(SHIPPED_MANIFESTS)
    assert {directory / name / "module.yaml" for name in discovered} == packaged

    scripts = [
        point
        for point in importlib.metadata.entry_points(group="console_scripts")
        if point.name == CONSOLE_SCRIPT
    ]
    if len(scripts) != 1:
        pytest.fail(f"{CONSOLE_SCRIPT}: the installed distribution names no console script")
    (script,) = scripts
    assert script.value == "core.main:main"
    capsys.readouterr()
    with pytest.raises(SystemExit) as exited:
        script.load()(["--help"])
    help_output = capsys.readouterr().out
    assert exited.value.code == 0, help_output
    assert "--check-config" in help_output
    assert "--config" in help_output


def test_install_test_never_skips_and_fails_loudly() -> None:
    """AC43: the install test cannot turn a wrong count into a pass.

    Supersedes ``test_install_test_skips_only_when_pip_is_unavailable``
    (phase 8 R9, AC33, plan P15e): the install test no longer runs ``pip``,
    so the one skip it had (``pip`` unavailable) is gone with it. It now has
    no skip at all — no ``skipif``, no ``xfail``, no import-or-skip — and a
    package-data glob outside the shipped packages, a wrong manifest set or
    a missing console script is a failure, and so is a build backend that
    cannot be imported (no fallback to a raw TOML read)."""

    source = inspect.getsource(
        test_installed_distribution_discovers_the_shipped_manifests_and_answers_help
    )
    assert "pytest.skip(" not in source
    assert "skipif" not in source and "xfail" not in source
    assert "importorskip" not in source
    assert source.count("pytest.fail(") >= 2
    assert "pytest.fail(" in inspect.getsource(_packaged_manifests)
    backend = inspect.getsource(_setuptools_table)
    assert 'find_spec("setuptools")' in backend and "pytest.fail(" in backend
    assert "is not None" not in backend


# --------------------------------------------------------------------------- #
# AC33: packaging of the three phase 2 manifests
# --------------------------------------------------------------------------- #


def test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency() -> None:
    """AC33 (R9), AC40 (R8): ``pyproject.toml`` carries the
    ``modules.audio_output``, ``modules.audio_input`` and
    ``modules.stream_control`` package-data lines beside the eight of phase
    1, and phase 3's final value pinned by plan step P23 (allowlisted):
    exactly 17 ``modules.<name>`` lines, one per shipped manifest — phase
    3's R8 adds ``clips`` (P9), ``viewer_memory`` (P10), ``moderation``
    (P14), ``watch`` (P16), ``kick`` (P18) and ``youtube`` (P20); phase 5's
    R10 (plan step P12, AC41, allowlisted) adds ``streamerbot``: 18; phase
    7's R1, R9 (plan step P1, allowlisted) adds ``web``: 19; phase 8's R6
    (plan step P9, allowlisted) adds ``operator``: 20; phase 8's R8 (plan
    step P11, allowlisted) adds ``timer``: 21. The
    runtime dependency list is still exactly ``aiohttp`` and ``PyYAML``
    (unchanged)."""

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]
    for name in PHASE2_PACKAGE_DATA:
        assert package_data[name] == ["module.yaml"], name
    shipped = sorted(
        child.name for child in (ROOT / "modules").iterdir() if (child / "module.yaml").is_file()
    )
    assert shipped == sorted(SHIPPED_MANIFESTS) == sorted(MODULE_NAMES)
    assert len(shipped) == 21
    module_lines = sorted(key for key in package_data if key.startswith("modules."))
    assert len(module_lines) == 21
    assert module_lines == sorted(f"modules.{name}" for name in shipped)
    for name in module_lines:
        assert package_data[name] == ["module.yaml"], name
    assert sorted(
        re.match(r"[A-Za-z0-9_.-]+", spec).group(0).lower()
        for spec in project["project"]["dependencies"]
    ) == ["aiohttp", "pyyaml"]
