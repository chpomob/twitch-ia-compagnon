from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

from conftest import ManualClock, settle, wait_until

from core.actions import ActionRegistry, AuthorizationPolicy, AuthorizationRule
from core.bus import EventBus
from core.contracts import ContractError, TriggerPolicy, TriggerRule
from core.lifecycle import SupervisedTasks, module_roles
from core.loader import ModuleLoadError, ModuleLoader
from core.runtime import RUNTIME_API, ModuleContext, RuntimeContext, Supervision
from core.triggers import TriggerEngine, TriggerRegistry


MODULE_SOURCE = """
class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    return Handle(settings)
"""


def make_module(
    root: Path,
    directory_name: str,
    *,
    manifest_name: str | None = None,
    manifest: dict | None = None,
    source: str = MODULE_SOURCE,
) -> Path:
    directory = root / directory_name
    directory.mkdir()
    data = manifest or {
        "name": manifest_name or directory_name,
        "produces": ["channel.chat.message"],
        "consumes": ["channel.*"],
        "middleware": False,
    }
    (directory / "module.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )
    (directory / "__init__.py").write_text(source, encoding="utf-8")
    return directory


@pytest.mark.asyncio
async def test_discovers_all_modules_and_activates_only_enabled(tmp_path: Path) -> None:
    make_module(tmp_path, "enabled")
    make_module(
        tmp_path,
        "disabled",
        source="async def activate(*args):\n    raise AssertionError('disabled')\n",
    )
    bus = object()
    enabled_settings = {"label": "enabled", "calls": [], "closes": []}
    disabled_settings = {"unused": True}
    loader = ModuleLoader(bus, tmp_path)

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["enabled"],
            "modules": {
                "enabled": enabled_settings,
                "disabled": disabled_settings,
            },
        }
    )

    assert list(loader.discovered) == ["disabled", "enabled"]
    assert set(loader.catalog) == {"disabled", "enabled"}
    assert [activation.name for activation in activations] == ["enabled"]
    assert len(enabled_settings["calls"]) == 1
    received_bus, received_settings, received_catalog = enabled_settings["calls"][0]
    assert received_bus is bus
    assert received_settings is enabled_settings
    assert received_catalog is loader.catalog
    assert set(received_catalog) == {"disabled", "enabled"}


@pytest.mark.asyncio
async def test_activation_close_is_idempotent_even_when_called_concurrently(
    tmp_path: Path,
) -> None:
    make_module(tmp_path, "one")
    settings = {"label": "one", "calls": [], "closes": []}
    activation = (
        await ModuleLoader(object(), tmp_path).load(
            {"enabled_modules": ["one"], "modules": {"one": settings}}
        )
    )[0]

    await asyncio.gather(activation.close(), activation.close(), activation.close())

    assert activation.closed is True
    assert settings["closes"] == ["one"]


def _base_config(*names: str) -> dict:
    return {
        "enabled_modules": list(names),
        "modules": {
            name: {"label": name, "calls": [], "closes": []} for name in names
        },
    }


HANGING_ACTIVATE_SOURCE = """
import asyncio


class Handle:
    async def close(self):
        return None


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    await asyncio.Future()
"""


@pytest.mark.asyncio
async def test_a_hanging_activation_is_bounded_by_the_startup_deadline(
    tmp_path: Path,
) -> None:
    """R4: a loader given the global startup deadline cannot hang on activate.

    The activation that never returns is cancelled at the deadline under a
    bounded grace, and the refusal names the module and its hook, without
    the module's own output.
    """

    make_module(tmp_path, "hang", source=HANGING_ACTIVATE_SOURCE)
    settings = {"label": "hang", "calls": [], "closes": []}
    loader = ModuleLoader(object(), tmp_path)

    with pytest.raises(ModuleLoadError) as raised:
        await asyncio.wait_for(
            loader.activate_enabled(
                {"enabled_modules": ["hang"], "modules": {"hang": settings}},
                deadline_at=time.monotonic() + 0.05,
            ),
            timeout=2,
        )

    assert raised.value.diagnostics == (
        "module 'hang': field 'activate': exceeded the global startup deadline",
    )
    assert loader.activations == []


LATE_HANDLE_ACTIVATE_SOURCE = """
import asyncio


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    try:
        # Still activating when the deadline arrives: nothing opens this.
        await settings["busy"].wait()
    except asyncio.CancelledError:
        # The handle is still returned, after the deadline cancelled us.
        return Handle(settings)
"""


@pytest.mark.asyncio
async def test_a_handle_returned_during_the_cancel_grace_is_kept_for_cleanup(
    tmp_path: Path,
) -> None:
    """R4: a late activation handle is not lost with the deadline that cut it.

    An activation that observes its cancellation and returns a handle during
    the grace window still opened resources: the deadline refusal stands, but
    the handle is registered so startup cleanup can close it. The deadline
    is driven from the injected clock: the activation is cut only when the
    test moves it, and the handle is returned inside the grace that follows.
    """

    clock = ManualClock()
    make_module(tmp_path, "late", source=LATE_HANDLE_ACTIVATE_SOURCE)
    settings = {"label": "late", "calls": [], "closes": [], "busy": asyncio.Event()}
    loader = ModuleLoader(object(), tmp_path, clock=clock, sleeper=clock.sleep)

    task = asyncio.ensure_future(
        loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}},
            deadline_at=clock.now + 5.0,
        )
    )
    await wait_until(lambda: settings["calls"])
    await settle()
    assert not task.done()
    clock.advance(5.0)  # the startup deadline cancels the activation
    await wait_until(task.done)
    with pytest.raises(ModuleLoadError) as raised:
        task.result()

    assert raised.value.diagnostics == (
        "module 'late': field 'activate': exceeded the global startup deadline",
    )
    assert [activation.name for activation in loader.activations] == ["late"]
    for activation in loader.activations:
        await activation.close()
    assert settings["closes"] == ["late"]


IMMEDIATE_HANDLE_ON_CANCEL_SOURCE = """
import asyncio


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        # Neither slow nor cancellation-resistant: the handle already opened
        # is simply returned from the cancellation handler, on the next turn.
        return Handle(settings)
"""


@pytest.mark.asyncio
async def test_a_handle_returned_from_the_cancellation_handler_is_kept_when_the_caller_is_cancelled(
    tmp_path: Path,
) -> None:
    """AC15 (P24 N2): caller cancellation gives the hook the same bounded grace.

    The caller is cancelled while ``activate()`` is in flight and the hook
    answers its cancellation by returning the handle it opened, immediately.
    Without a scheduling turn between the cancellation and the salvage that
    handle was discarded unregistered, with no cleanup owner. It is now
    registered before the cancellation propagates, so the entry point's
    snapshot of ``activations`` closes it like any other partial activation.
    """

    make_module(tmp_path, "late", source=IMMEDIATE_HANDLE_ON_CANCEL_SOURCE)
    settings = {"label": "late", "calls": [], "closes": []}
    loader = ModuleLoader(object(), tmp_path)
    task = asyncio.ensure_future(
        loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}}
        )
    )
    await wait_until(lambda: settings["calls"])
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)

    assert loader.cancellation_diagnostics == [
        "module 'late': field 'activate': activation was cancelled"
    ]
    assert [activation.name for activation in loader.activations] == ["late"]
    for activation in loader.activations:
        await activation.close()
    assert settings["closes"] == ["late"]
    assert await loader.settle_late_results() == []


HANDLE_AFTER_GRACE_SOURCE = """
import asyncio


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


async def activate(bus, settings, catalog):
    settings["calls"].append((bus, settings, catalog))
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    # Cancelled, then still busy past the grace: the handle is returned only
    # once the test opens the gate, after it observed the caller answered.
    await settings["gate"].wait()
    return Handle(settings)
"""


def _after_grace_settings(label: str = "late") -> dict:
    """Settings for ``HANDLE_AFTER_GRACE_SOURCE``: the gate is the test's."""

    return {"label": label, "calls": [], "closes": [], "gate": asyncio.Event()}


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["deadline", "caller"])
async def test_a_handle_returned_after_the_grace_is_closed_by_the_loader(
    tmp_path: Path, interruption: str
) -> None:
    """R4/AC15 (P24 N2): a handle returned after the grace has a cleanup owner.

    Whether the startup deadline or the caller's cancellation cut the hook,
    a handle it returns only after the grace window arrives once the caller
    has been answered and — through the entry point — has already read
    ``activations``. It is therefore never appended there; the loader owns
    its bounded close and reports that close's diagnostics on request.
    """

    make_module(tmp_path, "late", source=HANDLE_AFTER_GRACE_SOURCE)
    settings = _after_grace_settings()
    loader = ModuleLoader(object(), tmp_path, cancel_grace_seconds=0.005)
    config = {"enabled_modules": ["late"], "modules": {"late": settings}}

    if interruption == "deadline":
        with pytest.raises(ModuleLoadError) as raised:
            await loader.activate_enabled(
                config, deadline_at=time.monotonic() + 0.01
            )
        assert raised.value.diagnostics == (
            "module 'late': field 'activate': exceeded the global startup deadline",
        )
    else:
        task = asyncio.ensure_future(loader.activate_enabled(config))
        await wait_until(lambda: settings["calls"])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert loader.cancellation_diagnostics == [
            "module 'late': field 'activate': activation was cancelled"
        ]

    # Nothing was registered: the caller has been answered and its snapshot
    # of the activations is complete without this handle.
    assert loader.activations == []
    assert settings["closes"] == []

    # The handle arrives only now, and is closed by the loader, not lost.
    settings["gate"].set()
    await wait_until(lambda: settings["closes"] == ["late"])
    assert await loader.settle_late_results() == []
    assert loader.activations == []


