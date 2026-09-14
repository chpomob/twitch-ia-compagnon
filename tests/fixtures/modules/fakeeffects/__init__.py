"""Fictional effect providers on the v2 runtime — a test fixture (R1).

The manifest beside this package declares three ``write`` actions on wildcard
destinations — ``audio.say`` (delivery, text under ``text``),
``stream.set_scene`` (delivery, no text) and ``overlay.raw`` (no delivery
capability) — so the delivery scenarios of AC50–AC57 can configure a terminal
step beyond ``chat.write`` while the shipped brain names none of them (AC56).
The module plays no lifecycle role: ``prepare`` binds the three providers and
marks the module ready, ``close`` withdraws readiness.

Every provider **records** its invocations: ``calls`` on the provider (and
:attr:`FakeEffects.calls`, keyed by action) holds one entry per invocation
with the plain ``arguments`` the executor validated, the call id, the run id
and the destination. Every provider can be **scripted** through the
module-level :data:`SCRIPTS` mapping — action name to a queue of outcomes
consumed one per invocation, :data:`SUCCESS` once empty:

:data:`SUCCESS`              signal emission, return ``success`` with a
                             result matching the declared schema;
:data:`REFUSED`              return a ``refused`` observation (code
                             ``scripted_refusal``), nothing emitted;
:data:`ERROR`                return an ``error`` observation (code
                             ``scripted_error``), nothing emitted;
:data:`RAISE`                raise before signalling emission — the executor
                             reports ``error`` (``provider_failed``);
:data:`FAIL_AFTER_EMISSION`  raise after signalling emission — the executor
                             reports ``external_unknown``.

The loader imports this package under a private name, so a test activating
through it reaches the same mapping as :attr:`FakeEffects.scripts`, or hands
its own in through the ``scripts`` settings seam (a mapping of the same
shape, used instead of :data:`SCRIPTS` by that handle). ``validate_settings``
accepts ``{}``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from core.contracts import ActionObservation, ActionSpec, Destination


MODULE_NAME = "fakeeffects"
MANIFEST_PATH = Path(__file__).with_name("module.yaml")

AUDIO_SAY = "audio.say"
STREAM_SET_SCENE = "stream.set_scene"
OVERLAY_RAW = "overlay.raw"
ACTIONS: tuple[str, ...] = (AUDIO_SAY, STREAM_SET_SCENE, OVERLAY_RAW)
"""The three declared actions, in manifest (catalog) order."""

# Outcomes a provider can be scripted to produce for one invocation.
SUCCESS = "success"
REFUSED = "refused"
ERROR = "error"
RAISE = "raise"
FAIL_AFTER_EMISSION = "FAIL_AFTER_EMISSION"
OUTCOMES = frozenset({SUCCESS, REFUSED, ERROR, RAISE, FAIL_AFTER_EMISSION})

_ERROR_SCRIPTED_REFUSAL = "scripted_refusal"
_ERROR_SCRIPTED_ERROR = "scripted_error"
_ERROR_PROVIDER_CLOSED = "provider_closed"


class FakeEffectsError(RuntimeError):
    """A fixture failure whose text is safe to surface."""


SCRIPTS: dict[str, list[str]] = {}
"""Module-level scripts: action name to the queue of its next outcomes.

