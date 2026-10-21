"""Brain and PC agent on one in-memory link, from the shipped profiles (R6, R7; AC39).

Both runtimes are ``core.main.run`` over a profile file, in this test's event
loop — the brain under a profile derived from ``config.server.yaml.example``
(the chat input replaced by the ``fakeplatform`` fixture, whose scripted
``feed`` fires on every ``agent.status`` the paired agent sends; TLS removed
for a loopback listener; the model endpoint answered by a fake Chat
Completions session; the audit written to a temp file; ``modules_directory``
a temp copy of the shipped and fixture modules, plan decision 7) and the agent
under a profile derived from ``agent.yaml.example`` (``ws://127.0.0.1:<port>``,
a ``file`` source for the end-to-end scenarios and a ``gated`` ``command``
source whose program this test holds).

Phase 8 (R9, AC33, plan P15e) supersedes the two ``python -m core.main``
children on loopback: no test opens a socket or starts a process. Each
profile's file is loaded by the real entry point, which activates and starts
its modules through the real loader and phase coordinator; the edges the
process cannot own are the seams the modules already have, injected into the
loaded configuration. The brain's ``proxy`` listens through its
``_server_factory`` seam: every dial is a :class:`MemoryWebSocketPair` whose
server end the listener's own connection handler serves, the way
``tests/test_proxy.py`` joins them. The agent's ``agent_link`` dials through
its ``_connector`` seam, once per runtime — a runtime that lost its link
does not dial again — and the ``gated`` source's program runs through the
capture module's ``_subprocess_runner`` seam. What is lost — that two
operating-system processes cooperate — is named in the phase summary as an
in-process topology (D8).

**The capture gate.** The gated program reports to :class:`CaptureGate` as
soon as the agent's capture provider starts it — that report is the
*capture-start acknowledgement* — then waits. Released with a byte, it writes
the PNG to its stdout, tells the gate ``D`` and exits 0; released with EOF it
exits 1 having written nothing; it dies, having written nothing, with an agent
that is killed. The test therefore holds a ``screen.capture``
in flight for exactly as long as it wants and kills the agent only after the
acknowledgement. What the program sent before EOF makes the kill
deterministic rather than vacuous: ``D`` before the kill would mean the
capture completed first, and the test fails on it. A kill is what ``SIGKILL``
does to the agent: its link is lost abruptly (no close handshake, close code
1006) and its runtime is cancelled.

**What is waited on.** Readiness through each runtime's ``ready`` report; the
run's progress through the model requests the fake session answers (the
brain's own transcript, tool results and image parts included); the capture
start through the gate; pairing refusals through the agent's diagnostics and
a direct dial; and the runtimes' returns. Every wait is a bounded
``asyncio.wait_for``; there is no sleep. The brain's audit file — every bus
event, the agent's ``agent.status`` events and the proxy's readiness
transitions included — is read once both runtimes have returned on their
stop, complete: the pairings, the attachment reference (never a path), the
``error proxy_disconnected`` observation of the killed capture and the
readiness transitions around it are asserted there.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.main as application
from core.contracts import (
    PROXY_CLOSE_AUTH_FAILED,
    PROXY_ERROR_AUTH_FAILED,
    PROXY_ERROR_PROXY_DISCONNECTED,
    FRAME_ERROR,
    FRAME_HELLO,
    PROXY_PROTOCOL_VERSION,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
)
from conftest import (
    WS_ABNORMAL_CLOSURE,
    FakeResponse,
    FakeSession,
    MemoryWebSocketPair,
    WSMsgType,
    final,
    png_bytes,
    tool_call,
)


ROOT = Path(__file__).resolve().parent.parent
SERVER_PROFILE = ROOT / "config.server.yaml.example"
AGENT_PROFILE = ROOT / "agent.yaml.example"
FIXTURE_MODULES = ROOT / "tests" / "fixtures" / "modules"

#: The bound of every wait (plan P22): generous next to the runs, far under
#: the brain's ``total_run_seconds`` (60) and the gated source's
#: ``command_timeout_seconds`` (60), so neither budget races the test.
WAIT_SECONDS = 30.0
HELPER_TIMEOUT_SECONDS = 60
LOOPBACK = "127.0.0.1"
#: The nominal port the proxy listens on: nothing is ever bound.
PROXY_PORT = 8765
#: The nominal model endpoint: the brain's session answers it in memory.
MODEL_ENDPOINT = f"http://{LOOPBACK}:41100/v1/chat/completions"

PLATFORM = "fake"
CHANNEL = "channel-1"
VIEWER = "viewer-1"
COMPANION = "Companion"
KEYWORD = "!ask"
#: The two feed messages, one run each, admitted in this order on every
#: pairing (same session: at most one active run, FIFO).
MESSAGE_FILE = "m-file"
MESSAGE_GATED = "m-gated"
SOURCE_FILE = "file"
SOURCE_GATED = "gated"
SCREEN_CAPTURE = "screen.capture"
CHAT_READ = "chat.read"
FINAL_TEXT = "Here is what I see."

PAIRING_TOKEN = "loopback-pairing-token"
WRONG_TOKEN = "not-the-pairing-token"
TOKEN_VARIABLE = "PROXY_PAIRING_TOKEN"
AGENT_ID = "pc-agent"
INTRUDER_ID = "pc-agent-intruder"
MODEL_NAME = "scripted"
API_KEY = "loopback-api-key"

AGENT_STATUS = "agent.status"
OBSERVATION_TRACE = "brain.run.observation"
READY_LINE = "ready"
#: What a killed runtime returns in place of a status.
KILLED = "killed"

#: What the gated program tells the gate once its PNG is on stdout.
HELPER_DONE = b"D"


# --------------------------------------------------------------------------- #
# Profiles, modules directory
# --------------------------------------------------------------------------- #


def _read_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, Mapping)
    return dict(loaded)


def _write_yaml(path: Path, config: Mapping[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(dict(config), sort_keys=False), encoding="utf-8")
    return path


def _ignore_caches(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name == "__pycache__"}


def _modules_directory(destination: Path) -> Path:
    """A copy — never a symlink, the loader ignores those — of the shipped
    modules plus the ``fakeplatform`` fixture (plan decision 7)."""

    shutil.copytree(ROOT / "modules", destination, ignore=_ignore_caches)
    shutil.copytree(
        FIXTURE_MODULES / "fakeplatform", destination / "fakeplatform", ignore=_ignore_caches
    )
    return destination


def _brain_profile(
    path: Path,
    *,
    modules_directory: Path,
    model_endpoint: str,
    proxy_port: int,
    audit_path: Path,
) -> Path:
    """``config.server.yaml.example`` as the brain of this trial runs it.

    ``twitch`` becomes ``fakeplatform`` (its feed fires on ``agent.status``);
    the served channel's trigger policy and the four grants move to
    platform ``fake``; the brain's endpoint is the fake model session; the
    audit goes to a file; the proxy listens on loopback without TLS. The
    budgets, limits and the delivery list are the profile's own.
    """

    config = _read_yaml(SERVER_PROFILE)
    served = "${TWITCH_BROADCASTER_ID}"
    policy = config["triggers"]["twitch"]["channels"][served]

    config["modules_directory"] = str(modules_directory)
    config["enabled_modules"] = [
        "fakeplatform" if name == "twitch" else name for name in config["enabled_modules"]
    ]
    modules = dict(config["modules"])
    del modules["twitch"]
    modules["fakeplatform"] = {
        "channel_ids": [CHANNEL],
        "companion_name": COMPANION,
        "feed": {
            "after_event": AGENT_STATUS,
            "messages": [
                {
                    "channel_id": CHANNEL,
                    "author": VIEWER,
                    "message_id": MESSAGE_FILE,
                    "text": f"{KEYWORD} what is on my screen?",
                },
                {
                    "channel_id": CHANNEL,
                    "author": VIEWER,
                    "message_id": MESSAGE_GATED,
                    "text": f"{KEYWORD} capture through the gate",
                },
            ],
        },
    }
    modules["brain"] = {
        **modules["brain"],
        "endpoint": model_endpoint,
        "model": MODEL_NAME,
        "api_key": API_KEY,
    }
    modules["audit"] = {**modules["audit"], "output": str(audit_path)}
    proxy = {
        key: value for key, value in modules["proxy"].items() if key != "tls"
    }
    proxy["listen"] = {"host": LOOPBACK, "port": proxy_port}
    proxy["pairing_token"] = f"${{{TOKEN_VARIABLE}}}"
    modules["proxy"] = proxy
    config["modules"] = modules
    config["secrets"] = [f"${{{TOKEN_VARIABLE}}}"]
    config["triggers"] = {"fakeplatform": {"channels": {CHANNEL: policy}}}
    config["actions"] = [
        {
            **rule,
            "destination": {**rule["destination"], "platform": PLATFORM, "channel_id": CHANNEL},
        }
        for rule in config["actions"]
    ]
    return _write_yaml(path, config)


def _agent_profile(
    path: Path,
    *,
    modules_directory: Path,
    brain_url: str,
    agent_id: str,
    png_path: Path,
    helper_path: Path,
) -> Path:
    """``agent.yaml.example`` as the agent of this trial runs it: two
    capture sources — ``file`` (default) and the gated ``command`` — and the
    link dialing the loopback brain. The grant, the limits and the
    reconnection settings are the profile's own. The trial is the
    ``screen.capture`` one (AC39): the three phase 2 device modules the
    profile also enables (R9) are left out with their actions and rules —
    ``tests/test_phase2_scenario.py`` drives those across the proxy."""

    config = _read_yaml(AGENT_PROFILE)
    config["modules_directory"] = str(modules_directory)
    phase2_devices = ("audio_input", "audio_output", "stream_control")
    config["enabled_modules"] = [
        name for name in config["enabled_modules"] if name not in phase2_devices
    ]
    config["actions"] = [
        rule for rule in config["actions"] if rule["action_name"] == SCREEN_CAPTURE
    ]
    modules = {
        name: settings
        for name, settings in config["modules"].items()
        if name not in phase2_devices
    }
    modules["capture"] = {
        **modules["capture"],
        "sources": {
            SOURCE_FILE: {"kind": "file", "path": str(png_path)},
            SOURCE_GATED: {
                "kind": "command",
                "argv": [sys.executable, str(helper_path), str(png_path)],
                "command_timeout_seconds": HELPER_TIMEOUT_SECONDS,
            },
        },
        "default_source": SOURCE_FILE,
    }
    modules["agent_link"] = {
        **modules["agent_link"],
        "brain_url": brain_url,
        "pairing_token": f"${{{TOKEN_VARIABLE}}}",
        "agent_id": agent_id,
        "actions": [SCREEN_CAPTURE],
    }
    config["modules"] = modules
    config["secrets"] = [f"${{{TOKEN_VARIABLE}}}"]
    return _write_yaml(path, config)


# --------------------------------------------------------------------------- #
# The fake model session (Chat Completions shape, decision 2)
# --------------------------------------------------------------------------- #


def _current_message_id(body: Mapping[str, Any]) -> str | None:
    """The ``source_message_id`` of the viewer message this request answers:
    the last ``user`` message with string content, which the brain places
    after the history and before the turn entries."""

    current: str | None = None
    for message in body.get("messages", ()):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            for line in message["content"].splitlines():
                if line.startswith("source_message_id: "):
                    current = line.partition(": ")[2].strip()
    return current


def _tool_messages(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [m for m in body.get("messages", ()) if m.get("role") == "tool"]


def _tool_envelope(body: Mapping[str, Any], index: int) -> dict[str, Any]:
    """The envelope of the *index*-th tool result: the one JSON line the
    brain renders (status, error code, result) ahead of the text parts."""

    content = _tool_messages(body)[index]["content"]
    assert isinstance(content, str)
    return json.loads(content.splitlines()[0])


def _image_parts(body: Mapping[str, Any]) -> list[bytes]:
    """The decoded bytes of every ``image_url`` data URL the request carries."""

    images: list[bytes] = []
    for message in body.get("messages", ()):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "image_url":
                url = part["image_url"]["url"]
                images.append(base64.b64decode(url.partition(",")[2]))
    return images


class FakeModel(FakeSession):
    """The brain's backend: probes answered with the forced tool call,
    scenario requests scripted by viewer message and by turn.

    Both scenarios propose ``chat.read`` then ``screen.capture`` — from the
    ``file`` source for :data:`MESSAGE_FILE`, from the ``gated`` source for
    :data:`MESSAGE_GATED` — then answer :data:`FINAL_TEXT`, whatever the
    second observation was. Every scenario request body is put on
    ``requests`` once answered, so the test awaits the run's progress on a
    bounded ``get``; one no script answers is a 500, kept in ``unscripted``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.unscripted: list[dict[str, Any]] = []
        self.urls: set[str] = set()

    @property
    def probes(self) -> list[dict[str, Any]]:
        return self.probe_calls

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.urls.add(url)
        return await super().post(url, **kwargs)

    async def _answer(self, url: str, kwargs: dict[str, Any]) -> Any:
        body = kwargs.get("json")
        assert isinstance(body, Mapping)
        self.post_calls.append({"url": url, **kwargs})
        reply = self._script(body)
        if reply is None:
            self.unscripted.append(dict(body))
            return FakeResponse(500, "unscripted request")
        await self.requests.put(dict(body))
        return FakeResponse(200, reply)

    @staticmethod
    def _script(body: Mapping[str, Any]) -> dict[str, Any] | None:
        message_id = _current_message_id(body)
        if message_id is None:
            return None
        base = message_id.partition("#")[0]
        if base == MESSAGE_FILE:
            source = SOURCE_FILE
        elif base == MESSAGE_GATED:
            source = SOURCE_GATED
        else:
            return None
        turn = len(_tool_messages(body))
        if turn == 0:
            return tool_call(CHAT_READ, {"limit": 5})
        if turn == 1:
            return tool_call(SCREEN_CAPTURE, {"source": source})
        if turn == 2:
            return final(FINAL_TEXT)
        return None