LATE_CLOSE_FAILS_SOURCE = HANDLE_AFTER_GRACE_SOURCE.replace(
    '        self.settings["closes"].append(self.settings["label"])\n',
    '        self.settings["closes"].append(self.settings["label"])\n'
    '        raise RuntimeError(self.settings["label"] + " refused to close")\n',
)


@pytest.mark.asyncio
async def test_a_failed_late_close_is_reported_by_module_without_its_message(
    tmp_path: Path,
) -> None:
    """The loader's late close is bounded and its failure is a named diagnostic."""

    make_module(tmp_path, "late", source=LATE_CLOSE_FAILS_SOURCE)
    settings = _after_grace_settings()
    loader = ModuleLoader(object(), tmp_path, cancel_grace_seconds=0.005)

    with pytest.raises(ModuleLoadError):
        await loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}},
            deadline_at=time.monotonic() + 0.01,
        )
    settings["gate"].set()
    await wait_until(lambda: settings["closes"] == ["late"])

    diagnostics = await loader.settle_late_results()
    assert diagnostics == ["module 'late': field 'close': late handle close failed"]
    assert "refused to close" not in "\n".join(diagnostics)
    # Handed over once: a later settle does not repeat it.
    assert await loader.settle_late_results() == []
    assert loader.late_diagnostics == diagnostics


@pytest.mark.asyncio
async def test_a_late_close_that_finishes_after_settling_reports_through_the_reporter(
    tmp_path: Path,
) -> None:
    """A late close's failure reaches its reporter even when nobody settles.

    The abandoned hook returns its handle whenever it does — here after the
    caller has been answered and ``settle_late_results()`` has returned with
    nothing under way. The loader's reporter outlives both, so the close's
    diagnostic is reported the moment it exists, and the next settle does
    not repeat what the reporter already received.
    """

    make_module(tmp_path, "late", source=LATE_CLOSE_FAILS_SOURCE)
    settings = _after_grace_settings()
    reported: list[str] = []
    loader = ModuleLoader(object(), tmp_path, cancel_grace_seconds=0.005)
    loader.late_reporter = reported.append

    with pytest.raises(ModuleLoadError):
        await loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}},
            deadline_at=time.monotonic() + 0.01,
        )
    # Settled before the handle arrived: no close is under way yet.
    assert settings["closes"] == []
    assert await loader.settle_late_results() == []
    assert reported == []

    settings["gate"].set()
    await wait_until(lambda: reported == [
        "module 'late': field 'close': late handle close failed"
    ])
    assert settings["closes"] == ["late"]
    assert "refused to close" not in "\n".join(reported)
    assert await loader.settle_late_results() == []
    assert loader.late_diagnostics == reported


@pytest.mark.asyncio
async def test_a_failing_late_reporter_keeps_the_diagnostic_for_the_next_settle(
    tmp_path: Path,
) -> None:
    """A reporter that raises loses nothing: the settle hands the diagnostic over."""

    make_module(tmp_path, "late", source=LATE_CLOSE_FAILS_SOURCE)
    settings = _after_grace_settings()
    loader = ModuleLoader(object(), tmp_path, cancel_grace_seconds=0.005)

    def refuse(diagnostic: str) -> None:
        raise RuntimeError("reporter unavailable")

    loader.late_reporter = refuse

    with pytest.raises(ModuleLoadError):
        await loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}},
            deadline_at=time.monotonic() + 0.01,
        )
    settings["gate"].set()
    await wait_until(lambda: settings["closes"] == ["late"])
    await wait_until(lambda: loader.late_diagnostics)

    assert await loader.settle_late_results() == [
        "module 'late': field 'close': late handle close failed"
    ]
    assert await loader.settle_late_results() == []


HELD_LATE_CLOSE_SOURCE = HANDLE_AFTER_GRACE_SOURCE.replace(
    '    async def close(self):\n'
    '        self.settings["closes"].append(self.settings["label"])\n',
    '    async def close(self):\n'
    '        self.settings["closes"].append(self.settings["label"])\n'
    '        self.settings["close_task"] = asyncio.current_task()\n'
    '        try:\n'
    '            await self.settings["hold"].wait()\n'
    '        except asyncio.CancelledError:\n'
    '            self.settings["cancelled"].append(self.settings["label"])\n'
    '            # Resists the one cancellation its owner gives it: it ends\n'
    '            # only when the test releases it, after every deadline.\n'
    '            await self.settings["hold"].wait()\n'
    '        self.settings["released"].append(self.settings["label"])\n',
)
"""A late handle whose ``close()`` is held, observes, then resists cancellation."""

EXCEEDED_CLEANUP_DEADLINE = (
    "module 'late': field 'close': late handle close exceeded the global "
    "shutdown deadline"
)


def _held_close_settings() -> dict:
    return {
        **_after_grace_settings(),
        "hold": asyncio.Event(),
        "cancelled": [],
        "released": [],
    }


async def _abandon_activation_on_the_clock(
    loader: ModuleLoader, clock: ManualClock, settings: dict
) -> None:
    """Drive ``activate_enabled`` to its startup deadline and past its grace.

    The startup budget is 5 seconds and the grace 1 second on *clock*; the
    hook, cancelled at the deadline, keeps its handle until the test opens
    ``settings["gate"]``, so the caller is answered with the handle unseen.
    """

    task = asyncio.ensure_future(
        loader.activate_enabled(
            {"enabled_modules": ["late"], "modules": {"late": settings}},
            deadline_at=clock.now + 5.0,
        )
    )
    await wait_until(lambda: settings["calls"])
    await settle()
    clock.advance(5.0)  # the startup deadline: the hook is cancelled
    await settle()
    assert not task.done()
    clock.advance(1.0)  # its grace: the caller is answered
    await wait_until(task.done)
    with pytest.raises(ModuleLoadError) as raised:
        task.result()
    assert raised.value.diagnostics == (
        "module 'late': field 'activate': exceeded the global startup deadline",
    )
    assert loader.activations == []
    assert settings["closes"] == []


@pytest.mark.asyncio
async def test_a_late_close_is_capped_by_the_shared_cleanup_deadline(
    tmp_path: Path,
) -> None:
    """R4 (P24 N4): a late close takes no budget of its own past the deadline.

    The entry point establishes one absolute cleanup deadline on the
    loader's clock and spends most of it unwinding the snapshot. The
    abandoned hook returns its handle one second before that deadline and
    the handle's close is held: the loader caps the close by what remains,
    not by its own 60-second budget, and the settle awaited under the same
    deadline returns the moment it is reached — cancelling the close,
    reporting it as having exceeded the global shutdown deadline, and
    keeping the resisting task owned until it ends. No positive wait is
    spent anywhere: time moves only when this test moves it.
    """

    clock = ManualClock()
    make_module(tmp_path, "late", source=HELD_LATE_CLOSE_SOURCE)
    settings = _held_close_settings()
    reported: list[str] = []
    loader = ModuleLoader(
        object(),
        tmp_path,
        clock=clock,
        sleeper=clock.sleep,
        cancel_grace_seconds=1.0,
        late_close_seconds=60.0,
    )
    loader.late_reporter = reported.append
    await _abandon_activation_on_the_clock(loader, clock, settings)

    # The one cleanup deadline, shared with the coordinator that spends
    # nine of its ten seconds before the handle arrives.
    deadline_at = clock.now + 10.0
    loader.cleanup_deadline_at = deadline_at
    clock.advance(9.0)
    settings["gate"].set()
    await wait_until(lambda: settings["closes"] == ["late"])

    settling = asyncio.ensure_future(
        loader.settle_late_results(deadline_at=deadline_at)
    )
    await settle()
    # Within the deadline the held close is waited for, and nothing is
    # reported yet.
    assert not settling.done()
    assert reported == []
    assert settings["cancelled"] == []

    clock.advance(1.0)  # the shared deadline, one second later
    await wait_until(settling.done)
    assert clock.now == deadline_at
    assert settling.result() == []
    assert reported == [EXCEEDED_CLEANUP_DEADLINE]
    assert loader.late_diagnostics == [EXCEEDED_CLEANUP_DEADLINE]
    # The cancellation reached the held close; it resisted, and the loader
    # still owns it rather than waiting for it.
    assert settings["cancelled"] == ["late"]
    assert settings["released"] == []
    assert loader.abandoned == (settings["close_task"],)
    assert await loader.settle_late_results() == []

    settings["hold"].set()
    await wait_until(lambda: settings["released"] == ["late"])
    assert loader.abandoned == ()
    assert settings["closes"] == ["late"]
    assert reported == [EXCEEDED_CLEANUP_DEADLINE]


