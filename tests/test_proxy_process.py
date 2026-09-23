"""Two real processes on loopback: brain and PC agent (R6, R7; AC39).

Both are ``python -m core.main --config <tmp>`` children of the test — the
brain under a profile derived from ``config.server.yaml.example`` (the chat
input replaced by the ``fakeplatform`` fixture, whose scripted ``feed`` fires
on every ``agent.status`` the paired agent sends; TLS removed for a loopback
listener; the model endpoint pointed at a fake Chat Completions server run in
this process; the audit written to a temp file; ``modules_directory`` a temp
copy of the shipped and fixture modules, plan decision 7) and the agent under
a profile derived from ``agent.yaml.example`` (``ws://127.0.0.1:<port>``, a
``file`` source for the end-to-end scenarios and a ``gated`` ``command``
source whose helper answers to a loopback gate this test owns).

**The capture gate.** The ``gated`` helper connects to a TCP gate server of
this process as soon as the agent's capture provider starts it — that
connection is the *capture-start acknowledgement* — then blocks reading one
byte. On a byte it writes the PNG to its stdout, tells the gate ``D`` and
exits 0; on EOF it exits 1 having written nothing. The test therefore holds
a ``screen.capture`` in flight for exactly as long as it wants, kills the
agent only after the acknowledgement, and releases the orphaned helper by
half-closing the gate afterwards (in a ``finally``, so a failing test leaves
no child behind). What the helper sent before EOF makes the kill
deterministic rather than vacuous: ``D`` before the kill would mean the
capture completed first, and the test fails on it.

**What is waited on.** Readiness through each process's ``ready`` line; the
run's progress through the model requests the fake server answers (the
brain's own transcript, tool results and image parts included); the capture
start through the gate; pairing refusals through the agent's stderr and a
direct dial; and the processes' exits. Every wait is a bounded
``asyncio.wait_for``; there is no sleep. The brain's audit file — every bus
event, the agent's ``agent.status`` events and the proxy's readiness
transitions included — is read once both processes have exited on
``SIGTERM``, complete: the pairings, the attachment reference (never a
path), the ``error proxy_disconnected`` observation of the killed capture
and the readiness transitions around it are asserted there.

The module skips itself, naming the reason, when no free loopback port can
be bound.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import signal
import socket
import sys
import textwrap
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from core.contracts import (
    PROBE_TOOL,
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
from conftest import final, is_probe_request, png_bytes, tool_call


aiohttp = pytest.importorskip("aiohttp")
from aiohttp import WSMsgType, web  # noqa: E402  (after the import-or-skip)


ROOT = Path(__file__).resolve().parent.parent
SERVER_PROFILE = ROOT / "config.server.yaml.example"
AGENT_PROFILE = ROOT / "agent.yaml.example"
FIXTURE_MODULES = ROOT / "tests" / "fixtures" / "modules"

#: The bound of every wait (plan P22): generous next to the runs, far under
#: the brain's ``total_run_seconds`` (60) and the gated helper's
#: ``command_timeout_seconds`` (60), so neither budget races the test.
WAIT_SECONDS = 30.0
HELPER_TIMEOUT_SECONDS = 60
LOOPBACK = "127.0.0.1"

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

#: What the gated helper tells the gate once its PNG is on stdout.
HELPER_DONE = b"D"
#: The byte that releases a gated capture.
HELPER_RELEASE = b"\x01"

HELPER_SCRIPT = textwrap.dedent(
    """
    import socket
    import sys

    gate_port = int(sys.argv[1])
    png = open(sys.argv[2], "rb").read()
    with socket.create_connection(("127.0.0.1", gate_port)) as gate:
        released = gate.recv(1)
        if not released:
            sys.exit(1)
        sys.stdout.buffer.write(png)
        sys.stdout.buffer.flush()
        gate.sendall(b"D")
    sys.exit(0)
    """
)


# --------------------------------------------------------------------------- #
# Ports, profiles, modules directory
# --------------------------------------------------------------------------- #


def _free_loopback_port() -> int:
    """A port the kernel just handed out on loopback, or a skip naming why."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((LOOPBACK, 0))
            return int(probe.getsockname()[1])
    except OSError as exc:
        pytest.skip(f"no free loopback port can be bound: {exc}")


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
    platform ``fake``; the brain's endpoint is the fake model server; the
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
    gate_port: int,
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
                "argv": [sys.executable, str(helper_path), str(gate_port), str(png_path)],
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
# The fake model server (Chat Completions shape, decision 2)
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


