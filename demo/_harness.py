"""Reach the checkout's own scenario harness from the demo entry point.

The demo does not re-implement a runtime. It drives the scripted end-to-end
scenario of ``tests/test_phase2_scenario.py`` — the same one whose assertions
the suite enforces — and narrates the bus events that scenario publishes. That
keeps the demo honest, since what it shows is what the tests check, and cheap,
since there is one harness to maintain rather than two.

Two properties of that harness shape this module:

* ``tests/conftest.py`` runs a self-check at import time that starts an event
  loop of its own, so :func:`load_scenario` must be called *before*
  ``asyncio.run``. Called from inside a running loop it would raise the
  harness's own ``RuntimeError``; the guard below says what to do instead.
* the harness lives in ``tests/``, which a wheel does not ship, so the demo
  lives beside ``core/`` in the checkout rather than in the distribution, and a
  run outside a checkout is refused with :class:`DemoUnavailable` — a sentence,
  not a traceback.

The demo sits outside ``core/`` on purpose: it names platforms, and the core is
platform-neutral by construction — a rule the suite enforces by grep
(``tests/test_agentic_loop.py`` AC31, ``tests/test_youtube.py``'s broad scan).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

SCENARIO_MODULE = "test_phase2_scenario"
"""The harness module the demo drives, relative to the checkout's ``tests/``."""


class DemoUnavailable(RuntimeError):
    """The demo cannot run here: no harness next to the package."""


def repository_root() -> Path:
    """The checkout the demo belongs to."""

    return Path(__file__).resolve().parents[1]


def load_scenario(root: Path | str | None = None) -> Any:
    """Import and return the scenario harness rooted at *root*.

    *root* defaults to :func:`repository_root`. The checkout root and its
    ``tests/`` directory are put on ``sys.path`` — the harness imports its
    siblings (``conftest``, ``test_integration``) as top-level modules, as
    pytest arranges for it — and the scenario module is imported and returned.
    """

    base = Path(root) if root is not None else repository_root()
    tests = base / "tests"
    if not (tests / f"{SCENARIO_MODULE}.py").is_file():
        raise DemoUnavailable(
            f"no scenario harness under {tests}: the demo drives "
            "tests/test_phase2_scenario.py, so it runs from a checkout "
            "(git clone) — start it from the repository root"
        )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise DemoUnavailable(
            "the scenario harness must be loaded before the event loop starts"
        )
    for entry in (str(base), str(tests)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import importlib

    return importlib.import_module(SCENARIO_MODULE)