@pytest.mark.asyncio
async def test_settling_past_the_cleanup_deadline_gives_up_without_waiting(
    tmp_path: Path,
) -> None:
    """R4 (P24 N4): a settle whose deadline has passed never extends the wait.

    The close under way began before any cleanup deadline was known, so it
    runs under its own 60-second budget; the settle is then given a deadline
    already behind the clock. It returns within scheduling turns — the
    clock never moves — with the close cancelled, the unfinished cleanup
    named, and the resisting task retained by the loader.
    """

    clock = ManualClock()
    make_module(tmp_path, "late", source=HELD_LATE_CLOSE_SOURCE)
    settings = _held_close_settings()
    loader = ModuleLoader(
        object(),
        tmp_path,
        clock=clock,
        sleeper=clock.sleep,
        cancel_grace_seconds=1.0,
        late_close_seconds=60.0,
    )
    await _abandon_activation_on_the_clock(loader, clock, settings)

    settings["gate"].set()
    await wait_until(lambda: settings["closes"] == ["late"])
    now = clock.now

    diagnostics = await loader.settle_late_results(deadline_at=now - 1.0)

    assert clock.now == now
    assert diagnostics == [EXCEEDED_CLEANUP_DEADLINE]
    assert loader.late_diagnostics == [EXCEEDED_CLEANUP_DEADLINE]
    assert settings["cancelled"] == ["late"]
    assert loader.abandoned == (settings["close_task"],)
    # Handed over once; the loader still owns the task it gave up on.
    assert await loader.settle_late_results() == []
    assert loader.abandoned == (settings["close_task"],)

    settings["hold"].set()
    await wait_until(lambda: settings["released"] == ["late"])
    assert loader.abandoned == ()
    assert loader.late_diagnostics == [EXCEEDED_CLEANUP_DEADLINE]


@pytest.mark.asyncio
async def test_a_spent_startup_budget_refuses_before_scheduling_the_hook(
    tmp_path: Path,
) -> None:
    """R4: an exhausted deadline never schedules the activation hook.

    A hook given a scheduling turn on a zero remaining budget could open
    resources past the deadline and pass as an immediate completion.
    """

    make_module(tmp_path, "spent", source=HANGING_ACTIVATE_SOURCE)
    settings = {"label": "spent", "calls": [], "closes": []}
    loader = ModuleLoader(object(), tmp_path)

    with pytest.raises(ModuleLoadError) as raised:
        await loader.activate_enabled(
            {"enabled_modules": ["spent"], "modules": {"spent": settings}},
            deadline_at=time.monotonic() - 1,
        )

    assert raised.value.diagnostics == (
        "module 'spent': field 'activate': exceeded the global startup deadline",
    )
    assert settings["calls"] == []
    assert loader.activations == []


LEAKY_ACTIVATE_SOURCE = MODULE_SOURCE.replace(
    'async def activate(bus, settings, catalog):\n'
    '    settings["calls"].append((bus, settings, catalog))\n'
    '    return Handle(settings)\n',
    "async def activate(bus, settings, catalog):\n"
    "    from core.loader import ModuleLoadError\n"
    "\n"
    "    raise ModuleLoadError(settings['api_key'])\n",
)


@pytest.mark.asyncio
async def test_an_activation_cannot_echo_the_rejected_credential(
    tmp_path: Path,
) -> None:
    """R7/AC24: what ``activate()`` raises never becomes the diagnostic.

    A module raising the loader's own error type is not a pass-through: it
    was handed its settings, so its message may be the credential.
    """

    make_module(tmp_path, "leaky", source=LEAKY_ACTIVATE_SOURCE)
    settings = {"label": "leaky", "calls": [], "closes": [], "api_key": "s3cret"}
    loader = ModuleLoader(object(), tmp_path)

    with pytest.raises(ModuleLoadError) as raised:
        await loader.activate_enabled(
            {"enabled_modules": ["leaky"], "modules": {"leaky": settings}}
        )

    assert raised.value.diagnostics == (
        "module 'leaky': field 'activate': activation failed",
    )
    assert "s3cret" not in str(raised.value)
    assert loader.activations == []


@pytest.mark.asyncio
async def test_a_hanging_settings_validator_is_bounded_by_the_startup_deadline(
    tmp_path: Path,
) -> None:
    """R4: an asynchronous settings hook shares the same deadline.

    The validator that never returns is reported as a refusal naming the
    module and its hook, before any module is activated (AC24).
    """

    make_module(
        tmp_path,
        "hang",
        manifest={
            "name": "hang",
            "produces": [],
            "consumes": [],
            "middleware": False,
            "manifest_version": 2,
            "runtime_api": 2,
            "settings_validator": "validate_settings",
        },
        source="import asyncio\n" + MODULE_SOURCE
        + "\n\n"
        + "async def validate_settings(settings):\n"
        + "    await asyncio.Future()\n",
    )
    settings = {"label": "hang", "calls": [], "closes": []}
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError) as raised:
        await asyncio.wait_for(
            loader.activate_enabled(
                {"enabled_modules": ["hang"], "modules": {"hang": settings}},
                deadline_at=time.monotonic() + 0.05,
            ),
            timeout=2,
        )

    assert raised.value.diagnostics == (
        "module 'hang': field 'validate_settings': exceeded the global "
        "startup deadline",
    )
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_a_cancelled_activation_names_the_module_and_keeps_prior_handles(
    tmp_path: Path,
) -> None:
    """AC15: a cancellation during ``activate()`` names the cancelling module.

    Cancelling the caller while the second module's ``activate()`` is in
    flight propagates the cancellation, records one sanitised diagnostic
    naming that module and its hook, and keeps the handle the first module
    already returned available for cleanup.
    """

    make_module(tmp_path, "first")
    make_module(tmp_path, "second", source=HANGING_ACTIVATE_SOURCE)
    first_settings = {"label": "first", "calls": [], "closes": []}
    second_settings = {"label": "second", "calls": [], "closes": []}
    loader = ModuleLoader(object(), tmp_path)
    task = asyncio.ensure_future(
        loader.activate_enabled(
            {
                "enabled_modules": ["first", "second"],
                "modules": {"first": first_settings, "second": second_settings},
            }
        )
    )
    await wait_until(lambda: second_settings["calls"])
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)

    assert loader.cancellation_diagnostics == [
        "module 'second': field 'activate': activation was cancelled"
    ]
    assert [activation.name for activation in loader.activations] == ["first"]


@pytest.mark.asyncio
async def test_an_activation_that_cancels_itself_is_a_named_failure(
    tmp_path: Path,
) -> None:
    """AC15: a module-internal ``CancelledError`` terminates, not propagates.

    An ``activate()`` that raises ``CancelledError`` on its own — the caller
    was not cancelled — is a failed activation reported by module and hook,
    so the entry point returns a non-zero status instead of a cancellation.
    """

    make_module(
        tmp_path,
        "selfcancelling",
        source="""
import asyncio


class Handle:
    async def close(self):
        return None


async def activate(bus, settings, catalog):
    await asyncio.sleep(0)
    raise asyncio.CancelledError()
""",
    )
    settings = {"label": "selfcancelling", "calls": [], "closes": []}
    loader = ModuleLoader(object(), tmp_path)

    with pytest.raises(ModuleLoadError) as raised:
        await loader.activate_enabled(
            {
                "enabled_modules": ["selfcancelling"],
                "modules": {"selfcancelling": settings},
            }
        )

    assert raised.value.diagnostics == (
        "module 'selfcancelling': field 'activate': was cancelled",
    )
    assert loader.cancellation_diagnostics == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "field"),
    [
        ("duplicate_name", "name"),
        ("non_list_produces", "produces"),
        ("missing_middleware", "middleware"),
        ("non_bool_middleware", "middleware"),
        ("non_int_order", "order"),
        ("bool_order", "order"),
        ("invalid_pattern", "consumes"),
        ("unknown_enabled", "enabled_modules"),
        ("missing_enabled_settings", "modules"),
        ("unknown_settings", "modules"),
    ],
)
async def test_validation_failure_prevents_every_activation(
    tmp_path: Path, case: str, field: str
) -> None:
    calls: list = []
    source = """
async def activate(bus, settings, catalog):
    settings["calls"].append("activated")
    raise AssertionError("validation should have happened first")
"""
    make_module(tmp_path, "valid", source=source)
    config = {"enabled_modules": ["valid"], "modules": {"valid": {"calls": calls}}}

    if case == "duplicate_name":
        make_module(tmp_path, "duplicate", manifest_name="valid")
    elif case == "non_list_produces":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": "channel.chat.message",
                "consumes": [],
                "middleware": False,
            },
        )
    elif case == "missing_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={"name": "broken", "produces": [], "consumes": []},
        )
    elif case == "non_bool_middleware":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": [],
                "middleware": "false",
            },
        )
    elif case in {"non_int_order", "bool_order"}:
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["**"],
                "middleware": True,
                "order": "90" if case == "non_int_order" else True,
            },
        )
    elif case == "invalid_pattern":
        make_module(
            tmp_path,
            "broken",
            manifest={
                "name": "broken",
                "produces": [],
                "consumes": ["channel*"],
                "middleware": False,
            },
        )
    elif case == "unknown_enabled":
        config["enabled_modules"].append("missing")
        config["modules"]["missing"] = {}
    elif case == "missing_enabled_settings":
        config["modules"].pop("valid")
    elif case == "unknown_settings":
        config["modules"]["missing"] = {}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), tmp_path).load(config)

    assert field in str(caught.value)
    assert calls == []