class FakeModelServer:
    """The brain's backend: probes answered with the forced tool call,
    scenario requests scripted by viewer message and by turn.

    Both scenarios propose ``chat.read`` then ``screen.capture`` — from the
    ``file`` source for :data:`MESSAGE_FILE`, from the ``gated`` source for
    :data:`MESSAGE_GATED` — then answer :data:`FINAL_TEXT`, whatever the
    second observation was. Every scenario request body is put on
    ``requests`` once answered, so the test awaits the run's progress on a
    bounded ``get``.
    """

    def __init__(self) -> None:
        self.requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.probes: list[dict[str, Any]] = []
        self.unscripted: list[dict[str, Any]] = []
        self._runner: web.AppRunner | None = None
        self.endpoint = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/{tail:.*}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, LOOPBACK, 0)
        await site.start()
        server = getattr(site, "_server", None)
        assert server is not None and server.sockets
        port = server.sockets[0].getsockname()[1]
        self.endpoint = f"http://{LOOPBACK}:{port}/v1/chat/completions"

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.json()
        if is_probe_request(body):
            self.probes.append(body)
            return web.json_response(tool_call(PROBE_TOOL, {"ok": True}))
        reply = self._script(body)
        if reply is None:
            self.unscripted.append(body)
            return web.Response(status=500, text="unscripted request")
        await self.requests.put(body)
        return web.json_response(reply)

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