# --------------------------------------------------------------------------- #
# The capture gate
# --------------------------------------------------------------------------- #


class GatedCapture:
    """One start of the gated program: held until :meth:`release`."""

    def __init__(self, argv: list[str], png: bytes) -> None:
        self.argv = argv
        self._png = png
        self.outcome: asyncio.Future[tuple[int, bytes]] = (
            asyncio.get_running_loop().create_future()
        )
        #: What the program told the gate before it exited.
        self.sent = b""

    def release(self, *, byte: bool) -> bytes:
        """End this capture and return what the program sent before it
        exited: with *byte* it writes its PNG and reports ``D``; without, EOF
        makes it exit 1 having sent nothing. A program whose capture was
        abandoned (its agent killed) sends nothing either way."""

        if not self.outcome.done():
            if byte:
                self.outcome.set_result((0, self._png))
                self.sent = HELPER_DONE
            else:
                self.outcome.set_result((1, b""))
        return self.sent

    def abandon(self) -> None:
        """The program dies with its killed agent: the run of it ends
        interrupted, having sent nothing more."""

        self.outcome.cancel()


class CaptureGate:
    """Where the gated program reports its start: the capture module's
    ``_subprocess_runner`` seam, for the ``gated`` source's argv only."""

    def __init__(self, gated_argv: list[str], png: bytes) -> None:
        self.gated_argv = gated_argv
        self._png = png
        self.starts: asyncio.Queue[GatedCapture] = asyncio.Queue()

    async def runner(self, argv: Any, *, limit: int) -> tuple[int | None, bytes]:
        assert list(argv) == self.gated_argv, argv
        capture = GatedCapture(list(argv), self._png)
        await self.starts.put(capture)
        code, data = await capture.outcome
        assert len(data) <= limit
        return code, data

    async def acknowledged(self) -> GatedCapture:
        """The next capture-start acknowledgement, bounded."""

        return await asyncio.wait_for(self.starts.get(), WAIT_SECONDS)