@pytest.mark.asyncio
async def test_non_module_directories_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    make_module(tmp_path, "enabled")
    settings = {"label": "enabled", "calls": [], "closes": []}

    activations = await ModuleLoader(object(), tmp_path).activate_enabled(
        {"enabled_modules": ["enabled"], "modules": {"enabled": settings}}
    )

    assert [activation.name for activation in activations] == ["enabled"]


@pytest.mark.asyncio
async def test_symlinked_module_directory_is_not_discovered(tmp_path: Path) -> None:
    modules_root = tmp_path / "modules"
    modules_root.mkdir()
    outside = tmp_path / "outside"
    make_module(outside.parent, outside.name)
    (modules_root / "linked").symlink_to(outside, target_is_directory=True)

    loader = ModuleLoader(object(), modules_root)
    activations = await loader.activate_enabled(
        {"enabled_modules": [], "modules": {}}
    )

    assert activations == []
    assert loader.discovered == {}


@pytest.mark.asyncio
async def test_entry_point_symlink_cannot_escape_module_directory(
    tmp_path: Path,
) -> None:
    module = make_module(tmp_path, "linked_init")
    external_source = tmp_path / "external.py"
    external_source.write_text(MODULE_SOURCE, encoding="utf-8")
    (module / "__init__.py").unlink()
    (module / "__init__.py").symlink_to(external_source)

    with pytest.raises(ModuleLoadError, match="stay inside"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("linked_init")
        )