class CaptureGate:
    """The loopback server the gated helper connects to when it starts."""

    def __init__(self) -> None:
        self.connections: asyncio.Queue[
            tuple[asyncio.StreamReader, asyncio.StreamWriter]
        ] = asyncio.Queue()
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, LOOPBACK, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self.connections.put((reader, writer))

    async def acknowledged(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The next capture-start acknowledgement, bounded."""

        return await asyncio.wait_for(self.connections.get(), WAIT_SECONDS)

    @staticmethod
    async def release(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, byte: bool
    ) -> bytes:
        """End one gated capture and return what the helper sent before it
        exited: with *byte* the helper writes its PNG and reports ``D``;
        without, the half-close is its EOF and it exits having sent nothing.
        Either way the helper is gone once EOF comes back."""

        try:
            if byte:
                writer.write(HELPER_RELEASE)
                await writer.drain()
            writer.write_eof()
            return await asyncio.wait_for(reader.read(), WAIT_SECONDS)
        finally:
            writer.close()


# --------------------------------------------------------------------------- #
# The processes
# --------------------------------------------------------------------------- #


class _Stream:
    """One captured output stream: every line kept, waiters resolved as
    lines arrive, so a wait on a line is a bounded await and no sleep."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.lines: list[str] = []
        self._eof = False
        self._waiters: list[tuple[Callable[[str], bool], asyncio.Future[str]]] = []
        self._task = asyncio.ensure_future(self._pump(reader))

    async def _pump(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                self.lines.append(line)
                for predicate, future in list(self._waiters):
                    if not future.done() and predicate(line):
                        future.set_result(line)
                self._waiters = [(p, f) for p, f in self._waiters if not f.done()]
        finally:
            self._eof = True
            for _predicate, future in self._waiters:
                if not future.done():
                    future.set_exception(EOFError("stream closed before the line arrived"))
            self._waiters.clear()

    async def line(self, predicate: Callable[[str], bool]) -> str:
        for existing in self.lines:
            if predicate(existing):
                return existing
        if self._eof:
            raise EOFError("stream closed before the line arrived")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._waiters.append((predicate, future))
        return await asyncio.wait_for(future, WAIT_SECONDS)

    async def drained(self) -> None:
        await self._task

    def cancel(self) -> None:
        self._task.cancel()


class Process:
    """One ``python -m core.main --config <profile>`` child."""

    def __init__(self, name: str, profile: Path, environ: Mapping[str, str]) -> None:
        self.name = name
        self.profile = profile
        self.environ = dict(environ)
        self.process: asyncio.subprocess.Process | None = None
        self.stdout: _Stream | None = None
        self.stderr: _Stream | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "core.main",
            "--config",
            str(self.profile),
            cwd=str(ROOT),
            env=self.environ,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        self.stdout = _Stream(self.process.stdout)
        self.stderr = _Stream(self.process.stderr)

    async def ready(self) -> None:
        assert self.stdout is not None
        await self.stdout.line(lambda line: line == READY_LINE)

    async def diagnostic(self, needle: str) -> str:
        assert self.stderr is not None
        return await self.stderr.line(lambda line: needle in line)

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def kill(self) -> None:
        assert self.process is not None
        self.process.send_signal(signal.SIGKILL)

    async def exited(self) -> int:
        assert self.process is not None and self.stdout is not None and self.stderr is not None
        code = await asyncio.wait_for(self.process.wait(), WAIT_SECONDS)
        drained = asyncio.gather(self.stdout.drained(), self.stderr.drained())
        await asyncio.wait_for(drained, WAIT_SECONDS)
        return code

    async def terminate(self) -> int:
        assert self.process is not None
        self.process.send_signal(signal.SIGTERM)
        return await self.exited()

    async def reap(self) -> None:
        """Last resort for a failing test: kill and reap whatever is left."""

        if self.process is None:
            return
        if self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
            await self.process.wait()
        for stream in (self.stdout, self.stderr):
            if stream is not None:
                stream.cancel()

    def report(self) -> str:
        out = "\n".join(self.stdout.lines if self.stdout else ())
        err = "\n".join(self.stderr.lines if self.stderr else ())
        return f"[{self.name} stdout]\n{out}\n[{self.name} stderr]\n{err}"


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


async def _dial_with_wrong_token(brain_url: str) -> tuple[dict[str, Any], int | None]:
    """``hello`` with the wrong token over the real transport: the ``error``
    frame received and the close code the brain closed with."""

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(brain_url) as ws:
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


async def test_ac39_two_process_topology_over_loopback(tmp_path: Path) -> None:
    """AC39 (R6, R7): brain and agent as two real processes on loopback.

    In order: the brain reports ready (probes answered by the fake model);
    an agent presenting the wrong token is told ``auth_failed`` (its stderr)
    and a direct dial with that token is closed 4401; the valid agent pairs,
    which fires the feed: the ``file`` run completes end to end with the
    image transferred by reference and reaching the model, then the
    ``gated`` run starts its capture — the gate acknowledges, the agent is
    killed with the capture in flight, the run observes ``error
    proxy_disconnected`` and still answers; the agent restarts, pairs again,
    and both runs succeed a second time, the gated capture released through
    the gate this time; both processes exit 0 on SIGTERM. The audit file
    then shows the two pairings, the readiness transitions, the attachment
    references and never a path.
    """

    proxy_port = _free_loopback_port()
    brain_url = f"ws://{LOOPBACK}:{proxy_port}"
    png = png_bytes(16, 9)
    png_path = tmp_path / "screen.png"
    png_path.write_bytes(png)
    helper_path = tmp_path / "gated_capture.py"
    helper_path.write_text(HELPER_SCRIPT, encoding="utf-8")
    audit_path = tmp_path / "audit.jsonl"
    modules_directory = _modules_directory(tmp_path / "modules")

    model = FakeModelServer()
    gate = CaptureGate()
    processes: list[Process] = []
    gated: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None

    try:
        await model.start()
        await gate.start()
        brain_profile = _brain_profile(
            tmp_path / "brain.yaml",
            modules_directory=modules_directory,
            model_endpoint=model.endpoint,
            proxy_port=proxy_port,
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
                gate_port=gate.port,
            )

        base_environ = {
            key: value for key, value in os.environ.items() if key != TOKEN_VARIABLE
        }
        base_environ["PYTHONUNBUFFERED"] = "1"
        valid_environ = {**base_environ, TOKEN_VARIABLE: PAIRING_TOKEN}
        wrong_environ = {**base_environ, TOKEN_VARIABLE: WRONG_TOKEN}

        brain = Process("brain", brain_profile, valid_environ)
        intruder = Process("intruder", agent_profile("intruder.yaml", INTRUDER_ID), wrong_environ)
        agent = Process("agent", agent_profile("agent.yaml", AGENT_ID), valid_environ)
        restarted = Process("agent-restarted", agent.profile, valid_environ)
        processes.extend((brain, intruder, agent, restarted))

        # -- the brain: ready once both probes are answered ---------------- #
        await brain.start()
        await brain.ready()
        assert len(model.probes) == 2, brain.report()

        # -- invalid token: auth_failed on the agent's stderr, close 4401 -- #
        await intruder.start()
        await intruder.ready()
        refused = await intruder.diagnostic(PROXY_ERROR_AUTH_FAILED)
        assert WRONG_TOKEN not in refused and PAIRING_TOKEN not in refused
        assert await intruder.terminate() == 0, intruder.report()
        error, close_code = await _dial_with_wrong_token(brain_url)
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
        assert model.requests.empty()
        assert agent.alive
        agent.kill()
        assert await agent.exited() == -signal.SIGKILL
        reader, writer = gated
        gated = None
        # EOF releases the orphaned helper; what it sent first is the proof
        # the capture had not completed before the kill.
        assert await CaptureGate.release(reader, writer, byte=False) == b""
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
        reader, writer = gated
        gated = None
        assert await CaptureGate.release(reader, writer, byte=True) == HELPER_DONE
        again_gated_third = await asyncio.wait_for(model.requests.get(), WAIT_SECONDS)
        assert _current_message_id(again_gated_third) == f"{MESSAGE_GATED}#2"
        released = _tool_envelope(again_gated_third, 1)
        assert released["status"] == "success", released
        assert released["result"]["source"] == SOURCE_GATED
        assert _image_parts(again_gated_third) == [png]

        # -- both exit 0 on SIGTERM ---------------------------------------- #
        assert await restarted.terminate() == 0, restarted.report()
        assert await brain.terminate() == 0, brain.report()
        assert model.unscripted == []
        assert model.requests.empty()
    finally:
        if gated is not None:
            reader, writer = gated
            try:
                await CaptureGate.release(reader, writer, byte=False)
            except Exception:
                pass
        for process in processes:
            await process.reap()
        await gate.close()
        await model.close()

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
