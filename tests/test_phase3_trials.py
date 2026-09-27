"""Opt-in trials against the real platforms and the real screen (R8; AC41).

One trial per row of the phase 3 trial table in ``docs/README.md``, in the
R8 order — clips de plateforme, modération de plateforme, notifications
communautaires, Kick, YouTube, veille d'écran. Each is **skipped with a
reason naming its variables** unless every one of them is set, so the
default run of the suite collects six skipped tests and opens nothing.

When its variables are set, a trial activates the **real** modules through
the real loader (settings validation, manifest triggers, credential
redaction) on the shared runtime harness, on the real monotonic clock, with
the modules' own transports and default sleepers; this file injects no
sleeper and makes no sleep call. Credentials are handed to the loader as
``${NAME}`` references and resolved from the environment there: this file
never reads a credential value into a setting and never prints one. Real
waiting — opening a source (the EventSub welcome), a platform notification,
a webhook delivery, a poll, a watch tick — is bounded by ``asyncio.wait_for``
on those real transports
(``PHASE3_TRIAL_WAIT_SECONDS``, default 300), which only happens past the
opt-in gate.

Each trial prints one line (run with ``-s``)::

    PHASE3-TRIAL <name> commit=<sha> outcome=<...> date=<YYYY-MM-DD>

``outcome`` is the call's status (``status:code`` on a failure) followed by
the comma-separated fields saying what happened — never an endpoint, a token
or a secret. The README row cites it.

Variables (the first one switches the trial on; all are required):

* clips de plateforme — ``PHASE3_TRIAL_CLIPS`` and the Twitch credentials
  ``TWITCH_CLIENT_ID``, ``TWITCH_CLIENT_SECRET``, ``TWITCH_ACCESS_TOKEN``
  (clip-edit scope), ``TWITCH_BROADCASTER_ID``, ``TWITCH_BOT_USER_ID``; the
  channel must be live. One ``stream.clip.create``.
* modération de plateforme — ``PHASE3_MODERATION_MESSAGE_TEXT`` (the exact
  text a non-moderator test account posts once the trial is listening) and
  the Twitch credentials (chat-message moderation scope). ``moderation`` in
  mode ``act``; the observed message is deleted with one
  ``moderation.request``.
* notifications communautaires — ``PHASE3_TRIAL_NOTICES`` and the Twitch
  credentials (the moderator follower-read scope for ``follow``). The first
  ``raid`` or ``follow`` notice published on the broadcaster's channel.
* Kick — ``PHASE3_KICK_LISTENER_PORT`` (the local port the platform's public
  HTTPS endpoint forwards to), ``KICK_CLIENT_SECRET``, ``KICK_ACCESS_TOKEN``,
  ``KICK_CHANNEL_ID``; optional ``PHASE3_KICK_LISTENER_HOST`` (default
  ``127.0.0.1``), ``KICK_PUBLIC_KEY`` (else fetched at ``prepare``),
  ``KICK_BOT_USER_ID``. The first signed webhook accepted and published for
  the channel, then one ``chat.write``.
* YouTube — ``PHASE3_TRIAL_YOUTUBE``, ``YOUTUBE_CLIENT_ID``,
  ``YOUTUBE_CLIENT_SECRET``, ``YOUTUBE_REFRESH_TOKEN``, ``YOUTUBE_CHANNEL_ID``
  (a channel with an active broadcast). The token refresh, the first polled
  message published for the channel, then one ``chat.write`` under the
  quota ledger.
* veille d'écran — ``PHASE3_WATCH_CAPTURE_ARGV`` (shell-split; a screenshot
  command writing one PNG or JPEG on stdout); optional
  ``PHASE3_WATCH_TICKS`` (default 2) and ``PHASE3_WATCH_INTERVAL_SECONDS``
  (default 15, the smallest the module accepts). ``watch`` in activation
  ``startup`` ticks on the real clock; each admitted tick's run calls the
  real ``screen.capture`` under the principal ``brain.watch``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import os
import shlex
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from core.actions import AuthorizationPolicy, AuthorizationRule
from core.admission import AdmissionScheduler
from core.attachments import AttachmentStore
from core.context import ChatContext
from core.contracts import ActionCall, ActionObservation, Destination
from core.loader import ModuleLoader
from core.triggers import TriggerRegistry
from conftest import runtime_context

REPOSITORY = Path(__file__).resolve().parent.parent
MODULES_DIRECTORY = REPOSITORY / "modules"

CLIPS_TRIAL = "clips de plateforme"
MODERATION_TRIAL = "modération de plateforme"
NOTICES_TRIAL = "notifications communautaires"
KICK_TRIAL = "Kick"
YOUTUBE_TRIAL = "YouTube"
WATCH_TRIAL = "veille d'écran"

TWITCH_CREDENTIALS = (
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "TWITCH_ACCESS_TOKEN",
    "TWITCH_BROADCASTER_ID",
    "TWITCH_BOT_USER_ID",
)

#: Each trial's variables, in the README's R8 order; the first is its switch.
TRIAL_VARIABLES: dict[str, tuple[str, ...]] = {
    CLIPS_TRIAL: ("PHASE3_TRIAL_CLIPS", *TWITCH_CREDENTIALS),
    MODERATION_TRIAL: ("PHASE3_MODERATION_MESSAGE_TEXT", *TWITCH_CREDENTIALS),
    NOTICES_TRIAL: ("PHASE3_TRIAL_NOTICES", *TWITCH_CREDENTIALS),
    KICK_TRIAL: (
        "PHASE3_KICK_LISTENER_PORT",
        "KICK_CLIENT_SECRET",
        "KICK_ACCESS_TOKEN",
        "KICK_CHANNEL_ID",
    ),
    YOUTUBE_TRIAL: (
        "PHASE3_TRIAL_YOUTUBE",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
        "YOUTUBE_CHANNEL_ID",
    ),
    WATCH_TRIAL: ("PHASE3_WATCH_CAPTURE_ARGV",),
}

#: The variables whose values must never appear in a trial line.
SECRET_VARIABLES = (
    "TWITCH_CLIENT_SECRET",
    "TWITCH_ACCESS_TOKEN",
    "KICK_CLIENT_SECRET",
    "KICK_ACCESS_TOKEN",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
)

#: The call deadline, from the call's entry on the real clock; each action's
#: own ``timeout_seconds`` still bounds it first.
CALL_BUDGET_SECONDS = 120.0

#: How long a trial waits for the platform (or the watch) to produce what it
#: observes, unless ``PHASE3_TRIAL_WAIT_SECONDS`` says otherwise.
DEFAULT_WAIT_SECONDS = 300.0

COMPANION = "Companion"
TRIAL_TEXT = "Essai d'intégration de la phase trois."
CHAT_EVENT = "channel.chat.message"

CHAT_LIMITS: dict[str, Any] = {
    "max_messages": 200,
    "max_bytes": 262_144,
    "max_age_seconds": 3600.0,
}

STORE_LIMITS: dict[str, Any] = {
    "max_object_bytes": 4 * 1024 * 1024,
    "max_objects": 16,
    "max_total_bytes": 16 * 1024 * 1024,
    "max_bytes_per_run": 4 * 1024 * 1024,
    "ttl_seconds": 300.0,
}

ADMISSION: dict[str, Any] = {
    "session_queue_capacity": 4,
    "global_pending_capacity": 64,
    "max_sessions": 32,
    "workers": 4,
    "wait_seconds": 30,
    "total_run_seconds": 120,
}


# --------------------------------------------------------------------------- #
# The gate and the line
# --------------------------------------------------------------------------- #


def gate(trial: str) -> None:
    """Skip *trial* unless all its variables are set, naming them (R8)."""

    variables = TRIAL_VARIABLES[trial]
    missing = [name for name in variables if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(
            f"{trial}: opt-in trial, runs only with {', '.join(variables)} set "
            f"(unset: {', '.join(missing)})"
        )


def env(name: str, default: str = "") -> str:
    """A non-secret setting of a switched-on trial (an identifier, a port)."""

    return os.environ.get(name, "").strip() or default


def reference(name: str) -> str:
    """The ``${NAME}`` form the loader resolves: the value never passes here."""

    return "${" + name + "}"


def wait_seconds() -> float:
    return float(env("PHASE3_TRIAL_WAIT_SECONDS", str(DEFAULT_WAIT_SECONDS)))


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


def outcome_of(observation: ActionObservation) -> str:
    error = dict(observation.error or {})
    if error.get("code"):
        return f"{observation.status}:{error['code']}"
    return observation.status


def report(trial: str, outcome: str, **details: Any) -> str:
    """Print the one line the README row cites; it carries no secret."""

    fields = [outcome, *(f"{key}={value}" for key, value in details.items())]
    line = (
        f"PHASE3-TRIAL {trial} commit={commit()} outcome={','.join(fields)} "
        f"date={datetime.date.today().isoformat()}"
    )
    for name in SECRET_VARIABLES:
        value = os.environ.get(name, "").strip()
        # Checked without echoing the value: the message names the variable.
        assert not value or value not in line, f"the {trial} line would carry {name}"
    print(line)
    return line


def result_fields(observation: ActionObservation, *names: str) -> dict[str, Any]:
    result = observation.result or {}
    return {name: result.get(name) for name in names if name in result}


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #


class Trial:
    """One real runtime on the real clock, loaded through the real loader."""

    def __init__(
        self,
        *,
        attachments: bool = False,
        run_body: Callable[[Any], Any] | None = None,
    ) -> None:
        self.policy = AuthorizationPolicy()
        self.store = (
            AttachmentStore(clock=time.monotonic, **STORE_LIMITS) if attachments else None
        )
        runtime = runtime_context(
            clock=time.monotonic,  # type: ignore[arg-type]
            authorization=self.policy,
            attachments=self.store,
            trigger_registry=TriggerRegistry(companion_name=COMPANION),
            chat=ChatContext(clock=time.monotonic, **CHAT_LIMITS),
        )
        self.scheduler: AdmissionScheduler | None = None
        if run_body is not None:
            self.scheduler = AdmissionScheduler(
                run_body,
                **ADMISSION,
                clock=time.monotonic,
                supervision=runtime.supervision,
                run_module="brain",
                run_cleanup=None if self.store is None else self.store.release,
            )
            runtime = dataclasses.replace(runtime, scheduler=self.scheduler)
        self.runtime = runtime
        self.handles: dict[str, Any] = {}
        self._started: list[str] = []
        self._calls = 0

    async def load(self, modules: Mapping[str, Mapping[str, Any]]) -> None:
        """Validate and activate *modules* in order, then prepare each one."""

        loader = ModuleLoader(
            self.runtime.bus,
            MODULES_DIRECTORY,
            context=self.runtime,
            environ=os.environ,
            clock=time.monotonic,
        )
        activations = await loader.activate_enabled(
            {"enabled_modules": list(modules), "modules": dict(modules)}
        )
        self.handles.update({item.name: item.handle for item in activations})
        if self.scheduler is not None:
            await self.scheduler.start()
        for handle in self.handles.values():
            await handle.prepare()

    async def start_inputs(self, name: str, seconds: float) -> bool:
        """Open *name*'s input source inside *seconds*; ``False`` if it did not.

        Startup talks to the real transport (Twitch awaits its EventSub
        welcome), so it is bounded like the observation that follows. A start
        cancelled by the timeout releases its own connection.
        """

        try:
            await asyncio.wait_for(self.handles[name].start_inputs(), timeout=seconds)
        except asyncio.TimeoutError:
            return False
        self._started.append(name)
        return True

    async def close(self) -> None:
        for name in reversed(self._started):
            await self.handles[name].stop_inputs()
        if self.scheduler is not None:
            await self.scheduler.aclose()
        for handle in reversed(list(self.handles.values())):
            await handle.close()

    def grant(self, action: str, permission: str, principal: str = "brain") -> None:
        self.policy.grant(
            AuthorizationRule(
                rule_id=f"trial-{principal}-{action}",
                action_name=action,
                principals=(principal,),
                granted_permissions=(permission,),
            )
        )

    async def invoke(
        self,
        action: str,
        arguments: Mapping[str, Any],
        destination: Destination,
        *,
        principal: str = "brain",
        run_id: str = "phase3-trial-run",
        deadline: float | None = None,
    ) -> ActionObservation:
        self._calls += 1
        call = ActionCall(
            action_name=action,
            action_version=1,
            arguments=dict(arguments),
            conversation_id="phase3-trial",
            run_id=run_id,
            call_id=f"phase3-trial-{self._calls}",
            source_event_id="phase3-trial-source",
            destination=destination,
            principal=principal,
            deadline=time.monotonic() + CALL_BUDGET_SECONDS if deadline is None else deadline,
            message_id="phase3-trial-source",
        )
        return await self.runtime.executor.invoke(call)

    def observe(self, accept: Callable[[Mapping[str, Any]], bool]) -> asyncio.Future[Any]:
        """A future resolved with the first published chat payload *accept*s.

        Subscribed after every module prepared, so it runs last in the chain:
        by then the platform fed the chat context and the consumers indexed it.
        """

        found: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        def handler(event: Mapping[str, Any]) -> None:
            payload = event.get("payload") or {}
            if not found.done() and accept(payload):
                found.set_result(dict(payload))

        self.runtime.bus.subscribe(CHAT_EVENT, handler)
        return found


async def within(awaitable: Any, seconds: float) -> Any | None:
    """What the real transport produced inside *seconds*, or ``None``."""

    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError:
        return None


def twitch_settings(**extra: Any) -> dict[str, Any]:
    return {
        "client_id": reference("TWITCH_CLIENT_ID"),
        "client_secret": reference("TWITCH_CLIENT_SECRET"),
        "access_token": reference("TWITCH_ACCESS_TOKEN"),
        "broadcaster_id": reference("TWITCH_BROADCASTER_ID"),
        "bot_user_id": reference("TWITCH_BOT_USER_ID"),
        "companion_name": COMPANION,
        **extra,
    }


# --------------------------------------------------------------------------- #
# The six trials, in the R8 order
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_trial_platform_clips() -> None:
    """``stream.clip.create`` on the live Twitch channel through the clip
    service the platform module publishes: one create request, then the
    lookups of the confirmation window."""

    gate(CLIPS_TRIAL)
    from modules.clips import CLIP_ACTION

    broadcaster = env("TWITCH_BROADCASTER_ID")
    trial = Trial()
    try:
        await trial.load({"twitch": twitch_settings(), "clips": {"required": True}})
        trial.grant(CLIP_ACTION, "stream.clip")
        observation = await trial.invoke(
            CLIP_ACTION, {}, Destination("twitch", broadcaster, "clip")
        )
        report(
            CLIPS_TRIAL,
            outcome_of(observation),
            clip_confirmed=bool((observation.result or {}).get("url")),
        )
    finally:
        await trial.close()
    assert observation.status in {"success", "error", "timeout", "refused", "external_unknown"}


@pytest.mark.asyncio
async def test_trial_platform_moderation() -> None:
    """``moderation`` in mode ``act`` deletes the test message the trial
    observed on the Twitch chat: one ``moderation.request`` of
    ``delete_message``, under the strict rules, sent once."""

    gate(MODERATION_TRIAL)
    from modules.moderation import MODERATION_ACTION

    marker = env("PHASE3_MODERATION_MESSAGE_TEXT")
    broadcaster = env("TWITCH_BROADCASTER_ID")
    trial = Trial()
    observation: ActionObservation | None = None
    try:
        await trial.load({"twitch": twitch_settings(), "moderation": {"mode": "act"}})
        message = trial.observe(
            lambda payload: payload.get("channel_id") == broadcaster
            and payload.get("text") == marker
        )
        if not await trial.start_inputs("twitch", wait_seconds()):
            report(MODERATION_TRIAL, "not_started", waited_seconds=int(wait_seconds()))
            return
        payload = await within(message, wait_seconds())
        if payload is None:
            report(MODERATION_TRIAL, "not_observed", waited_seconds=int(wait_seconds()))
        else:
            trial.grant(MODERATION_ACTION, "moderation.request")
            observation = await trial.invoke(
                MODERATION_ACTION,
                {
                    "operation": "delete_message",
                    "message_id": payload["message_id"],
                    "reason": "Essai de modération de la phase trois.",
                },
                Destination("twitch", broadcaster, "moderation"),
            )
            report(
                MODERATION_TRIAL,
                outcome_of(observation),
                mode="act",
                operation="delete_message",
                **result_fields(observation, "disposition"),
            )
    finally:
        await trial.close()
    if observation is not None:
        assert observation.status in {"success", "error", "refused", "external_unknown"}


@pytest.mark.asyncio
async def test_trial_community_notices() -> None:
    """Twitch EventSub with the ``raid`` and ``follow`` notices listed: the
    first one the platform delivers for the broadcaster's channel is
    normalised, fed and published as a chat event with its ``kind``."""

    gate(NOTICES_TRIAL)
    broadcaster = env("TWITCH_BROADCASTER_ID")
    trial = Trial()
    try:
        await trial.load({"twitch": twitch_settings(notices={"kinds": ["raid", "follow"]})})
        notice = trial.observe(
            lambda payload: payload.get("channel_id") == broadcaster
            and payload.get("kind") in {"raid", "follow"}
        )
        if not await trial.start_inputs("twitch", wait_seconds()):
            report(NOTICES_TRIAL, "not_started", waited_seconds=int(wait_seconds()))
            return
        payload = await within(notice, wait_seconds())
        if payload is None:
            report(NOTICES_TRIAL, "not_observed", waited_seconds=int(wait_seconds()))
        else:
            report(
                NOTICES_TRIAL,
                "observed",
                kind=payload["kind"],
                fed=bool(trial.runtime.chat.live_channels()),
            )
    finally:
        await trial.close()


@pytest.mark.asyncio
async def test_trial_kick() -> None:
    """The Kick listener accepts one signed webhook for the channel (signature,
    timestamp and dedup checked, the event published), then one
    ``chat.write`` on that channel."""

    gate(KICK_TRIAL)
    channel = env("KICK_CHANNEL_ID")
    settings: dict[str, Any] = {
        "client_secret": reference("KICK_CLIENT_SECRET"),
        "access_token": reference("KICK_ACCESS_TOKEN"),
        "listener": {
            "host": env("PHASE3_KICK_LISTENER_HOST", "127.0.0.1"),
            "port": int(env("PHASE3_KICK_LISTENER_PORT")),
        },
        "channels": [channel],
        "companion_name": COMPANION,
    }
    if env("KICK_PUBLIC_KEY"):
        settings["public_key"] = reference("KICK_PUBLIC_KEY")
    if env("KICK_BOT_USER_ID"):
        settings["bot_user_id"] = env("KICK_BOT_USER_ID")
    trial = Trial()
    observation: ActionObservation | None = None
    try:
        await trial.load({"kick": settings})
        delivery = trial.observe(lambda payload: payload.get("channel_id") == channel)
        if not await trial.start_inputs("kick", wait_seconds()):
            report(KICK_TRIAL, "not_started", waited_seconds=int(wait_seconds()))
            return
        payload = await within(delivery, wait_seconds())
        if payload is None:
            report(KICK_TRIAL, "not_observed", waited_seconds=int(wait_seconds()))
        else:
            trial.grant("chat.write", "chat.write")
            observation = await trial.invoke(
                "chat.write", {"text": TRIAL_TEXT}, Destination("kick", channel, "chat")
            )
            report(
                KICK_TRIAL,
                outcome_of(observation),
                webhook=payload.get("kind") or "message",
            )
    finally:
        await trial.close()
    if observation is not None:
        assert observation.status in {"success", "error", "refused", "external_unknown"}


@pytest.mark.asyncio
async def test_trial_youtube() -> None:
    """The YouTube poller refreshes the token, finds the active broadcast and
    polls its live chat until one message is published; then one
    ``chat.write``, covered by the quota ledger."""

    gate(YOUTUBE_TRIAL)
    channel = env("YOUTUBE_CHANNEL_ID")
    settings = {
        "client_id": reference("YOUTUBE_CLIENT_ID"),
        "client_secret": reference("YOUTUBE_CLIENT_SECRET"),
        "refresh_token": reference("YOUTUBE_REFRESH_TOKEN"),
        "channels": [channel],
        "companion_name": COMPANION,
    }
    trial = Trial()
    observation: ActionObservation | None = None
    try:
        await trial.load({"youtube": settings})
        polled = trial.observe(lambda payload: payload.get("channel_id") == channel)
        if not await trial.start_inputs("youtube", wait_seconds()):
            report(YOUTUBE_TRIAL, "not_started", waited_seconds=int(wait_seconds()))
            return
        payload = await within(polled, wait_seconds())
        module = trial.handles["youtube"]
        if payload is None:
            report(
                YOUTUBE_TRIAL,
                "not_observed",
                token_refreshed=module.tokens.expires_at is not None,
                quota_remaining=module.ledger.remaining,
                waited_seconds=int(wait_seconds()),
            )
        else:
            trial.grant("chat.write", "chat.write")
            observation = await trial.invoke(
                "chat.write", {"text": TRIAL_TEXT}, Destination("youtube", channel, "chat")
            )
            report(
                YOUTUBE_TRIAL,
                outcome_of(observation),
                token_refreshed=module.tokens.expires_at is not None,
                polled="message",
                quota_remaining=module.ledger.remaining,
            )
    finally:
        await trial.close()
    if observation is not None:
        assert observation.status in {"success", "error", "refused", "external_unknown"}


@pytest.mark.asyncio
async def test_trial_screen_watch() -> None:
    """``watch`` in activation ``startup`` ticks on the real clock; each
    accepted tick is admitted to the real scheduler, whose run calls the real
    ``screen.capture`` (the configured command) under ``brain.watch``."""

    gate(WATCH_TRIAL)
    from modules.brain import WATCH_PRINCIPAL
    from modules.capture import SCREEN_CAPTURE_ACTION

    argv = shlex.split(os.environ["PHASE3_WATCH_CAPTURE_ARGV"])
    ticks = int(env("PHASE3_WATCH_TICKS", "2"))
    interval = int(env("PHASE3_WATCH_INTERVAL_SECONDS", "15"))
    platform, _, channel = env("PHASE3_WATCH_CHANNEL", "trial/screen").partition("/")
    captures: list[ActionObservation] = []
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    trial: Trial

    async def run_body(run: Any) -> None:
        observation = await trial.invoke(
            SCREEN_CAPTURE_ACTION,
            {},
            Destination(platform, channel, "capture"),
            principal=WATCH_PRINCIPAL,
            run_id=run.run_id,
            deadline=run.total_deadline,
        )
        captures.append(observation)
        if len(captures) >= ticks and not done.done():
            done.set_result(None)

    trial = Trial(attachments=True, run_body=run_body)
    try:
        await trial.load(
            {
                "capture": {
                    "sources": {"screen": {"kind": "command", "argv": argv}},
                    "default_source": "screen",
                },
                "watch": {
                    "channels": [f"{platform}/{channel}"],
                    "interval_seconds": interval,
                    "max_active_seconds": max(60, interval * (ticks + 2)),
                    "activation": "startup",
                },
            }
        )
        trial.grant(SCREEN_CAPTURE_ACTION, "screen.capture", principal=WATCH_PRINCIPAL)
        if not await trial.start_inputs("watch", CALL_BUDGET_SECONDS):
            report(WATCH_TRIAL, "not_started", waited_seconds=int(CALL_BUDGET_SECONDS))
            return
        await within(done, interval * (ticks + 1) + CALL_BUDGET_SECONDS)
        last = captures[-1] if captures else None
        report(
            WATCH_TRIAL,
            "not_observed" if last is None else outcome_of(last),
            ticks=len(captures),
            captured=sum(1 for item in captures if item.status == "success"),
            **({} if last is None else result_fields(last, "content_type", "width", "height")),
        )
    finally:
        await trial.close()
    assert all(
        item.status in {"success", "error", "timeout", "refused"} for item in captures
    )
