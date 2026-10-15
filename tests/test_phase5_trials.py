"""Opt-in trials against a real automation instance (R8; AC36).

One node per question of section 9 of the integration study
(``docs/research/streamerbot-etude-2026-10.md``), in its order:

1. nonexistent-name behaviour — what the instance answers a ``DoAction``
   naming an action it does not have;
2. latency and blocking queue — the round trip of the real run action, one
   at a time and in a burst behind the action's queue;
3. enforce-mode authentication — what an unauthenticated client gets from a
   server that enforces authentication, then whether the bridge connects;
4. minimum version per request — whether ``GetActiveViewers``,
   ``GetCredits`` and ``ExecuteCodeTrigger`` are known to this version;
5. C# HTTP/JSON availability — an operator observation;
6. custom-event round trip — whether a run whose action ends by triggering a
   custom event brings back the correlated cue;
7. servers under Wine — an operator observation;
8. subscribed-event volume — how many events a subscription to everything the
   instance lists delivers on a real stream.

Each node is **skipped with a reason naming its variables** unless every one
of them is set, so the default run of this file collects eight skipped nodes.
The skip is decided first in the body, before any import of the transport or
of the bridge and before any I/O; the nodes are synchronous, so no event loop
(and no loop self-pipe) exists before the gate; and an autouse spy refuses
every socket a gated node would create (AC36). Nodes 5 and 7 probe nothing:
they record the operator's observation passed through their variables.

Past the gate, a node talks to the real instance on the real monotonic clock:
either the real bridge loaded through the real loader (settings validation,
the ``${NAME}`` resolution of the password, its redaction) on the shared
runtime harness, or — for the questions about the instance's own protocol
(1, 3, 4, 8) — one raw connection authenticated with the bridge's documented
challenge answer. Every wait is bounded by ``asyncio.wait_for`` on that real
transport (``PHASE5_TRIAL_WAIT_SECONDS``, default 30); this file makes no sleep
call. The password is handed to the loader as a ``${NAME}`` reference; the
raw probes read it to answer the challenge and never print it.

Each node prints one line (run with ``-s``)::

    PHASE5-TRIAL q<n> commit=<sha> instance=<version> outcome=<...> date=<YYYY-MM-DD>

``outcome`` is the comma-separated fields saying what was observed — never an
address, a password or an instance answer's free text. The trial record
(``docs/campaigns/phase5/bridge-trial-record.md``) cites these lines.

Variables. ``PHASE5_TRIAL_URL`` (the instance's WebSocket server address) and
``PHASE5_TRIAL_PASSWORD`` (its authentication password; the server must
require authentication) are shared by every probing node; each node adds its
own, and all of a node's variables are required:

* q1 — ``PHASE5_TRIAL_MISSING_ACTION``: a name no action of the instance has
  (checked against ``GetActions`` before the probe).
* q2 — ``PHASE5_TRIAL_ACTION``: an existing, enabled action without arguments,
  ideally on a blocking queue; optional ``PHASE5_TRIAL_RUNS`` (default 5).
  Running it has the effect that action has.
* q3 — ``PHASE5_TRIAL_ENFORCE``: set once the server's authentication is
  enforced.
* q4 — ``PHASE5_TRIAL_VERSION_PROBE``: the switch. ``ExecuteCodeTrigger`` is
  sent for a trigger name no action should listen to
  (``phase5-trial-probe``).
* q5 — ``PHASE5_TRIAL_INSTANCE_VERSION`` and ``PHASE5_TRIAL_CSHARP_HTTP_JSON``
  (what a C# sub-action using the HTTP client and the JSON library did).
* q6 — ``PHASE5_TRIAL_CUE_ACTION``: an action that ends with *Trigger Custom
  Event* carrying the cue envelope, its ``cue_id`` taken from the run's
  ``cue_id`` argument; optional ``PHASE5_TRIAL_INSTANCE_IDENTITY``
  (``platform=channel_id``) when the envelope names no channel.
* q7 — ``PHASE5_TRIAL_INSTANCE_VERSION`` and ``PHASE5_TRIAL_WINE_SERVERS``
  (whether the WebSocket and HTTP servers started under Wine, and how).
* q8 — ``PHASE5_TRIAL_VOLUME_SECONDS``: how long to count, during a real
  stream.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import statistics
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

REPOSITORY = Path(__file__).resolve().parent.parent
MODULES_DIRECTORY = REPOSITORY / "modules"

URL_VARIABLE = "PHASE5_TRIAL_URL"
PASSWORD_VARIABLE = "PHASE5_TRIAL_PASSWORD"
INSTANCE = (URL_VARIABLE, PASSWORD_VARIABLE)

#: Each study question's title and variables, in the study's order (R8).
QUESTIONS: dict[int, tuple[str, tuple[str, ...]]] = {
    1: ("nonexistent-name behaviour", (*INSTANCE, "PHASE5_TRIAL_MISSING_ACTION")),
    2: ("latency and blocking queue", (*INSTANCE, "PHASE5_TRIAL_ACTION")),
    3: ("enforce-mode authentication", (*INSTANCE, "PHASE5_TRIAL_ENFORCE")),
    4: ("minimum version per request", (*INSTANCE, "PHASE5_TRIAL_VERSION_PROBE")),
    5: (
        "C# HTTP/JSON availability",
        ("PHASE5_TRIAL_INSTANCE_VERSION", "PHASE5_TRIAL_CSHARP_HTTP_JSON"),
    ),
    6: ("custom-event round trip", (*INSTANCE, "PHASE5_TRIAL_CUE_ACTION")),
    7: (
        "servers under Wine",
        ("PHASE5_TRIAL_INSTANCE_VERSION", "PHASE5_TRIAL_WINE_SERVERS"),
    ),
    8: ("subscribed-event volume", (*INSTANCE, "PHASE5_TRIAL_VOLUME_SECONDS")),
}

#: The node of each question, by function name, for the socket spy.
NODES: dict[str, int] = {
    "test_trial_q1_nonexistent_name": 1,
    "test_trial_q2_latency_and_blocking_queue": 2,
    "test_trial_q3_enforced_authentication": 3,
    "test_trial_q4_minimum_version_per_request": 4,
    "test_trial_q5_csharp_http_json": 5,
    "test_trial_q6_custom_event_round_trip": 6,
    "test_trial_q7_servers_under_wine": 7,
    "test_trial_q8_subscribed_event_volume": 8,
}

#: The variables whose values must never appear in a trial line.
SECRET_VARIABLES = (PASSWORD_VARIABLE,)

DEFAULT_WAIT_SECONDS = 30.0
DEFAULT_RUNS = 5
#: The bridge's default inbound queue, the bound q8's volume is read against.
DEFAULT_QUEUE_MAX_EVENTS = 256
#: An operator observation is one short line.
OBSERVATION_MAX_CHARACTERS = 300
#: The run deadline, from the call's entry; the action's own timeout bounds it first.
CALL_BUDGET_SECONDS = 60.0

COMPANION = "Companion"
PRINCIPAL = "brain"
CHAT_EVENT = "channel.chat.message"
CUE_KIND = "cue"
PROBE_TRIGGER = "phase5-trial-probe"
#: The version-dependent requests question 4 names.
VERSIONED_REQUESTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("GetActiveViewers", {}),
    ("GetCredits", {}),
    ("ExecuteCodeTrigger", {"triggerName": PROBE_TRIGGER, "args": {}}),
)
#: The argument a q6 run hands its action, so the cue can be correlated.
CUE_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cue_id": {"type": "string"}},
    "required": ["cue_id"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# The gate, the socket spy and the line
# --------------------------------------------------------------------------- #


def gate(question: int) -> None:
    """Skip *question*'s node unless all its variables are set, naming them (R8)."""

    title, variables = QUESTIONS[question]
    missing = [name for name in variables if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(
            f"q{question} {title}: opt-in trial, runs only with "
            f"{', '.join(variables)} set (unset: {', '.join(missing)})"
        )


def opted_in(question: int) -> bool:
    return all(os.environ.get(name, "").strip() for name in QUESTIONS[question][1])


@pytest.fixture(autouse=True)
def _no_socket_unless_opted_in(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """A gated node opens 0 sockets (AC36): every creation path refuses."""

    question = NODES.get(request.node.originalname)
    if question is None or opted_in(question):
        yield
        return

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(f"q{question}: a socket was created by a skipped trial")

    monkeypatch.setattr(socket.socket, "__init__", refuse)
    for name in ("socketpair", "fromfd", "create_connection", "create_server"):
        monkeypatch.setattr(socket, name, refuse)
    yield


def env(name: str, default: str = "") -> str:
    """A non-secret value of a switched-on node (a name, a count)."""

    return os.environ.get(name, "").strip() or default


def reference(name: str) -> str:
    """The ``${NAME}`` form the loader resolves: the value never passes here."""

    return "${" + name + "}"


def wait_seconds() -> float:
    return float(env("PHASE5_TRIAL_WAIT_SECONDS", str(DEFAULT_WAIT_SECONDS)))


def commit() -> str:
    """The commit the trial ran on, as ``git`` reports it."""

    try:
        answer = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return answer.stdout.strip() or "unknown"


def report(question: int, instance: str, *fields: str, **details: Any) -> str:
    """Print the one line the trial record cites; it carries no secret."""

    values = [*fields, *(f"{key}={value}" for key, value in details.items())]
    line = (
        f"PHASE5-TRIAL q{question} commit={commit()} instance={instance or 'unknown'} "
        f"outcome={','.join(values)} date={datetime.date.today().isoformat()}"
    )
    for name in SECRET_VARIABLES:
        value = os.environ.get(name, "").strip()
        # Checked without a bare ``assert``: pytest's rewriting would echo
        # ``value`` and ``line`` on failure; the message names the variable.
        if value and value in line:
            raise AssertionError(f"the q{question} line would carry {name}")
    print(line)
    return line


def observation(name: str) -> str:
    """An operator observation: one short line, quoted in the trial line."""

    text = env(name)
    assert "\n" not in text and len(text) <= OBSERVATION_MAX_CHARACTERS, (
        f"{name} must be one line of at most {OBSERVATION_MAX_CHARACTERS} characters"
    )
    return json.dumps(text, ensure_ascii=False)


def status_of(answer: Any) -> str:
    """An instance answer's ``status``, or what stood in for it."""

    if isinstance(answer, Mapping):
        status = answer.get("status")
        return status if isinstance(status, str) and status.isidentifier() else "unparsed"
    return str(answer)


def milliseconds(seconds: float) -> int:
    return round(seconds * 1000)


# --------------------------------------------------------------------------- #
# The raw probe (questions 1, 3, 4, 8)
# --------------------------------------------------------------------------- #


class Probe:
    """One raw connection to the instance, with the bridge's request reader.

    ``answer`` is what the instance replied to one request: its frame, or
    ``"no_answer"`` past the wait, or ``"closed"`` when the connection ended.
    Every frame that is not an answer is counted by :attr:`events` and timed.
    """

    def __init__(self, connection: Any, hello: Mapping[str, Any]) -> None:
        self.connection = connection
        self.hello = hello
        self.event_times: list[float] = []

    @property
    def version(self) -> str:
        info = self.hello.get("info")
        version = info.get("version") if isinstance(info, Mapping) else None
        return version if isinstance(version, str) else "unknown"

    @property
    def requires_authentication(self) -> bool:
        return "authentication" in self.hello

    def on_event(self, frame: Mapping[str, Any]) -> None:
        del frame
        self.event_times.append(time.monotonic())

    async def answer(self, frame: Mapping[str, Any]) -> Any:
        import asyncio

        from modules.streamerbot import ConnectionClosed

        try:
            return await self.connection.request(frame, wait_seconds())
        except asyncio.TimeoutError:
            return "no_answer"
        except ConnectionClosed:
            return "closed"

    async def authenticate(self) -> None:
        from modules.streamerbot import authentication_response

        challenge = self.hello.get("authentication")
        assert isinstance(challenge, Mapping), "the server does not require authentication"
        response = authentication_response(
            # The exact value, as the bridge's loader hands it over: no strip.
            os.environ[PASSWORD_VARIABLE], challenge["salt"], challenge["challenge"]
        )
        answer = await self.answer({"request": "Authenticate", "authentication": response})
        assert status_of(answer) == "ok", f"authentication answered {status_of(answer)}"


@asynccontextmanager
async def probe() -> AsyncIterator[Probe]:
    """Connect to ``PHASE5_TRIAL_URL`` and read the hello; close on exit."""

    import asyncio

    import aiohttp

    from modules.streamerbot import _Connection

    async with aiohttp.ClientSession() as session:
        ws = await asyncio.wait_for(
            session.ws_connect(env(URL_VARIABLE), compress=0), wait_seconds()
        )
        try:
            message = await asyncio.wait_for(ws.receive(), wait_seconds())
            assert message.type is aiohttp.WSMsgType.TEXT, "the server sent no hello"
            hello = json.loads(message.data)
            assert isinstance(hello, Mapping) and hello.get("request") == "Hello"
        except BaseException:
            await ws.close()
            raise
        holder: list[Probe] = []
        connection = _Connection(ws, lambda frame: holder[0].on_event(frame))
        holder.append(Probe(connection, hello))
        try:
            yield holder[0]
        finally:
            await connection.close()


# --------------------------------------------------------------------------- #
# The real bridge (questions 2, 3, 6)
# --------------------------------------------------------------------------- #


class Bridge:
    """The real bridge on the real clock, loaded through the real loader."""

    def __init__(self) -> None:
        from core.actions import AuthorizationPolicy, AuthorizationRule
        from core.triggers import TriggerRegistry
        from conftest import runtime_context
        from modules.streamerbot import RUN_ACTION

        self.policy = AuthorizationPolicy()
        self.policy.grant(
            AuthorizationRule(
                rule_id="phase5-trial-run",
                action_name=RUN_ACTION,
                principals=(PRINCIPAL,),
                granted_permissions=("automation.run",),
            )
        )
        self.runtime = runtime_context(
            clock=time.monotonic,  # type: ignore[arg-type]
            authorization=self.policy,
            trigger_registry=TriggerRegistry(companion_name=COMPANION),
        )
        self.handle: Any = None
        self._calls = 0

    async def start(self, **settings: Any) -> bool:
        """Load, prepare and start the bridge; whether it connected."""

        from core.loader import ModuleLoader
        from modules.streamerbot import MODULE_NAME

        loader = ModuleLoader(
            self.runtime.bus,
            MODULES_DIRECTORY,
            context=self.runtime,
            environ=os.environ,
            clock=time.monotonic,
        )
        module = {
            "url": reference(URL_VARIABLE),
            "password": reference(PASSWORD_VARIABLE),
            "companion_name": COMPANION,
            **settings,
        }
        activations = await loader.activate_enabled(
            {"enabled_modules": [MODULE_NAME], "modules": {MODULE_NAME: module}}
        )
        (self.handle,) = (item.handle for item in activations)
        await self.handle.prepare()
        # Bounded by the bridge's own first-attempt wait.
        await self.handle.start_inputs()
        return bool(self.handle.connected)

    async def close(self) -> None:
        if self.handle is not None:
            await self.handle.stop_inputs()
            await self.handle.close()

    async def run(self, action: str, arguments: Mapping[str, Any] | None = None) -> Any:
        from core.contracts import ActionCall, Destination
        from modules.streamerbot import RUN_ACTION

        self._calls += 1
        call = ActionCall(
            action_name=RUN_ACTION,
            action_version=1,
            arguments={"action": action, "arguments": dict(arguments or {})},
            conversation_id="phase5-trial",
            run_id="phase5-trial-run",
            call_id=f"phase5-trial-{self._calls}",
            source_event_id="phase5-trial-source",
            destination=Destination("twitch", "phase5-trial", "automation"),
            principal=PRINCIPAL,
            deadline=time.monotonic() + CALL_BUDGET_SECONDS,
            message_id="phase5-trial-source",
        )
        return await self.runtime.executor.invoke(call)

    def observe(self, accept: Callable[[Mapping[str, Any]], bool]) -> Any:
        """A future resolved with the first published chat payload *accept*s."""

        import asyncio

        found: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def handler(event: Mapping[str, Any]) -> None:
            payload = event.get("payload") or {}
            if not found.done() and accept(payload):
                found.set_result((time.monotonic(), dict(payload)))

        self.runtime.bus.subscribe(CHAT_EVENT, handler)
        return found


def outcome_of(observation: Any) -> str:
    """``status`` or ``status:code[:cause]`` of one run observation."""

    error = dict(observation.error or {})
    parts = [observation.status, error.get("code"), error.get("cause")]
    return ":".join(part for part in parts if part)


async def timed_run(bridge: Bridge, action: str) -> tuple[str, float]:
    started = time.monotonic()
    observation = await bridge.run(action)
    return outcome_of(observation), time.monotonic() - started


def instance_identity() -> dict[str, str]:
    value = env("PHASE5_TRIAL_INSTANCE_IDENTITY")
    if not value:
        return {}
    platform, _, channel = value.partition("=")
    assert platform and channel, "PHASE5_TRIAL_INSTANCE_IDENTITY is platform=channel_id"
    return {platform.strip(): channel.strip()}


async def inventory_names(instance: Probe) -> set[str]:
    answer = await instance.answer({"request": "GetActions"})
    assert status_of(answer) == "ok", f"GetActions answered {status_of(answer)}"
    return {item.get("name") for item in answer.get("actions") or [] if isinstance(item, Mapping)}


async def instance_version() -> str:
    async with probe() as instance:
        return instance.version


# --------------------------------------------------------------------------- #
# The eight nodes, in the study's order
# --------------------------------------------------------------------------- #


def test_trial_q1_nonexistent_name() -> None:
    """Q1: ``DoAction`` by a name the instance does not have — ``error`` or a
    silent ``ok``? The bridge never depends on it (R8): it runs by id."""

    gate(1)
    import asyncio

    missing = env("PHASE5_TRIAL_MISSING_ACTION")

    async def trial() -> str:
        async with probe() as instance:
            await instance.authenticate()
            assert missing not in await inventory_names(instance), (
                "PHASE5_TRIAL_MISSING_ACTION names an existing action"
            )
            answer = await instance.answer(
                {"request": "DoAction", "action": {"name": missing}, "args": {}}
            )
            error_field = isinstance(answer, Mapping) and "error" in answer
            return report(
                1,
                instance.version,
                f"answer={status_of(answer)}",
                f"error_field={'yes' if error_field else 'no'}",
            )

    asyncio.run(trial())


def test_trial_q2_latency_and_blocking_queue() -> None:
    """Q2: the run action's round trip on the real instance, one run at a
    time, then a burst of the same runs queued behind the action's queue."""

    gate(2)
    import asyncio

    action = env("PHASE5_TRIAL_ACTION")
    runs = int(env("PHASE5_TRIAL_RUNS", str(DEFAULT_RUNS)))
    assert runs >= 1

    async def trial() -> str:
        version = await instance_version()
        bridge = Bridge()
        try:
            assert await bridge.start(allowlist=[{"action": action}]), "the bridge did not connect"
            sequential = [await timed_run(bridge, action) for _ in range(runs)]
            burst = await asyncio.gather(*(timed_run(bridge, action) for _ in range(runs)))
        finally:
            await bridge.close()
        sequential_ms = [milliseconds(seconds) for _, seconds in sequential]
        burst_ms = [milliseconds(seconds) for _, seconds in burst]
        outcomes = sorted({outcome for outcome, _ in [*sequential, *burst]})
        return report(
            2,
            version,
            f"runs={runs}",
            f"outcomes={'|'.join(outcomes)}",
            f"sequential_median_ms={round(statistics.median(sequential_ms))}",
            f"sequential_max_ms={max(sequential_ms)}",
            f"burst_median_ms={round(statistics.median(burst_ms))}",
            f"burst_max_ms={max(burst_ms)}",
        )

    asyncio.run(trial())


def test_trial_q3_enforced_authentication() -> None:
    """Q3: with authentication enforced, does an unauthenticated request fail
    cleanly (an error, a closed connection) or stay open without rights? Then
    does the bridge, with the password, connect?"""

    gate(3)
    import asyncio

    async def trial() -> str:
        async with probe() as instance:
            version = instance.version
            challenged = instance.requires_authentication
            answer = await instance.answer({"request": "GetActions"})
            unauthenticated = status_of(answer)
            if isinstance(answer, Mapping) and unauthenticated == "ok":
                unauthenticated = "ok_with_inventory" if "actions" in answer else "ok"
        bridge = Bridge()
        try:
            connected = await bridge.start()
        finally:
            await bridge.close()
        return report(
            3,
            version,
            f"hello_challenge={'yes' if challenged else 'no'}",
            f"unauthenticated={unauthenticated}",
            f"bridge={'connected' if connected else 'degraded'}",
        )

    asyncio.run(trial())


def test_trial_q4_minimum_version_per_request() -> None:
    """Q4: which of the version-dependent requests this instance version knows."""

    gate(4)
    import asyncio

    async def trial() -> str:
        async with probe() as instance:
            await instance.authenticate()
            answers = [
                f"{request}={status_of(await instance.answer({'request': request, **body}))}"
                for request, body in VERSIONED_REQUESTS
            ]
            return report(4, instance.version, *answers)

    asyncio.run(trial())


def test_trial_q5_csharp_http_json() -> None:
    """Q5: the operator's observation of the C# HTTP client and JSON library
    in this instance version; nothing is probed."""

    gate(5)
    report(
        5,
        env("PHASE5_TRIAL_INSTANCE_VERSION"),
        "operator_observation",
        observed=observation("PHASE5_TRIAL_CSHARP_HTTP_JSON"),
    )


def test_trial_q6_custom_event_round_trip() -> None:
    """Q6: a run whose action ends with *Trigger Custom Event* — does the cue
    come back through the bridge, and is it the one this run asked for?"""

    gate(6)
    import asyncio

    action = env("PHASE5_TRIAL_CUE_ACTION")
    token = f"phase5-trial-{uuid.uuid4().hex[:12]}"

    async def trial() -> str:
        version = await instance_version()
        bridge = Bridge()
        try:
            connected = await bridge.start(
                allowlist=[{"action": action, "arguments": CUE_ARGUMENTS_SCHEMA}],
                instance_identity=instance_identity(),
            )
            assert connected, "the bridge did not connect"
            # Wait for this run's own cue, so another automation's cue cannot
            # stand in for it; any other cue is only noted when this one never
            # comes.
            any_cue = bridge.observe(lambda payload: payload.get("kind") == CUE_KIND)
            cue = bridge.observe(
                lambda payload: payload.get("kind") == CUE_KIND
                and payload.get("message_id") == token
            )
            started = time.monotonic()
            observation = await bridge.run(action, {"cue_id": token})
            try:
                arrived, _ = await asyncio.wait_for(cue, wait_seconds())
            except asyncio.TimeoutError:
                arrived = None
        finally:
            await bridge.close()
        if arrived is None:
            seen = "cue=seen,correlated=no" if any_cue.done() else "cue=none"
            return report(6, version, f"run={outcome_of(observation)}", seen)
        return report(
            6,
            version,
            f"run={outcome_of(observation)}",
            "cue=seen",
            "correlated=yes",
            f"latency_ms={milliseconds(arrived - started)}",
        )

    asyncio.run(trial())


def test_trial_q7_servers_under_wine() -> None:
    """Q7: the operator's observation of the WebSocket and HTTP servers under
    Wine; nothing is probed."""

    gate(7)
    report(
        7,
        env("PHASE5_TRIAL_INSTANCE_VERSION"),
        "operator_observation",
        observed=observation("PHASE5_TRIAL_WINE_SERVERS"),
    )


def test_trial_q8_subscribed_event_volume() -> None:
    """Q8: subscribed to every event the instance lists, how many arrive
    during a real stream, and how they compare with the bridge's queue."""

    gate(8)
    import asyncio

    window = float(env("PHASE5_TRIAL_VOLUME_SECONDS"))
    assert window > 0

    async def trial() -> str:
        async with probe() as instance:
            await instance.authenticate()
            listed = await instance.answer({"request": "GetEvents"})
            assert status_of(listed) == "ok", f"GetEvents answered {status_of(listed)}"
            events = {
                category: list(names)
                for category, names in (listed.get("events") or {}).items()
                if isinstance(names, list)
            }
            subscribed = await instance.answer({"request": "Subscribe", "events": events})
            assert status_of(subscribed) == "ok", f"Subscribe answered {status_of(subscribed)}"
            started = time.monotonic()
            # Counting ends with the window, or earlier if the instance closes.
            try:
                await asyncio.wait_for(instance.connection.lost.wait(), window)
            except asyncio.TimeoutError:
                pass
            elapsed = time.monotonic() - started
            seconds: dict[int, int] = {}
            for moment in instance.event_times:
                if moment >= started:
                    second = int(moment - started)
                    seconds[second] = seconds.get(second, 0) + 1
            total = sum(seconds.values())
            peak = max(seconds.values(), default=0)
            return report(
                8,
                instance.version,
                f"categories={len(events)}",
                f"types={sum(len(names) for names in events.values())}",
                f"seconds={round(elapsed)}",
                f"events={total}",
                f"peak_per_second={peak}",
                f"peak_over_default_queue={'yes' if peak > DEFAULT_QUEUE_MAX_EVENTS else 'no'}",
            )

    asyncio.run(trial())