@pytest.mark.asyncio
async def test_failed_entry_point_validation_does_not_leak_imports(
    tmp_path: Path,
) -> None:
    module = make_module(
        tmp_path,
        "invalid_entry",
        source="from . import helper\nVALUE = helper.VALUE\n",
    )
    (module / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = {name for name in sys.modules if name.startswith("_companion_module_")}

    with pytest.raises(ModuleLoadError, match="activate"):
        await ModuleLoader(object(), tmp_path).activate_enabled(
            _base_config("invalid_entry")
        )

    after = {name for name in sys.modules if name.startswith("_companion_module_")}
    assert after == before


@pytest.mark.asyncio
async def test_invalid_close_hook_remains_in_partial_activation_set(
    tmp_path: Path,
) -> None:
    make_module(
        tmp_path,
        "invalid_close",
        source="""
class Handle:
    pass

async def activate(bus, settings, catalog):
    settings["activated"] = True
    return Handle()
""",
    )
    loader = ModuleLoader(object(), tmp_path)
    settings: dict = {}

    with pytest.raises(ModuleLoadError, match="close"):
        await loader.activate_enabled(
            {
                "enabled_modules": ["invalid_close"],
                "modules": {"invalid_close": settings},
            }
        )

    assert settings["activated"] is True
    assert [activation.name for activation in loader.activations] == [
        "invalid_close"
    ]


@pytest.mark.asyncio
async def test_capability_catalog_is_recursively_read_only(tmp_path: Path) -> None:
    make_module(tmp_path, "one")
    loader = ModuleLoader(object(), tmp_path)
    settings = {"label": "one", "calls": [], "closes": []}

    await loader.activate_enabled(
        {"enabled_modules": ["one"], "modules": {"one": settings}}
    )

    with pytest.raises(TypeError):
        loader.catalog["two"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        loader.catalog["one"]["name"] = "renamed"  # type: ignore[index]
    assert loader.catalog["one"]["produces"] == ("channel.chat.message",)


@pytest.mark.asyncio
async def test_arbitrary_filesystem_module_needs_no_core_registration(
    tmp_path: Path,
) -> None:
    # Naming the directory after a standard-library package catches accidental
    # package-name imports instead of loading this exact __init__.py file.
    make_module(tmp_path, "json", manifest_name="fourth_party")
    settings = {
        "label": "fourth_party",
        "calls": [],
        "closes": [],
    }

    activations = await ModuleLoader(object(), tmp_path).load(
        {
            "enabled_modules": ["fourth_party"],
            "modules": {"fourth_party": settings},
        }
    )

    assert [activation.name for activation in activations] == ["fourth_party"]
    assert len(settings["calls"]) == 1


@pytest.mark.asyncio
async def test_import_and_activation_errors_are_sanitized(tmp_path: Path) -> None:
    make_module(tmp_path, "broken_import", source="raise RuntimeError('secret-value')")
    with pytest.raises(ModuleLoadError) as imported:
        await ModuleLoader(object(), tmp_path).load(
            _base_config("broken_import")
        )
    assert "broken_import" in str(imported.value)
    assert "__init__.py" in str(imported.value)
    assert "secret-value" not in str(imported.value)

    other_root = tmp_path / "other"
    other_root.mkdir()
    make_module(
        other_root,
        "broken_activation",
        source="async def activate(*args):\n    raise RuntimeError('secret-value')\n",
    )
    with pytest.raises(ModuleLoadError) as activated:
        await ModuleLoader(object(), other_root).load(
            _base_config("broken_activation")
        )
    assert "broken_activation" in str(activated.value)
    assert "activate" in str(activated.value)
    assert "secret-value" not in str(activated.value)


# --------------------------------------------------------------------------- #
# Manifest v2, the v1 compatibility route, and declared actions/triggers
# (R7, R5, R4 — AC25, AC26)
# --------------------------------------------------------------------------- #

V2_MODULE_SOURCE = """
from core.contracts import ActionSpec, Destination


class Provider:
    name = "recorder"

    async def invoke(self, invocation):
        raise AssertionError("no call is made during activation")


class Handle:
    def __init__(self, settings):
        self.settings = settings

    async def prepare(self):
        self.settings["prepared"] = True

    async def start_inputs(self):
        self.settings["started"] = True

    async def close(self):
        self.settings["closes"].append(self.settings["label"])


def validate_settings(settings):
    settings["validated"] = True


async def activate(context, settings, catalog):
    settings["calls"].append((context, settings, catalog))
    settings["module"] = context.module
    settings["runtime_api"] = context.runtime_api
    settings["bus"] = context.bus
    context.actions.bind(
        "chat.write",
        Provider(),
        destinations=Destination("twitch", "*", "chat"),
    )
    context.actions.mark_ready()
    return Handle(settings)
"""


def v2_manifest(
    name: str,
    *,
    roles: list[str] | None = None,
    actions: list[dict] | None = None,
    triggers: dict | None = None,
    settings_schema: dict | None = None,
    settings_validator: str | None = None,
    credentials: list[str] | None = None,
    runtime_api: int = RUNTIME_API,
    manifest_version: int = 2,
) -> dict:
    """A minimal v2 manifest, with only the declarations a test cares about."""

    manifest: dict = {
        "name": name,
        "manifest_version": manifest_version,
        "runtime_api": runtime_api,
        "produces": [],
        "consumes": ["channel.*"],
        "middleware": False,
    }
    if roles is not None:
        manifest["lifecycle"] = {"roles": roles}
    if actions is not None:
        manifest["actions"] = actions
    if triggers is not None:
        manifest["triggers"] = triggers
    if settings_schema is not None:
        manifest["settings_schema"] = settings_schema
    if settings_validator is not None:
        manifest["settings_validator"] = settings_validator
    if credentials is not None:
        manifest["credentials"] = credentials
    return manifest


CHAT_WRITE_ACTION = {
    "name": "chat.write",
    "version": 1,
    "description": "Send one chat message",
    "argument_schema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "result_schema": {
        "type": "object",
        "properties": {"delivered": {"type": "boolean"}},
        "required": ["delivered"],
    },
    "nature": "write",
    "required_permissions": ["chat.write"],
    "supported_destinations": [
        {"platform": "twitch", "channel_id": "*", "scope": "chat"}
    ],
    "timeout_seconds": 5,
    "idempotency": "none",
}

KEYWORD_TRIGGERS = {
    "types": [
        {
            "name": "keyword",
            "parameter_schema": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
        }
    ],
    "combinations": ["all_of", "any_of"],
    "default_policy": {
        "combination": "all_of",
        "rules": [{"type": "keyword", "parameters": {"keywords": ["${companion_name}"]}}],
    },
}


def runtime_context(**overrides: object) -> RuntimeContext:
    """A runtime context over a real bus, a real registry and real supervision."""

    bus = overrides.pop("bus", None) or EventBus()
    authorization = overrides.pop("authorization", None) or AuthorizationPolicy()
    fields: dict = {
        "bus": bus,
        "actions": ActionRegistry(authorization=authorization),
        "supervision": Supervision(bus),
        "tasks": SupervisedTasks(),
        "triggers": TriggerEngine(
            TriggerRegistry(companion_name="companion"),
            dedup_max_entries=8,
            dedup_ttl_seconds=60.0,
        ),
    }
    fields.update(overrides)
    return RuntimeContext(**fields)


def v2_settings(label: str = "v2") -> dict:
    return {"label": label, "calls": [], "closes": []}


@pytest.mark.asyncio
async def test_v2_module_receives_the_runtime_context_and_registers_its_actions(
    tmp_path: Path,
) -> None:
    """AC26: a manifest_version 2 module is handed the context and declares through it."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", roles=["input"], actions=[CHAT_WRITE_ACTION]),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    activations = await loader.activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": settings}}
    )

    # The first activation argument is the module's scoped context, not the bus.
    assert len(settings["calls"]) == 1
    received_context, received_settings, received_catalog = settings["calls"][0]
    assert isinstance(received_context, ModuleContext)
    assert received_context.module == "chat"
    assert received_settings is settings
    assert received_catalog is loader.catalog
    assert settings["runtime_api"] == RUNTIME_API
    assert settings["bus"] is context.bus

    # The declared action is discovered, and the provider bound through the
    # scoped facade is bound under this module's name.
    assert set(context.actions.discovered()) == {"chat.write"}
    assert [binding.module for binding in context.actions.bindings()] == ["chat"]
    assert set(context.actions.registered_ready()) == {"chat.write"}

    # The declared phase roles travel on the activation, never a name lookup.
    activation = activations[0]
    assert activation.roles == frozenset({"input"})
    assert activation.manifest_version == 2
    assert module_roles(activation) == frozenset({"input"})


@pytest.mark.asyncio
async def test_module_without_manifest_version_keeps_the_v1_route(
    tmp_path: Path,
) -> None:
    """AC26: no manifest_version means activate(bus, settings, catalog) and close()."""

    make_module(tmp_path, "legacy")
    context = runtime_context()
    settings = {"label": "legacy", "calls": [], "closes": []}
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    activations = await loader.activate_enabled(
        {"enabled_modules": ["legacy"], "modules": {"legacy": settings}}
    )

    # Even with a context available, a v1 manifest is handed the bare bus.
    received_bus, received_settings, received_catalog = settings["calls"][0]
    assert received_bus is context.bus
    assert not isinstance(received_bus, ModuleContext)
    assert received_settings is settings
    assert received_catalog is loader.catalog
    assert activations[0].manifest_version == 1
    assert activations[0].roles == frozenset()

    # The v1 route is the one that still owns close(): the transport a v1
    # module opened is released by that call and by nothing else.
    await activations[0].close()
    assert settings["closes"] == ["legacy"]


@pytest.mark.asyncio
async def test_activation_adds_no_attribute_to_the_bus(tmp_path: Path) -> None:
    """AC26: comparing the bus attribute set before and after shows 0 additions."""

    make_module(tmp_path, "legacy")
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", actions=[CHAT_WRITE_ACTION]),
        source=V2_MODULE_SOURCE,
    )
    bus = EventBus()
    context = runtime_context(bus=bus)
    before = set(dir(bus)) | set(vars(bus))

    await ModuleLoader(bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["legacy", "chat"],
            "modules": {
                "legacy": {"label": "legacy", "calls": [], "closes": []},
                "chat": v2_settings("chat"),
            },
        }
    )

    after = set(dir(bus)) | set(vars(bus))
    assert after - before == set()
    assert after == before


@pytest.mark.asyncio
async def test_disabled_module_manifest_is_validated_but_its_secrets_are_not(
    tmp_path: Path, monkeypatch
) -> None:
    """AC25: an invalid disabled manifest stops startup; an unresolved disabled secret does not."""

    broken_root = tmp_path / "broken"
    broken_root.mkdir()
    make_module(broken_root, "enabled")
    make_module(
        broken_root,
        "disabled",
        manifest={"name": "disabled", "produces": [], "consumes": []},
    )
    config = {
        "enabled_modules": ["enabled"],
        "modules": {
            "enabled": {"label": "enabled", "calls": [], "closes": []},
            "disabled": {"api_key": "${COMPANION_ABSENT}"},
        },
    }

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), broken_root).activate_enabled(config)
    assert "'disabled'" in str(caught.value)
    assert "middleware" in str(caught.value)

    # Same disabled module, this time with a valid manifest: its unresolvable
    # secret is never looked up, so the application starts.
    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    make_module(valid_root, "enabled")
    make_module(valid_root, "disabled")
    settings = {"label": "enabled", "calls": [], "closes": []}
    loader = ModuleLoader(
        object(),
        valid_root,
        environ={},
    )

    activations = await loader.activate_enabled(
        {
            "enabled_modules": ["enabled"],
            "modules": {
                "enabled": settings,
                "disabled": {"api_key": "${COMPANION_ABSENT}"},
            },
        }
    )

    assert [activation.name for activation in activations] == ["enabled"]
    assert loader.resolved_secrets == 0
    assert set(loader.discovered) == {"disabled", "enabled"}


@pytest.mark.asyncio
async def test_enabled_module_secret_is_resolved_and_an_absent_one_stops_startup(
    tmp_path: Path,
) -> None:
    """The enabled half of AC25: its references are resolved, and counted."""

    make_module(tmp_path, "enabled")
    settings = {"label": "enabled", "calls": [], "closes": [], "api_key": "${TOKEN}"}
    loader = ModuleLoader(object(), tmp_path, environ={"TOKEN": "s3cret"})

    await loader.activate_enabled(
        {"enabled_modules": ["enabled"], "modules": {"enabled": settings}}
    )

    assert loader.resolved_secrets == 1
    resolved = settings["calls"][0][1]
    assert resolved is not settings
    assert resolved["api_key"] == "s3cret"

    other = ModuleLoader(object(), tmp_path, environ={})
    with pytest.raises(ModuleLoadError) as caught:
        await other.activate_enabled(
            {
                "enabled_modules": ["enabled"],
                "modules": {"enabled": {"calls": [], "api_key": "${TOKEN}"}},
            }
        )
    assert "'enabled'" in str(caught.value)
    assert "api_key" in str(caught.value)
    assert "s3cret" not in str(caught.value)
    assert other.resolved_secrets == 0


@pytest.mark.asyncio
async def test_declared_action_is_discovered_but_never_authorized_by_declaring(
    tmp_path: Path,
) -> None:
    """R7/R5: declaring an action, a capability or a produced event grants nothing."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            actions=[CHAT_WRITE_ACTION, {**CHAT_WRITE_ACTION, "name": "chat.read", "nature": "read"}],
        ),
        source=V2_MODULE_SOURCE,
    )
    authorization = AuthorizationPolicy()
    context = runtime_context(authorization=authorization)

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    registry = context.actions
    assert set(registry.discovered()) == {"chat.write", "chat.read"}
    # A bound, ready provider with no applicable rule is still not authorized,
    # and the read nature is refused exactly like the write.
    assert set(registry.registered_ready()) == {"chat.write"}
    assert dict(registry.authorized(principal="viewer")) == {}

    authorization.grant(
        AuthorizationRule(
            rule_id="chat-write",
            action_name="chat.write",
            granted_permissions=("chat.write",),
        )
    )
    assert set(registry.authorized(principal="viewer")) == {"chat.write"}