:func:`script` appends to it and :func:`reset` clears it. An action absent
from the mapping, or with an empty queue, succeeds.
"""


def script(action: str, *outcomes: str, scripts: dict[str, list[str]] | None = None) -> None:
    """Queue *outcomes* for the next invocations of *action*."""

    if action not in ACTIONS:
        raise FakeEffectsError(f"fakeeffects script: unknown action {action!r}")
    for outcome in outcomes:
        if outcome not in OUTCOMES:
            raise FakeEffectsError(f"fakeeffects script: unknown outcome {outcome!r}")
    target = SCRIPTS if scripts is None else scripts
    target.setdefault(action, []).extend(outcomes)


def reset() -> None:
    """Forget every scripted outcome."""

    SCRIPTS.clear()


def validate_settings(settings: Any) -> list[str]:
    """Accept ``{}``; refuse a non-mapping or a malformed ``scripts`` seam."""

    if not isinstance(settings, Mapping):
        return [_setting_diagnostic("settings", "must be a mapping")]
    scripts = settings.get("scripts")
    if scripts is None:
        return []
    if not isinstance(scripts, dict):
        return [_setting_diagnostic("scripts", "must be a mapping of action to outcomes")]
    diagnostics: list[str] = []
    for action, outcomes in scripts.items():
        if action not in ACTIONS:
            diagnostics.append(_setting_diagnostic(f"scripts.{action}", "is not a declared action"))
            continue
        if (
            isinstance(outcomes, str)
            or not isinstance(outcomes, list)
            or any(outcome not in OUTCOMES for outcome in outcomes)
        ):
            diagnostics.append(
                _setting_diagnostic(f"scripts.{action}", "must be a list of scripted outcomes")
            )
    return diagnostics


def _setting_diagnostic(field_name: str, reason: str) -> str:
    return f"module {MODULE_NAME!r}: field {field_name!r}: {reason}"


class FakeEffectProvider:
    """One recording, scriptable provider behind a declared action."""

    __slots__ = ("_module", "action", "calls", "name")

    def __init__(self, module: "FakeEffects", action: str) -> None:
        self._module = module
        self.action = action
        self.name = f"fake-{action}"
        self.calls: list[dict[str, Any]] = []

    @property
    def invocations(self) -> int:
        return len(self.calls)

    async def invoke(self, invocation: Any) -> ActionObservation:
        return await self._module._invoke(self, invocation)


class FakeEffects:
    """The v2 handle: three bound providers, one script queue per action."""

    def __init__(self, context: Any, scripts: dict[str, list[str]]) -> None:
        self._actions = context.actions
        self._scripts = scripts
        self.providers: dict[str, FakeEffectProvider] = {
            action: FakeEffectProvider(self, action) for action in ACTIONS
        }
        self._prepared = False
        self._closed = False

    # -- test seams -------------------------------------------------------- #

    @property
    def scripts(self) -> dict[str, list[str]]:
        """The script queues this handle consumes (module-level by default)."""

        return self._scripts

    @property
    def calls(self) -> Mapping[str, list[dict[str, Any]]]:
        """Recorded invocations per action, in invocation order."""

        return {action: provider.calls for action, provider in self.providers.items()}

    def provider(self, action: str) -> FakeEffectProvider:
        try:
            return self.providers[action]
        except KeyError:
            raise FakeEffectsError(f"fakeeffects: unknown action {action!r}") from None

    def script(self, action: str, *outcomes: str) -> None:
        """Queue *outcomes* for the next invocations of *action* on this handle."""

        script(action, *outcomes, scripts=self._scripts)

    # -- phases ------------------------------------------------------------ #

    async def prepare(self) -> None:
        """Bind every declared provider over its declared destinations."""

        if self._prepared or self._closed:
            return
        for spec in _declared_specs():
            provider = self.providers[spec.name]
            try:
                self._actions.register(spec, provider, provider_name=provider.name)
            except Exception as exc:
                raise FakeEffectsError(f"fakeeffects prepare: {exc}") from None
        self._actions.mark_ready()
        self._prepared = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._prepared:
            try:
                self._actions.mark_not_ready()
            except Exception:
                pass

    # -- invocation -------------------------------------------------------- #

    async def _invoke(self, provider: FakeEffectProvider, invocation: Any) -> ActionObservation:
        invocation.mark_not_emitted()
        call = invocation.call
        provider.calls.append(
            {
                "action": call.action_name,
                "arguments": _thaw(call.arguments),
                "call_id": call.call_id,
                "run_id": call.run_id,
                "destination": call.destination,
                "principal": call.principal,
            }
        )
        provenance = {"provider": provider.name, "action": provider.action}

        def failure(status: str, code: str, message: str) -> ActionObservation:
            return ActionObservation(
                status=status,
                provenance=provenance,
                error={"code": code, "message": message, "retryable": False},
            )

        if self._closed:
            return failure("error", _ERROR_PROVIDER_CLOSED, "fakeeffects: closed")
        queue = self._scripts.get(provider.action)
        outcome = queue.pop(0) if queue else SUCCESS
        if outcome == REFUSED:
            return failure("refused", _ERROR_SCRIPTED_REFUSAL, "scripted refusal")
        if outcome == ERROR:
            return failure("error", _ERROR_SCRIPTED_ERROR, "scripted error")
        if outcome == RAISE:
            raise FakeEffectsError(f"fakeeffects {provider.action}: scripted failure")
        invocation.mark_emitted()
        if outcome == FAIL_AFTER_EMISSION:
            raise FakeEffectsError(f"fakeeffects {provider.action}: no confirmation")
        return ActionObservation(
            status="success",
            provenance=provenance,
            result=_result(provider.action, call.arguments, provider.invocations),
        )


def _result(action: str, arguments: Mapping[str, Any], count: int) -> dict[str, Any]:
    if action == AUDIO_SAY:
        return {"utterance_id": f"utterance-{count}"}
    if action == STREAM_SET_SCENE:
        return {"scene": arguments["scene"]}
    return {"accepted": True}


def _thaw(value: Any) -> Any:
    """A plain, mutable copy of a frozen argument mapping."""

    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


async def activate(
    context: Any,
    settings: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]],
) -> FakeEffects:
    """Build the handle; the ``scripts`` seam replaces the module-level queues."""

    del catalog
    actions = getattr(context, "actions", None)
    if actions is None or not all(
        callable(getattr(actions, method, None))
        for method in ("register", "mark_ready", "mark_not_ready")
    ):
        raise FakeEffectsError("fakeeffects activation: runtime context is invalid")
    diagnostics = validate_settings(settings)
    if diagnostics:
        raise FakeEffectsError(diagnostics[0])
    scripts = settings.get("scripts")
    return FakeEffects(context, SCRIPTS if scripts is None else scripts)


def _declared_specs() -> tuple[ActionSpec, ...]:
    """Build the three contracts from the colocated manifest, in order."""

    try:
        manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
        specs = tuple(_spec(entry) for entry in manifest["actions"])
    except Exception:
        raise FakeEffectsError("fakeeffects prepare: manifest declaration is invalid") from None
    if tuple(spec.name for spec in specs) != ACTIONS:
        raise FakeEffectsError("fakeeffects prepare: manifest declares unexpected actions")
    return specs


def _spec(entry: Mapping[str, Any]) -> ActionSpec:
    return ActionSpec(
        name=entry["name"],
        version=entry["version"],
        description=entry["description"],
        argument_schema=entry["argument_schema"],
        result_schema=entry["result_schema"],
        nature=entry["nature"],
        required_permissions=tuple(entry.get("required_permissions", ())),
        supported_destinations=tuple(
            Destination(
                platform=item.get("platform"),
                channel_id=item.get("channel_id"),
                scope=item.get("scope"),
            )
            for item in entry["supported_destinations"]
        ),
        timeout_seconds=entry["timeout_seconds"],
        idempotency=entry["idempotency"],
        delivery=entry.get("delivery"),
    )

