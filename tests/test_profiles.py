"""The shipped profiles as a user meets them (R7, R8; AC40, AC41, AC43).

Three checks the example suite (``tests/test_examples.py``) does not make:

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
  ``modules_directory: builtin`` discovers exactly the 8 shipped manifests
  and the console script answers ``--help`` (AC43). That test skips only
  when ``pip`` is unavailable, naming that reason; a build backend that is
  not importable, an install error or a wrong manifest count is a failure.

No positive-duration sleep: the checks run on fakes, the install test on
subprocesses that terminate on their own.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

import core.main as application
from core.contracts import TRACE_MODULE_DEGRADED
from conftest import events_of
from modules.capture import SCREEN_CAPTURE_PROVIDER
from modules.proxy import PROVIDER_NAME as PROXY_PROVIDER
from test_examples import (
    MODULE_NAMES,
    PC_PROFILE,
    PROFILES,
    ROOT,
    SERVER_PROFILE,
    _activate_example,
    _close_all,
    _prepare_all,
    _read_yaml,
    _sample_environ,
)


SCREEN_CAPTURE = "screen.capture"
#: The credential AC40 unsets, and the diagnostic the check must give.
UNSET_CREDENTIAL = "TWITCH_ACCESS_TOKEN"
UNSET_DIAGNOSTIC = "modules.twitch.access_token: environment reference is unresolved"
#: The names R8 requires a clean environment to discover, in its order.
SHIPPED_MANIFESTS = (
    "twitch",
    "brain",
    "audit",
    "chat_context",
    "users",
    "capture",
    "proxy",
    "agent_link",
)
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


async def test_server_profile_binds_screen_capture_to_the_proxy_provider() -> None:
    """AC41 (R7, decision 7): with ``proxy`` enabled and ``actions:
    [screen.capture]``, no enabled manifest declares ``screen.capture`` —
    the proxy declares it from the catalog at preparation and binds itself,
    the one provider, over ``*/*/capture``; the action waits for a pairing
    to become ready. The brain module took no part in the choice."""

    config = _read_yaml(SERVER_PROFILE)
    assert "capture" not in config["enabled_modules"]
    assert config["modules"]["proxy"]["actions"] == [SCREEN_CAPTURE]

    runtime, activations, diagnostics = await _bindings_after_preparation(SERVER_PROFILE)
    try:
        registry = runtime.context.actions
        (binding,) = registry.bindings(SCREEN_CAPTURE)
        assert binding.provider_name == PROXY_PROVIDER
        assert binding.module == "proxy"
        assert binding.destination.scope == "capture"
        assert binding.destination.platform == "*" and binding.destination.channel_id == "*"
        assert SCREEN_CAPTURE in registry.discovered()
        assert SCREEN_CAPTURE not in registry.registered_ready()
        assert diagnostics == []
        # Every other granted action is served locally, by its own module.
        assert {b.module for b in registry.bindings("chat.write")} == {"twitch"}
        assert {b.module for b in registry.bindings("chat.read")} == {"chat_context"}
        assert {b.module for b in registry.bindings("users.read")} == {"users"}
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
    """AC43 (R8): ``pip install --no-deps --no-build-isolation --target <empty
    dir> <checkout>`` — one command, network-free because the build backend
    is imported from the test environment — then, from a directory outside
    the checkout with only the target on ``PYTHONPATH`` and the checkout
    removed from ``sys.path``, a configuration with ``modules_directory:
    builtin`` discovers exactly the 8 shipped manifests and the installed
    console script answers ``--help`` with status 0.

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
    assert len(report["manifests"]) == 8
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