@pytest.mark.asyncio
async def test_declared_triggers_reach_the_trigger_registry(tmp_path: Path) -> None:
    """R1/R7: a declared TriggerSpec is registered under the module's input name."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat", triggers=KEYWORD_TRIGGERS, actions=[CHAT_WRITE_ACTION]
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    registry = context.triggers.registry
    assert registry.inputs() == ("chat",)
    spec = registry.spec("chat")
    assert spec.declares("keyword")
    assert spec.combinations == ("all_of", "any_of")
    # An undeclared type is refused against that very declaration.
    with pytest.raises(ContractError):
        registry.validate_policy(
            "chat",
            TriggerPolicy(
                rules=(TriggerRule(type="probability", parameters={"probability": 1}),)
            ),
        )


@pytest.mark.asyncio
async def test_companion_name_token_resolves_from_the_declaring_module_settings(
    tmp_path: Path,
) -> None:
    """R1: the name behind ``${companion_name}`` is the input's own setting.

    The registry the application builds knows no name — the core's
    configuration carries no module vocabulary — so the loader hands the name
    configured in the declaring module's settings to the registry for that
    input, and a module configured without one is refused at load, before
    any activation, naming the input and the field.
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat", triggers=KEYWORD_TRIGGERS, actions=[CHAT_WRITE_ACTION]
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context(
        triggers=TriggerEngine(
            TriggerRegistry(), dedup_max_entries=8, dedup_ttl_seconds=60.0
        )
    )
    settings = {**v2_settings("chat"), "companion_name": "Ada"}

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": settings}}
    )

    registry = context.triggers.registry
    assert registry.companion_name is None
    assert registry.companion_name_for("chat") == "Ada"
    assert settings["calls"]

    unnamed = runtime_context(
        triggers=TriggerEngine(
            TriggerRegistry(), dedup_max_entries=8, dedup_ttl_seconds=60.0
        )
    )
    refused = v2_settings("chat")
    with pytest.raises(ModuleLoadError) as excinfo:
        await ModuleLoader(unnamed.bus, tmp_path, context=unnamed).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": refused}}
        )
    assert "module 'chat': field 'triggers'" in str(excinfo.value)
    assert "no companion name is configured" in str(excinfo.value)
    assert refused["calls"] == []
    assert unnamed.triggers.registry.inputs() == ()


@pytest.mark.asyncio
async def test_settings_schema_and_declared_hook_run_before_any_activation(
    tmp_path: Path,
) -> None:
    """R7: every enabled module validates its own settings before any is activated."""

    schema = {
        "type": "object",
        "properties": {"channel": {"type": "string", "enum": ["general", "vip"]}},
        "required": ["channel"],
    }
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_schema=schema,
            settings_validator="validate_settings",
            actions=[CHAT_WRITE_ACTION],
        ),
        source=V2_MODULE_SOURCE,
    )
    make_module(
        tmp_path,
        "other",
        manifest=v2_manifest("other"),
        source=V2_MODULE_SOURCE.replace(
            '    context.actions.bind(\n'
            '        "chat.write",\n'
            '        Provider(),\n'
            '        destinations=Destination("twitch", "*", "chat"),\n'
            '    )\n',
            "",
        ),
    )
    context = runtime_context()
    chat = {**v2_settings("chat"), "channel": "general"}
    other = v2_settings("other")

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["chat", "other"],
            "modules": {"chat": chat, "other": other},
        }
    )
    assert chat["validated"] is True

    # A settings mapping the declared schema refuses stops startup with 0
    # activations, naming the module and the field and quoting no value.
    context = runtime_context()
    chat = {**v2_settings("chat"), "channel": "s3cret"}
    other = v2_settings("other")
    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {
                "enabled_modules": ["chat", "other"],
                "modules": {"chat": chat, "other": other},
            }
        )
    assert "'chat'" in str(caught.value)
    assert "channel" in str(caught.value)
    # The rejected value is never quoted back: it may be the credential (AC24).
    assert "s3cret" not in str(caught.value)
    assert chat["calls"] == []
    assert other["calls"] == []


CREDENTIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "token": {"type": "string"},
        "backend": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "port": {"type": "integer"}},
        },
    },
}

PLAIN_V2_SOURCE = V2_MODULE_SOURCE.replace(
    '    context.actions.bind(\n'
    '        "chat.write",\n'
    '        Provider(),\n'
    '        destinations=Destination("twitch", "*", "chat"),\n'
    '    )\n',
    "",
)


@pytest.mark.asyncio
async def test_declared_credentials_reach_supervision_before_any_activation(
    tmp_path: Path,
) -> None:
    """R8/AC29 (P24 F7): a module's declared credentials are redacted whether
    they were configured literally or resolved from an environment reference,
    and they are known to supervision before the first module is activated,
    so the first trace any module publishes is already redacted of them."""

    make_module(
        tmp_path,
        "first",
        manifest=v2_manifest(
            "first",
            settings_schema=CREDENTIAL_SCHEMA,
            credentials=["token", "backend.key"],
        ),
        source=PLAIN_V2_SOURCE.replace(
            '    settings["bus"] = context.bus\n',
            '    settings["bus"] = context.bus\n'
            '    await context.supervision.emit(\n'
            '        "probe.activation",\n'
            '        {"reason": "opened with " + settings["token"] + " and "\n'
            '         + settings["backend"]["key"]},\n'
            '    )\n',
        ),
    )
    make_module(
        tmp_path,
        "second",
        manifest=v2_manifest(
            "second", settings_schema=CREDENTIAL_SCHEMA, credentials=["token"]
        ),
        source=PLAIN_V2_SOURCE,
    )
    context = runtime_context()
    delivered: list[dict] = []
    context.bus.subscribe("probe.**", lambda event: delivered.append(event))
    literal = "literal-credential-9f1c"
    nested = "nested-credential-4b7e"
    resolved = "resolved-credential-c03d"
    first = {**v2_settings("first"), "token": literal, "backend": {"key": nested, "port": 1}}
    second = {**v2_settings("second"), "token": "${SECOND_TOKEN}"}
    loader = ModuleLoader(
        context.bus, tmp_path, context=context, environ={"SECOND_TOKEN": resolved}
    )

    await loader.activate_enabled(
        {
            "enabled_modules": ["first", "second"],
            "modules": {"first": first, "second": second},
        }
    )

    assert loader.redacted_credentials == 3
    # The first module's own activation trace, published before the second
    # module was even activated, carries neither its literal credentials nor
    # the second module's resolved one.
    (event,) = delivered
    assert event["payload"]["reason"].startswith("opened with ")
    assert literal not in event["payload"]["reason"]
    assert nested not in event["payload"]["reason"]
    await context.supervision.emit("probe.later", {"reason": "with " + resolved})
    assert resolved not in delivered[-1]["payload"]["reason"]
    assert "with " in delivered[-1]["payload"]["reason"]


@pytest.mark.asyncio
async def test_a_declared_credential_the_settings_omit_contributes_nothing(
    tmp_path: Path,
) -> None:
    """An optional credential left unset is not an error and redacts nothing."""

    make_module(
        tmp_path,
        "quiet",
        manifest=v2_manifest(
            "quiet", settings_schema=CREDENTIAL_SCHEMA, credentials=["token", "backend.key"]
        ),
        source=PLAIN_V2_SOURCE,
    )
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    await loader.activate_enabled(
        {"enabled_modules": ["quiet"], "modules": {"quiet": v2_settings("quiet")}}
    )

    assert loader.redacted_credentials == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("declared", "schema", "field"),
    [
        pytest.param("token", CREDENTIAL_SCHEMA, "credentials", id="not-a-list"),
        pytest.param([1], CREDENTIAL_SCHEMA, "credentials[0]", id="not-a-string"),
        pytest.param([""], CREDENTIAL_SCHEMA, "credentials[0]", id="empty"),
        pytest.param(["backend..key"], CREDENTIAL_SCHEMA, "credentials[0]", id="malformed"),
        pytest.param(["token", "token"], CREDENTIAL_SCHEMA, "credentials[1]", id="duplicate"),
        pytest.param(["tokne"], CREDENTIAL_SCHEMA, "credentials[0]", id="misspelt"),
        pytest.param(["backend.port"], CREDENTIAL_SCHEMA, "credentials[0]", id="not-a-string-property"),
        pytest.param(["backend"], CREDENTIAL_SCHEMA, "credentials[0]", id="object-property"),
        pytest.param(["token"], None, "credentials", id="no-schema"),
    ],
)
async def test_a_credential_declaration_the_schema_does_not_back_is_refused(
    tmp_path: Path, declared: object, schema: dict | None, field: str
) -> None:
    """R8 (P24 F7): a misspelt or unbacked credential declaration is refused at
    discovery, naming module and entry, rather than silently redacting nothing."""

    manifest = v2_manifest("broken", settings_schema=schema)
    manifest["credentials"] = declared
    make_module(tmp_path, "broken", manifest=manifest, source=PLAIN_V2_SOURCE)
    context = runtime_context()
    settings = {**v2_settings("broken"), "token": "t0ken"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["broken"], "modules": {"broken": settings}}
        )

    assert str(caught.value).startswith(f"module 'broken': field '{field}': ")
    assert "t0ken" not in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_a_v1_manifest_may_not_declare_credentials(tmp_path: Path) -> None:
    """``credentials`` is a v2 declaration: a v1 module has no schema to back it."""

    make_module(
        tmp_path,
        "legacy",
        manifest={
            "name": "legacy",
            "produces": [],
            "consumes": [],
            "middleware": False,
            "credentials": ["api_key"],
        },
    )
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            _base_config("legacy")
        )

    assert str(caught.value).startswith("module 'legacy': field 'credentials': ")