# --------------------------------------------------------------------------- #
# The link: the proxy's listener and the agents' dials
# --------------------------------------------------------------------------- #


class Listener:
    """What the brain proxy's ``_server_factory`` seam opens: the handler of
    every connection, served on the server end of one in-memory pair."""

    def __init__(self) -> None:
        self.handler: Callable[[Any], Any] | None = None
        self.listening: dict[str, Any] = {}
        self.closed = False
        self._served: list[asyncio.Task[Any]] = []

    async def factory(self, handler: Any, **kwargs: Any) -> Listener:
        assert self.handler is None
        self.handler = handler
        self.listening = dict(kwargs)
        return self

    async def stop(self) -> None:
        self.handler = None

    async def close(self) -> None:
        self.closed = True

    def dial(self) -> MemoryWebSocketPair:
        assert self.handler is not None, "the brain is not listening"
        pair = MemoryWebSocketPair()
        self._served.append(asyncio.ensure_future(self.handler(pair.server)))
        return pair

    async def settle(self) -> None:
        for task in self._served:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._served, return_exceptions=True)


class Runtime:
    """One ``core.main.run`` over a profile file, in this event loop.

    The seams of each module are added to the configuration the entry point
    loads (:func:`inject_seams`). ``diagnostics`` collects every line the
    entry point and the modules given a reporter print.
    """

    def __init__(self, name: str, profile: Path, environ: Mapping[str, str]) -> None:
        self.name = name
        self.profile = profile
        self.environ = dict(environ)
        self.diagnostics: list[str] = []
        self.task: asyncio.Task[int] | None = None
        self.pair: MemoryWebSocketPair | None = None
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._changed = asyncio.Event()

    def report(self, line: str) -> None:
        self.diagnostics.append(line)
        self._changed.set()

    def ready_reporter(self, line: str) -> None:
        assert line == READY_LINE, line
        self._ready.set()

    async def start(self) -> None:
        self.task = asyncio.ensure_future(
            application.run(
                self.profile,
                self._stop,
                environ=self.environ,
                ready_reporter=self.ready_reporter,
                diagnostic_reporter=self.report,
            )
        )

    async def ready(self) -> None:
        assert self.task is not None
        waiter = asyncio.ensure_future(self._ready.wait())
        await asyncio.wait(
            {waiter, self.task}, timeout=WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED
        )
        waiter.cancel()
        assert self._ready.is_set(), self.describe()

    async def diagnostic(self, needle: str) -> str:
        async def found() -> str:
            while True:
                for line in self.diagnostics:
                    if needle in line:
                        return line
                self._changed.clear()
                await self._changed.wait()

        return await asyncio.wait_for(found(), WAIT_SECONDS)

    async def connect(self, listener: Listener) -> Any:
        """The agent link's ``_connector``: one dial per runtime. A runtime
        whose link was lost does not reach the brain again."""

        if self.pair is not None:
            await asyncio.Event().wait()
        self.pair = listener.dial()
        return self.pair.client

    @property
    def alive(self) -> bool:
        return self.task is not None and not self.task.done()

    def kill(self) -> None:
        """What ``SIGKILL`` does: the link is lost abruptly, the runtime ends."""

        assert self.task is not None and self.pair is not None
        self.pair.drop()
        self.task.cancel()

    async def exited(self) -> int | str:
        assert self.task is not None
        try:
            return await asyncio.wait_for(asyncio.shield(self.task), WAIT_SECONDS)
        except asyncio.CancelledError:
            if self.task.cancelled():
                return KILLED
            raise

    async def terminate(self) -> int | str:
        self._stop.set()
        return await self.exited()

    async def reap(self) -> None:
        """Last resort for a failing test: end whatever is left."""

        if self.task is None or self.task.done():
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(self.task), WAIT_SECONDS)
        except BaseException:  # noqa: BLE001 - reaping must not mask the failure
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def describe(self) -> str:
        return f"[{self.name} diagnostics]\n" + "\n".join(self.diagnostics)