@pytest.mark.asyncio
async def test_a_supervision_that_cannot_redact_refuses_a_declared_credential(
    tmp_path: Path,
) -> None:
    """A runtime whose supervision cannot be told the credentials would publish
    them: the module is refused before activation rather than traced in clear."""

    make_module(
        tmp_path,
        "keyed",
        manifest=v2_manifest("keyed", settings_schema=CREDENTIAL_SCHEMA, credentials=["token"]),
        source=PLAIN_V2_SOURCE,
    )

    class Sink:
        def emit(self, *args, **kwargs):
            raise AssertionError("never published")

        record_and_emit = emit

        def snapshot(self):
            return {}

    context = runtime_context(supervision=Sink())
    settings = {**v2_settings("keyed"), "token": "t0ken"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["keyed"], "modules": {"keyed": settings}}
        )

    assert "field 'supervision'" in str(caught.value)
    assert "t0ken" not in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_declared_settings_hook_the_module_does_not_define_stops_startup(
    tmp_path: Path,
) -> None:
    """R7: an unresolvable hook is a startup failure naming the module, never 'valid'."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", settings_validator="check_settings"),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "settings_validator" in str(caught.value)
    assert settings["calls"] == []


LEAKY_VALIDATOR_SOURCE = V2_MODULE_SOURCE.replace(
    'def validate_settings(settings):\n    settings["validated"] = True\n',
    "def validate_settings(settings):\n"
    "    from core.loader import ModuleLoadError\n"
    "\n"
    "    raise ModuleLoadError(settings['api_key'])\n",
)


@pytest.mark.asyncio
async def test_settings_hook_cannot_echo_the_rejected_credential(
    tmp_path: Path,
) -> None:
    """R7/AC24: what the hook raises never becomes the diagnostic, whatever its type."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", settings_validator="validate_settings"),
        source=LEAKY_VALIDATOR_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "api_key": "s3cret"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    # A hook raising the loader's own error type is not a pass-through: the
    # module was handed the settings, so its message may be the credential.
    assert "s3cret" not in str(caught.value)
    assert "'chat'" in str(caught.value)
    assert "validate_settings" in str(caught.value)
    assert settings["calls"] == []


#: The settings shape the returning hooks below report against: a hook may
#: only name a field its module declares or was handed (R7).
CHAT_SETTINGS_SCHEMA = {
    "type": "object",
    "properties": {"api_key": {"type": "string"}, "channel": {"type": "string"}},
}


RETURNING_VALIDATOR_SOURCE = V2_MODULE_SOURCE.replace(
    'def validate_settings(settings):\n    settings["validated"] = True\n',
    "def validate_settings(settings):\n"
    "    settings['validated'] = True\n"
    "    return [\n"
    "        f\"module 'chat': field 'api_key': {settings['api_key']}\",\n"
    "        \"module 'chat': field 'channel': must be a non-empty string\",\n"
    "    ]\n",
)


@pytest.mark.asyncio
async def test_settings_hook_refuses_by_returning_diagnostics(
    tmp_path: Path,
) -> None:
    """R7/AC24: a hook returning diagnostics refuses like one that raises.

    The module's diagnostics name module and field, and they are passed
    through — the field is what the module knows and the core does not —
    with every configured value redacted from the reason, so a careless
    hook that echoes the value it was handed still leaks nothing. The
    loader used to borrow their count only; the field-naming half of AC24
    is what this asserts now (P24).
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_validator="validate_settings",
            settings_schema=CHAT_SETTINGS_SCHEMA,
        ),
        source=RETURNING_VALIDATOR_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "api_key": "s3cret"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert settings["validated"] is True
    assert caught.value.diagnostics == (
        "module 'chat': field 'api_key': <redacted>",
        "module 'chat': field 'channel': must be a non-empty string",
    )
    assert str(caught.value) == "\n".join(caught.value.diagnostics)
    assert "s3cret" not in str(caught.value)
    assert settings["calls"] == []


UNTRUSTWORTHY_VALIDATOR_SOURCE = V2_MODULE_SOURCE.replace(
    'def validate_settings(settings):\n    settings["validated"] = True\n',
    "def validate_settings(settings):\n"
    "    return [\n"
    "        f\"module 'other': field '{settings['api_key']}': is wrong\",\n"
    "        f\"the key {settings['api_key']} is wrong\",\n"
    "        42,\n"
    "        \"module 'chat': field 'not a path!': is wrong\",\n"
    "        \"module 'chat': field 'missing.nested': is required\",\n"
    "        \"module 'chat': field 'channel': \" + \"x\" * 1000,\n"
    "    ]\n",
)


@pytest.mark.asyncio
async def test_hook_diagnostics_are_held_to_this_module_a_declared_field_and_no_value(
    tmp_path: Path,
) -> None:
    """R7/AC24: what a hook returns is module-authored text, not the report.

    A diagnostic naming another module is re-attributed to this one; a field
    slot carrying the credential, a field that is not shaped like a setting
    path, an entry with no field at all and an entry that is not text all
    fall back to the declared hook's name; a field the settings lack is
    still named; every reason is redacted of every configured value and
    bounded in length.
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_validator="validate_settings",
            settings_schema=CHAT_SETTINGS_SCHEMA,
        ),
        source=UNTRUSTWORTHY_VALIDATOR_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "api_key": "s3cret"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    diagnostics = caught.value.diagnostics
    assert diagnostics[:5] == (
        "module 'chat': field 'validate_settings': is wrong",
        "module 'chat': field 'validate_settings': the key <redacted> is wrong",
        "module 'chat': field 'validate_settings': settings were refused by the module",
        "module 'chat': field 'validate_settings': is wrong",
        "module 'chat': field 'missing.nested': is required",
    )
    assert diagnostics[5].startswith("module 'chat': field 'channel': xxx")
    assert len(diagnostics[5]) < 400
    assert "s3cret" not in str(caught.value)
    assert "'other'" not in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_every_enabled_module_is_validated_and_every_refusal_is_reported(
    tmp_path: Path,
) -> None:
    """AC24: 2 enabled modules with invalid settings are both reported.

    The first module's refusal does not stop the second from being
    validated: one error carries both diagnostics, each naming its own
    module and field, 0 activations happened and 0 credential values are
    echoed.
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_validator="validate_settings",
            settings_schema=CHAT_SETTINGS_SCHEMA,
        ),
        source=RETURNING_VALIDATOR_SOURCE,
    )
    make_module(
        tmp_path,
        "other",
        manifest=v2_manifest(
            "other",
            settings_schema={
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    chat = {**v2_settings("chat"), "api_key": "s3cret"}
    other = v2_settings("other")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat", "other"], "modules": {"chat": chat, "other": other}}
        )

    assert caught.value.diagnostics == (
        "module 'chat': field 'api_key': <redacted>",
        "module 'chat': field 'channel': must be a non-empty string",
        "module 'other': field 'settings.token': is required and missing",
    )
    assert "s3cret" not in str(caught.value)
    assert chat["calls"] == []
    assert other["calls"] == []


@pytest.mark.asyncio
async def test_schema_diagnostic_drops_a_value_that_contains_the_separator(
    tmp_path: Path,
) -> None:
    """R7/AC24: the observed value is cut whole, not down to its first ', got '."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            settings_schema={
                "type": "object",
                "properties": {"channel": {"type": "string", "enum": ["general"]}},
                "required": ["channel"],
            },
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = {**v2_settings("chat"), "channel": "s3cret, got tail"}

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "channel" in str(caught.value)
    assert "s3cret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", ["read", False, 0, {"chat.write": True}])
async def test_permission_declaration_that_is_not_a_list_is_a_manifest_error(
    tmp_path: Path, declared: object
) -> None:
    """R5/R7: a malformed permission requirement is refused, never normalised away.

    ``"read"`` must not become four single-letter permissions and ``False``
    must not become an empty requirement: both would change what a rule has to
    grant for the action to be permitted.
    """

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            actions=[{**CHAT_WRITE_ACTION, "required_permissions": declared}],
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "required_permissions" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_unknown_manifest_version_is_refused_rather_than_guessed(
    tmp_path: Path,
) -> None:
    """R7/R4: neither arm of the fork may be guessed for an unknown contract."""

    make_module(
        tmp_path,
        "future",
        manifest=v2_manifest("future", manifest_version=3),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["future"], "modules": {"future": v2_settings()}}
        )

    assert "'future'" in str(caught.value)
    assert "manifest_version" in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("manifest", "field"),
    [
        (v2_manifest("broken", runtime_api=RUNTIME_API + 1), "runtime_api"),
        (
            {
                key: value
                for key, value in v2_manifest("broken").items()
                if key != "runtime_api"
            },
            "runtime_api",
        ),
        (
            v2_manifest("broken", actions=[{**CHAT_WRITE_ACTION, "nature": "sideways"}]),
            "actions[0]",
        ),
        (
            v2_manifest("broken", settings_schema={"type": "object", "unknown": 1}),
            "settings_schema",
        ),
        (
            v2_manifest(
                "broken",
                triggers={**KEYWORD_TRIGGERS, "combinations": ["most_of"]},
            ),
            "triggers",
        ),
        (v2_manifest("broken", roles=["producer"]), "lifecycle.roles"),
    ],
)
async def test_invalid_v2_declaration_stops_startup(
    tmp_path: Path, manifest: dict, field: str
) -> None:
    """Every v2 declaration is turned into its contract at discovery, or refused."""

    make_module(tmp_path, "broken", manifest=manifest, source=V2_MODULE_SOURCE)
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["broken"], "modules": {"broken": v2_settings()}}
        )

    assert "'broken'" in str(caught.value)
    assert field in str(caught.value)


@pytest.mark.asyncio
async def test_v1_manifest_may_not_declare_a_v2_key(tmp_path: Path) -> None:
    """A manifest belongs to exactly one contract, so the fork is never ambiguous."""

    manifest = {
        "name": "mixed",
        "produces": [],
        "consumes": [],
        "middleware": False,
        "actions": [CHAT_WRITE_ACTION],
    }
    make_module(tmp_path, "mixed", manifest=manifest)
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            _base_config("mixed")
        )

    assert "'mixed'" in str(caught.value)
    assert "actions" in str(caught.value)


@pytest.mark.asyncio
async def test_v1_producer_that_cannot_honour_the_barrier_is_refused(
    tmp_path: Path,
) -> None:
    """R4: a v1 module has no start_inputs phase, so it may not declare the input role."""

    manifest = {
        "name": "feed",
        "produces": ["channel.chat.message"],
        "consumes": [],
        "middleware": False,
        "lifecycle": {"roles": ["input"]},
    }
    make_module(tmp_path, "feed", manifest=manifest)
    settings = {"label": "feed", "calls": [], "closes": []}
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["feed"], "modules": {"feed": settings}}
        )

    assert "module 'feed'" in str(caught.value)
    assert "readiness barrier" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_v2_input_module_must_open_its_source_in_start_inputs(
    tmp_path: Path,
) -> None:
    """R4: the same barrier obligation, checked against the v2 handle."""

    source = V2_MODULE_SOURCE.replace("    async def start_inputs(self):\n        self.settings[\"started\"] = True\n\n", "")
    make_module(
        tmp_path,
        "feed",
        manifest=v2_manifest("feed", roles=["input"], actions=[CHAT_WRITE_ACTION]),
        source=source,
    )
    context = runtime_context()

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
            {"enabled_modules": ["feed"], "modules": {"feed": v2_settings("feed")}}
        )

    assert "'feed'" in str(caught.value)
    assert "start_inputs" in str(caught.value)


@pytest.mark.asyncio
async def test_enabled_v2_module_without_a_runtime_context_is_refused(
    tmp_path: Path,
) -> None:
    """Without a context there is nothing to hand a v2 module; refuse, never fall back."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat"),
        source=V2_MODULE_SOURCE,
    )
    settings = v2_settings("chat")

    with pytest.raises(ModuleLoadError) as caught:
        await ModuleLoader(object(), tmp_path).activate_enabled(
            {"enabled_modules": ["chat"], "modules": {"chat": settings}}
        )

    assert "'chat'" in str(caught.value)
    assert "manifest_version" in str(caught.value)
    assert settings["calls"] == []


@pytest.mark.asyncio
async def test_v2_handle_close_is_optional_and_idempotent(tmp_path: Path) -> None:
    """Closing is a declared phase: a v2 handle with no close hook is not a defect."""

    source = V2_MODULE_SOURCE.replace(
        "    async def close(self):\n"
        "        self.settings[\"closes\"].append(self.settings[\"label\"])\n",
        "",
    )
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest("chat", actions=[CHAT_WRITE_ACTION]),
        source=source,
    )
    context = runtime_context()

    activations = await ModuleLoader(
        context.bus, tmp_path, context=context
    ).activate_enabled(
        {"enabled_modules": ["chat"], "modules": {"chat": v2_settings("chat")}}
    )

    await activations[0].close()
    await activations[0].close()
    assert activations[0].closed is True


@pytest.mark.asyncio
async def test_declarations_are_recorded_before_the_first_activation(
    tmp_path: Path,
) -> None:
    """A module may bind at activation to an action another module declared."""

    provider_source = """
from core.contracts import Destination


class Provider:
    name = "late"

    async def invoke(self, invocation):
        raise AssertionError("no call is made during activation")


class Handle:
    async def close(self):
        return None


async def activate(context, settings, catalog):
    settings["discovered"] = sorted(context.actions.discovered())
    context.actions.bind(
        "chat.write", Provider(), destinations=Destination("twitch", "*", "chat")
    )
    return Handle()
"""
    make_module(
        tmp_path,
        "declarer",
        manifest=v2_manifest("declarer", actions=[CHAT_WRITE_ACTION]),
        source="""
class Handle:
    async def close(self):
        return None


async def activate(context, settings, catalog):
    return Handle()
""",
    )
    make_module(
        tmp_path,
        "binder",
        manifest=v2_manifest("binder"),
        source=provider_source,
    )
    context = runtime_context()
    binder: dict = {}

    await ModuleLoader(context.bus, tmp_path, context=context).activate_enabled(
        {
            "enabled_modules": ["binder", "declarer"],
            "modules": {"binder": binder, "declarer": {}},
        }
    )

    # "binder" is activated first and still sees the declaration of "declarer".
    assert binder["discovered"] == ["chat.write"]
    assert [binding.module for binding in context.actions.bindings()] == ["binder"]


# --------------------------------------------------------------------------- #
# The trigger policies configuration selects (R1)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_configured_channel_trigger_policies_replace_the_module_default(
    tmp_path: Path,
) -> None:
    """R1, AC1: a channel policy replaces the default; others keep it."""

    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat", triggers=KEYWORD_TRIGGERS, actions=[CHAT_WRITE_ACTION]
        ),
        source=V2_MODULE_SOURCE,
    )
    context = runtime_context()
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    await loader.activate_enabled(
        {
            "enabled_modules": ["chat"],
            "modules": {"chat": v2_settings()},
            "triggers": {
                "chat": {
                    "channels": {
                        "42": {
                            "combination": "all_of",
                            "rules": [
                                {
                                    "type": "keyword",
                                    "parameters": {"keywords": ["!ask"]},
                                }
                            ],
                        }
                    }
                }
            },
        }
    )

    registry = context.triggers.registry
    selected = registry.resolve("chat", "42")
    assert selected is not None
    assert list(selected.rules[0].parameters["keywords"]) == ["!ask"]
    kept = registry.resolve("chat", "43")
    assert kept is not None
    assert "${companion_name}" in kept.rules[0].parameters["keywords"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "context_without_engine", "diagnostic"),
    [
        (
            {
                "chat": {
                    "channels": {
                        "42": {
                            "rules": [
                                {
                                    "type": "probability",
                                    "parameters": {"probability": 0.5},
                                }
                            ],
                        }
                    }
                }
            },
            False,
            "is not declared by the module; declared: keyword",
        ),
        (
            {
                "ghost": {
                    "channels": {
                        "42": {
                            "rules": [
                                {
                                    "type": "keyword",
                                    "parameters": {"keywords": ["!ask"]},
                                }
                            ],
                        }
                    }
                }
            },
            False,
            "names an input no module declares; declared inputs: chat",
        ),
        (
            {
                "chat": {
                    "channels": {
                        "42": {
                            "rules": [
                                {
                                    "type": "keyword",
                                    "parameters": {"keywords": ["!ask"]},
                                }
                            ],
                        }
                    }
                }
            },
            True,
            "the runtime context carries no trigger engine",
        ),
    ],
)
async def test_an_invalid_configured_trigger_policy_stops_startup(
    tmp_path: Path,
    configured: dict,
    context_without_engine: bool,
    diagnostic: str,
) -> None:
    """R1, R7: a policy the input cannot honour stops startup, 0 activated."""

    # Without an engine the module must not declare triggers of its own, or
    # the refusal would name the declaration instead of the configuration.
    make_module(
        tmp_path,
        "chat",
        manifest=v2_manifest(
            "chat",
            triggers=None if context_without_engine else KEYWORD_TRIGGERS,
            actions=[CHAT_WRITE_ACTION],
        ),
        source=V2_MODULE_SOURCE,
    )
    fields: dict = (
        {"triggers": None}
        if context_without_engine
        else {
            "triggers": TriggerEngine(
                TriggerRegistry(companion_name="companion"),
                dedup_max_entries=8,
                dedup_ttl_seconds=60.0,
            )
        }
    )
    context = runtime_context(**fields)
    loader = ModuleLoader(context.bus, tmp_path, context=context)

    with pytest.raises(ModuleLoadError, match=re.escape(diagnostic)):
        await loader.activate_enabled(
            {
                "enabled_modules": ["chat"],
                "modules": {"chat": v2_settings()},
                "triggers": configured,
            }
        )
    assert loader.activations == []