def inject_seams(
    monkeypatch: pytest.MonkeyPatch, seams: dict[Path, Callable[[], dict[str, dict[str, Any]]]]
) -> None:
    """Add each profile's module seams to the configuration ``core.main``
    loads from it: the file is read and validated as before, then the seams
    the process could not own are set on the loaded modules."""

    real_load_config = application.load_config

    def load_with_seams(path: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        config = real_load_config(path, *args, **kwargs)
        for module, settings in seams[Path(path)]().items():
            config["modules"][module].update(settings)
        return config

    monkeypatch.setattr(application, "load_config", load_with_seams)


# --------------------------------------------------------------------------- #
# Audit file reading
# --------------------------------------------------------------------------- #


def _audit_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            assert isinstance(record, dict) and "type" in record, line
            records.append(record)
    return records


def _of_type(records: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [record for record in records if record["type"] == event_type]


def _index_of(records: list[dict[str, Any]], record: Mapping[str, Any]) -> int:
    return next(index for index, item in enumerate(records) if item is record)


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [leaf for k, v in value.items() for leaf in _string_leaves(k) + _string_leaves(v)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _string_leaves(item)]
    return []


# --------------------------------------------------------------------------- #
# A direct dial with the wrong token (the close code the agent does not print)
# --------------------------------------------------------------------------- #


async def _dial_with_wrong_token(listener: Listener) -> tuple[dict[str, Any], int | None]:
    """``hello`` with the wrong token over the listener's own connection
    handler: the ``error`` frame received and the close code the brain
    closed with."""

    ws = listener.dial().client
    await ws.send_str(
        json.dumps(
            {
                "v": PROXY_PROTOCOL_VERSION,
                "type": FRAME_HELLO,
                "id": "nonce-intruder",
                "agent_id": INTRUDER_ID,
                "token": WRONG_TOKEN,
                "actions": [],
                "max_frame_bytes": 1048576,
            }
        )
    )
    message = await asyncio.wait_for(ws.receive(), WAIT_SECONDS)
    assert message.type == WSMsgType.TEXT, message
    error = json.loads(message.data)
    closing = await asyncio.wait_for(ws.receive(), WAIT_SECONDS)
    assert closing.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED), closing
    await asyncio.wait_for(ws.close(), WAIT_SECONDS)
    return error, ws.close_code


# --------------------------------------------------------------------------- #
# The trial
# --------------------------------------------------------------------------- #


async def test_ac39_two_process_topology_over_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC39 (R6, R7): brain and agent from their shipped profiles, on one link.

    In order: the brain reports ready (probes answered by the fake model);
    an agent presenting the wrong token is told ``auth_failed`` (its
    diagnostics) and a direct dial with that token is closed 4401; the valid
    agent pairs, which fires the feed: the ``file`` run completes end to end
    with the image transferred by reference and reaching the model, then the
    ``gated`` run starts its capture — the gate acknowledges, the agent is
    killed with the capture in flight, the run observes ``error
    proxy_disconnected`` and still answers; the agent restarts, pairs again,
    and both runs succeed a second time, the gated capture released through
    the gate this time; both runtimes return 0 on their stop. The audit file
    then shows the two pairings, the readiness transitions, the attachment
    references and never a path.

    Phase 8 (R9, AC33, plan P15e): the two runtimes run in this event loop,
    joined by in-memory pairs (see the module docstring); the assertions are
    the two-process ones.
    """

    brain_url = f"ws://{LOOPBACK}:{PROXY_PORT}"
    png = png_bytes(16, 9)
    png_path = tmp_path / "screen.png"
    png_path.write_bytes(png)
    # The gated source's program: named in the profile, never executed.
    helper_path = tmp_path / "gated_capture.py"
    audit_path = tmp_path / "audit.jsonl"
    modules_directory = _modules_directory(tmp_path / "modules")

    model = FakeModel()
    gate = CaptureGate([sys.executable, str(helper_path), str(png_path)], png)
    listener = Listener()
    runtimes: list[Runtime] = []
    gated: GatedCapture | None = None

    try:
        brain_profile = _brain_profile(
            tmp_path / "brain.yaml",
            modules_directory=modules_directory,
            model_endpoint=MODEL_ENDPOINT,
            proxy_port=PROXY_PORT,
            audit_path=audit_path,
        )

        def agent_profile(name: str, agent_id: str) -> Path:
            return _agent_profile(
                tmp_path / name,
                modules_directory=modules_directory,
                brain_url=brain_url,
                agent_id=agent_id,
                png_path=png_path,
                helper_path=helper_path,
            )

        base_environ = {
            key: value for key, value in os.environ.items() if key != TOKEN_VARIABLE
        }
        valid_environ = {**base_environ, TOKEN_VARIABLE: PAIRING_TOKEN}
        wrong_environ = {**base_environ, TOKEN_VARIABLE: WRONG_TOKEN}

        brain = Runtime("brain", brain_profile, valid_environ)
        intruder = Runtime("intruder", agent_profile("intruder.yaml", INTRUDER_ID), wrong_environ)
        agent = Runtime("agent", agent_profile("agent.yaml", AGENT_ID), valid_environ)
        restarted = Runtime("agent-restarted", tmp_path / "agent-restarted.yaml", valid_environ)
        shutil.copyfile(agent.profile, restarted.profile)
        runtimes.extend((brain, intruder, agent, restarted))
        agents = {runtime.profile: runtime for runtime in (intruder, agent, restarted)}

        def brain_seams() -> dict[str, dict[str, Any]]:
            return {
                "brain": {"_session_factory": lambda: model},
                "proxy": {
                    "_server_factory": listener.factory,
                    "diagnostic_reporter": brain.report,
                },
            }

        def agent_seams(runtime: Runtime) -> Callable[[], dict[str, dict[str, Any]]]:
            return lambda: {
                "capture": {"_subprocess_runner": gate.runner},
                "agent_link": {
                    "_connector": lambda url, **kwargs: runtime.connect(listener),
                    "diagnostic_reporter": runtime.report,
                },
            }

        inject_seams(
            monkeypatch,
            {
                brain.profile: brain_seams,
                **{profile: agent_seams(runtime) for profile, runtime in agents.items()},
            },
        )

        # -- the brain: ready once both probes are answered ---------------- #
        await brain.start()
        await brain.ready()
        assert len(model.probes) == 2, brain.describe()
        assert listener.listening["host"] == LOOPBACK and listener.listening["port"] == PROXY_PORT
        assert listener.listening["ssl_context"] is None

        # -- invalid token: auth_failed in the agent's diagnostics, 4401 --- #
        await intruder.start()
        await intruder.ready()
        refused = await intruder.diagnostic(PROXY_ERROR_AUTH_FAILED)
        assert WRONG_TOKEN not in refused and PAIRING_TOKEN not in refused
        assert await intruder.terminate() == 0, intruder.describe()
        error, close_code = await _dial_with_wrong_token(listener)
        assert error["type"] == FRAME_ERROR and error["code"] == PROXY_ERROR_AUTH_FAILED
        assert error["id"] == "nonce-intruder"
        assert close_code == PROXY_CLOSE_AUTH_FAILED == 4401

        # -- valid token: pairing fires the feed; the file run is AC1 ------ #
        await agent.start()
        await agent.ready()
        first = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(first) == MESSAGE_FILE
        assert _tool_messages(first) == []
        second = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(second) == MESSAGE_FILE
        assert _tool_envelope(second, 0)["status"] == "success"
        third = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(third) == MESSAGE_FILE
        capture_result = _tool_envelope(third, 1)
        assert capture_result["status"] == "success", capture_result
        assert capture_result["result"]["source"] == SOURCE_FILE
        assert capture_result["result"]["content_type"] == "image/png"
        # The image the agent captured reached the model through the brain:
        # by reference over the wire, by value into the request.
        assert _image_parts(third) == [png]

        # -- the gated run: kill the agent with the capture in flight ------ #
        gated_first = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(gated_first) == MESSAGE_GATED
        gated_second = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(gated_second) == MESSAGE_GATED
        gated = await gate.acknowledged()
        assert model.requests.empty(), [
            (_current_message_id(body), len(_tool_messages(body)), body["messages"][-1])
            for body in model.requests._queue  # type: ignore[attr-defined]
        ]
        assert agent.alive
        agent.kill()
        gated.abandon()
        assert await agent.exited() == KILLED
        assert agent.pair is not None and agent.pair.server.close_code == WS_ABNORMAL_CLOSURE
        killed, gated = gated, None
        # What the abandoned program sent before it died is the proof the
        # capture had not completed before the kill.
        assert killed.release(byte=False) == b""
        gated_third = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(gated_third) == MESSAGE_GATED
        disconnected = _tool_envelope(gated_third, 1)
        assert disconnected == {
            "status": "error",
            "error": {"code": PROXY_ERROR_PROXY_DISCONNECTED},
        }, disconnected
        assert _image_parts(gated_third) == []

        # -- restart: pairs again, both runs succeed ----------------------- #
        await restarted.start()
        await restarted.ready()
        again_first = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(again_first) == f"{MESSAGE_FILE}#2"
        await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        again_third = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(again_third) == f"{MESSAGE_FILE}#2"
        again_capture = _tool_envelope(again_third, 1)
        assert again_capture["status"] == "success", again_capture
        assert _image_parts(again_third) == [png]

        again_gated_first = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(again_gated_first) == f"{MESSAGE_GATED}#2"
        await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        gated = await gate.acknowledged()
        released, gated = gated, None
        assert released.release(byte=True) == HELPER_DONE
        again_gated_third = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(again_gated_third) == f"{MESSAGE_GATED}#2"
        released_result = _tool_envelope(again_gated_third, 1)
        assert released_result["status"] == "success", released_result
        assert released_result["result"]["source"] == SOURCE_GATED
        assert _image_parts(again_gated_third) == [png]

        # -- both return 0 on their stop ----------------------------------- #
        assert await restarted.terminate() == 0, restarted.describe()
        assert await brain.terminate() == 0, brain.describe()
        assert model.unscripted == []
        assert model.requests.empty()
        assert model.urls == {MODEL_ENDPOINT}
    finally:
        if gated is not None:
            gated.release(byte=False)
        for runtime in runtimes:
            await runtime.reap()
        await listener.settle()

    # -- the audit file, complete once the brain exited --------------------- #
    records = _audit_records(audit_path)

    def at(record: Mapping[str, Any]) -> int:
        return _index_of(records, record)

    # Two pairings, both the valid agent's, published by the proxy's adapter
    # with its own metadata; the intruder never reached the bus.
    statuses = _of_type(records, AGENT_STATUS)
    assert [record["payload"]["state"] for record in statuses] == ["paired", "paired"]
    for record in statuses:
        assert record["metadata"]["source"] == "proxy"
        assert record["metadata"]["provider_id"] == AGENT_ID
    assert not any(INTRUDER_ID in leaf for leaf in _string_leaves(records))

    # The four runs, in feed order, every one answered.
    completed = _of_type(records, TRACE_BRAIN_RUN_COMPLETED)
    assert [r["payload"]["source_event_id"] for r in completed] == [
        MESSAGE_FILE, MESSAGE_GATED, f"{MESSAGE_FILE}#2", f"{MESSAGE_GATED}#2"
    ]
    assert [r["payload"]["status"] for r in completed] == ["success"] * 4

    # The four captures: by reference (an attachment id, never a path), the
    # killed one ``error proxy_disconnected`` with no part.
    captures = [
        r for r in _of_type(records, OBSERVATION_TRACE) if r["payload"]["action"] == SCREEN_CAPTURE
    ]
    assert [(r["payload"]["status"], r["payload"]["error_code"]) for r in captures] == [
        ("success", None),
        ("error", PROXY_ERROR_PROXY_DISCONNECTED),
        ("success", None),
        ("success", None),
    ]
    for record in (captures[0], captures[2], captures[3]):
        (part,) = record["payload"]["parts"]
        assert part["type"] == "image_ref"
        assert isinstance(part["attachment_id"], str) and part["attachment_id"]
        assert os.sep not in part["attachment_id"]
        assert part["size"] == len(png) and (part["width"], part["height"]) == (16, 9)
    assert captures[1]["payload"]["parts"] == []
    assert [r["payload"]["run_id"] for r in captures] == [r["payload"]["run_id"] for r in completed]

    # Readiness around the kill: the proxy is degraded — not ready — before
    # the disconnected observation is recorded, and ready again with
    # ``screen.capture`` once the restarted agent paired, before its runs.
    proxy_degraded = [
        r for r in _of_type(records, TRACE_MODULE_DEGRADED) if r["payload"]["module"] == "proxy"
    ]
    paired_again = [
        r
        for r in _of_type(records, TRACE_MODULE_READY)
        if r["payload"]["module"] == "proxy" and "capabilities" in r["payload"]
    ]
    assert proxy_degraded, [r["payload"] for r in records]
    assert "disconnected" in proxy_degraded[0]["payload"]["reason"]
    assert [r["payload"]["capabilities"] for r in paired_again] == [[SCREEN_CAPTURE]]
    assert paired_again[0]["payload"]["agent_id"] == AGENT_ID
    assert at(statuses[0]) < at(captures[0]) < at(proxy_degraded[0]) < at(captures[1])
    assert at(captures[1]) < at(paired_again[0]) < at(captures[2])
    assert at(proxy_degraded[0]) < at(statuses[1]) < at(captures[2])

    leaves = _string_leaves(records)
    assert not any(str(png_path) in leaf or str(helper_path) in leaf for leaf in leaves)
    assert not any(PAIRING_TOKEN in leaf or WRONG_TOKEN in leaf for leaf in leaves)
