"""The install guides are guides the code accepts (docs/installation.md, .fr.md).

``docs/installation.md`` and its French counterpart hand a non-technical reader
~100 lines of YAML to copy, eight values to export and three commands to run. A
guide that drifts from the code is worse than no guide: the reader's first command
would fail and nothing in the document would say why. So both documents are
exercised here, by the same rules:

* the profile block each one prints is written out and checked by the real entry
  point's ``--check-config``, with exactly the eight values the guide tells the
  reader to export — so the block is validated by the code path the reader runs;
* every ``${...}`` reference in that block is one of the eight documented exports
  (an undocumented reference is precisely the first error a reader would hit);
* every ``python -m X`` and console-script command both guides and the README
  print resolves against the code — ``X`` importable with a callable entry point,
  or a ``[project.scripts]`` name — and the README's configuration-UI command,
  run exactly as printed with ``--help`` for its arguments, exits 0 (R8, F16, F18);
* the ``MODEL_ENDPOINT`` values the guides give are complete Chat Completions URLs,
  and a fake-backed startup of the guide's profile posts to exactly that URL
  (R8, F15);
* the overlay file the documents name is the one ``core/overlay.py`` derives for
  their profile (R8, F17);
* the two documents name each other and print the same profile, which is what makes
  the pair a translation rather than two documents that happen to share a subject.

The environment is built explicitly rather than inherited: the check must pass on
the guide's eight values, not on whatever the developer happens to have exported.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import re
import shlex
import sys
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
from conftest import FakeResponse, FakeSession
from core.overlay import implicit_overlay_path
from modules.twitch import EVENTSUB_SUBSCRIPTIONS_URL

ROOT = Path(__file__).parents[1]
GUIDES = (ROOT / "docs" / "installation.md", ROOT / "docs" / "installation.fr.md")
"""The English guide and its French counterpart, in that order."""

README = ROOT / "README.md"
DOCUMENTS = (*GUIDES, README)
"""Every document that prints a command a newcomer copies."""

PROFILE_NAME = "mon-compagnon.yaml"
"""The file name both guides tell the reader to save the profile under."""

README_PROFILE_NAME = "my-config.yaml"
"""The profile name the README's quick start passes to ``--config``."""

PRE_FIX_README_COMMAND = "python -m twitch-ia-compagnon-config-ui --config my-config.yaml"
"""The README's configuration-UI line before F16: a console-script name given to ``-m``."""

VALUES = {
    "TWITCH_CLIENT_ID": "guide-client-id",
    "TWITCH_CLIENT_SECRET": "guide-client-secret",
    "TWITCH_ACCESS_TOKEN": "guide-access-token",
    "TWITCH_BROADCASTER_ID": "123456789",
    "TWITCH_BOT_USER_ID": "987654321",
    "MODEL_ENDPOINT": "https://api.example.com/v1/chat/completions",
    "MODEL_NAME": "guide-model",
    "MODEL_API_KEY": "guide-api-key",
}
"""The eight values both guides export in their section 7; none is a real credential."""

_BLOCK = re.compile(r"^```(?P<language>[a-z]+)\n(?P<body>.*?)^```$", re.MULTILINE | re.DOTALL)
_REFERENCES = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXPORTS = re.compile(r"^(?:export\s+)?(?:\$env:)?([A-Z][A-Z0-9_]*)[=]", re.MULTILINE)
_TITLE = re.compile(r"^# (.+)$", re.MULTILINE)


def _text(guide: Path) -> str:
    return guide.read_text(encoding="utf-8")


def _profile(guide: Path) -> str:
    """A guide's one profile block, with the modules directory made absolute.

    The block ships ``modules_directory: ./modules`` because the reader runs from
    the checkout; this test writes the file elsewhere, so only that one path is
    pointed back at the repository.
    """

    bodies = [
        match.group("body")
        for match in _BLOCK.finditer(_text(guide))
        if match.group("language") == "yaml"
    ]
    assert len(bodies) == 1, f"{guide.name}: exactly one profile block"
    (body,) = bodies
    assert "modules_directory: ./modules" in body, f"{guide.name}: that path is gone"
    return body.replace(
        "modules_directory: ./modules", f"modules_directory: {ROOT / 'modules'}"
    )


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_the_guides_profile_is_accepted_by_the_real_entry_point(
    guide: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--check-config`` over the printed block, with the eight printed values.

    Phase 8 (R9, AC33, plan P15e): the entry point's ``main`` is called
    in-process with the arguments the reader types, instead of a child
    interpreter: the same argument parsing, the same check and the same
    status, with nothing printed to either stream."""

    config = tmp_path / PROFILE_NAME
    config.write_text(_profile(guide), encoding="utf-8")
    # The guide's own values, over whatever this developer has exported.
    for name, value in {**VALUES, "NO_COLOR": "1"}.items():
        monkeypatch.setenv(name, value)

    status = application.main(["--config", str(config), "--check-config"])
    captured = capsys.readouterr()
    assert status == 0, captured.out + captured.err
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_every_reference_in_the_profile_is_an_export_the_guide_gives(guide: Path) -> None:
    """No ``${...}`` the reader was never told to export."""

    referenced = set(_REFERENCES.findall(_profile(guide)))
    assert referenced == set(VALUES)
    documented = set(_EXPORTS.findall(_text(guide)))
    assert referenced <= documented, sorted(referenced - documented)


_MODULE_COMMAND = re.compile(r"(?<![\w.-])(?:python3?|py(?: -3)?) -m (?P<module>[^\s`|]+)")
_INLINE = re.compile(r"`([^`\n]+)`")
_CONSOLE_PREFIX = "twitch-ia-compagnon"
_BOOTSTRAPPED = {"pip": "ensurepip"}
"""Tools a fresh virtual environment may not carry yet, by their stdlib bootstrapper.

``python -m pip`` is printed for a reader whose environment ``venv`` created —
which installs pip through ``ensurepip``. An environment created otherwise (this
suite's may be) can lack pip itself, so pip resolves when it is importable or when
the bootstrapper that provides it is.
"""


def _project_scripts() -> frozenset[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return frozenset(project["project"]["scripts"])


def _command_lines(text: str) -> list[str]:
    """Every line a reader copies: fenced-block lines and inline code spans."""

    lines = [
        line.strip()
        for match in _BLOCK.finditer(text)
        if match.group("language") != "yaml"
        for line in match.group("body").splitlines()
    ]
    lines.extend(span.strip() for span in _INLINE.findall(text))
    return [line for line in lines if line]


def _modules(text: str) -> list[str]:
    """The ``X`` of every ``python -m X`` the text prints, in order."""

    return [
        match.group("module")
        for line in _command_lines(text)
        for match in _MODULE_COMMAND.finditer(line)
    ]


def _console_scripts(text: str) -> list[str]:
    """Every command line that starts with the project's console-script prefix."""

    return [
        line.split()[0]
        for line in _command_lines(text)
        if line.split()[0].startswith(_CONSOLE_PREFIX)
    ]


def _module_unresolved(module: str) -> str | None:
    """Why ``python -m module`` would not run, or ``None`` when it resolves.

    It resolves when the module is importable, is runnable by ``-m`` (a
    package needs its ``__main__``) and exposes a callable ``main`` — the
    entry point a command runs. The ``__main__`` is located, never imported:
    importing it would run the command.
    """

    if module in _BOOTSTRAPPED and importlib.util.find_spec(module) is None:
        module = _BOOTSTRAPPED[module]
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return f"{module}: no such module"
    if spec.submodule_search_locations is not None:
        if importlib.util.find_spec(f"{module}.__main__") is None:
            return f"{module}: a package without __main__"
    entry = getattr(importlib.import_module(module), "main", None)
    if entry is None and module in _BOOTSTRAPPED.values():
        entry = getattr(importlib.import_module(module), "_main", None)
    if not callable(entry):
        return f"{module}: no callable entry point"
    return None


def _unresolved_commands(text: str) -> list[str]:
    scripts = _project_scripts()
    failures = [
        reason
        for reason in (_module_unresolved(module) for module in _modules(text))
        if reason is not None
    ]
    failures.extend(
        f"{name}: not a [project.scripts] entry"
        for name in _console_scripts(text)
        if name not in scripts
    )
    return failures


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_the_guide_prints_the_commands_the_code_still_has(document: Path) -> None:
    """Every printed command resolves against the code, not against a grep (F18, R8).

    Replaces a grep of the guide text for command names, which passed for a
    command the code no longer has and never looked at the README (F18). Each
    ``python -m X`` printed in both guides and the README must name an
    importable, ``-m``-runnable module exposing a callable entry point, and each
    console-script command a ``[project.scripts]`` name of ``pyproject.toml``
    (R8). The commands the guides walk the reader through — the check, the
    run, the configuration UI and the demo — must still be among them, with
    the check flag on the guide's profile.

    On each guide, the pre-fix test's exact assertions still hold on top of the
    resolution: the run and the configuration UI carry the guide's profile
    argument, so a UI command pointed at another file fails (N2, R10/AC38).
    """

    text = _text(document)
    modules = _modules(text)
    assert modules, f"{document.name}: no command found"
    assert _unresolved_commands(text) == []
    assert {"core.main", "core.config_ui", "demo"} <= set(modules)
    if document in GUIDES:
        assert f"--config {PROFILE_NAME} --check-config" in text
        assert f"python -m core.main --config {PROFILE_NAME}" in text
        assert f"python -m core.config_ui --config {PROFILE_NAME}" in text
        assert "python -m demo" in text


def test_the_pre_fix_readme_line_does_not_resolve() -> None:
    """The resolution rejects the README line F16 found (F18, R8).

    ``twitch-ia-compagnon-config-ui`` is a console script, not a module: the
    grep this resolution replaces could not tell the two apart.
    """

    assert _unresolved_commands(f"```bash\n{PRE_FIX_README_COMMAND}\n```\n") == [
        "twitch-ia-compagnon-config-ui: no such module"
    ]


def _readme_config_ui_command() -> str:
    """The README quick start's configuration-UI line, as printed."""

    for match in _BLOCK.finditer(_text(README)):
        lines = match.group("body").splitlines()
        for comment, command in zip(lines, lines[1:]):
            if comment.startswith("#") and "configuration UI" in comment:
                return command.split("#", 1)[0].strip()
    raise AssertionError("README: no configuration-UI command")


def _exit_status(entry: Any, argv: list[str]) -> int:
    """What a process running *entry* with *argv* exits with."""

    try:
        code = entry(argv)
    except SystemExit as exit_:
        code = exit_.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 1


def _run_with_help(command: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """Run *command* as printed, its arguments replaced by ``--help``: its
    exit status and what it printed.

    The command is everything before its first option. Phase 8 (R9, AC33,
    plan P15e): it runs in-process, not in a child interpreter. ``python -m
    X`` calls ``X``'s ``main`` — a name no module answers to exits 1, as the
    interpreter does — and a console script is the ``main`` its installed
    entry point names, resolved through ``importlib.metadata``.
    """

    words = shlex.split(command)
    first_option = next(
        (index for index, word in enumerate(words) if word.startswith("--")), len(words)
    )
    program = words[:first_option]
    capsys.readouterr()
    if program[0] in ("python", "python3"):
        assert program[1] == "-m" and len(program) == 3, program
        try:
            spec = importlib.util.find_spec(program[2])
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            return 1, ""
        entry = importlib.import_module(program[2]).main
    else:
        entry = _console_script(program[0])
    status = _exit_status(entry, ["--help"])
    return status, capsys.readouterr().out


def _console_script(name: str) -> Any:
    """The callable the installed console script *name* runs."""

    found = [
        point for point in importlib.metadata.entry_points(group="console_scripts")
        if point.name == name
    ]
    assert len(found) == 1, f"{name}: not an installed console script"
    return found[0].load()


def test_the_readme_config_ui_command_runs(capsys: pytest.CaptureFixture[str]) -> None:
    """The README's configuration-UI command runs, as printed (F16, R8).

    Executed exactly as printed with its arguments replaced by ``--help``: it
    exits 0 and prints the usage naming ``--config``. The pre-fix line, under
    the same check, exits non-zero. The ``[project.scripts]`` console script
    for the same entry point, resolved through the installed distribution's
    entry points, answers ``--help`` alike (phase 8 R9, AC33: in-process).
    """

    command = _readme_config_ui_command()
    assert command.endswith(f"--config {README_PROFILE_NAME}")
    status, output = _run_with_help(command, capsys)
    assert status == 0, output
    assert "usage:" in output
    assert "--config" in output

    assert _run_with_help(PRE_FIX_README_COMMAND, capsys)[0] != 0

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    (script,) = [name for name, target in scripts.items() if target == "core.config_ui:main"]
    status, output = _run_with_help(f"{script} --config {README_PROFILE_NAME}", capsys)
    assert status == 0, output
    assert "usage:" in output and "--config" in output


# --------------------------------------------------------------------------- #
# F15: the endpoint the guide gives is the URL the brain posts to
# --------------------------------------------------------------------------- #


def _endpoint_values(guide: Path) -> list[str]:
    """The example ``MODEL_ENDPOINT`` URLs of the guide's table of values."""

    rows = [
        line for line in _text(guide).splitlines() if line.startswith("| `MODEL_ENDPOINT` |")
    ]
    assert len(rows) == 1, f"{guide.name}: one MODEL_ENDPOINT row"
    examples = rows[0].split("|")[3]
    return [span for span in _INLINE.findall(examples) if span.startswith("http")]


class _FakeEventSocket:
    """A platform socket that welcomes the session, then stays silent."""

    def __init__(self) -> None:
        self._frames: asyncio.Queue[Any] = asyncio.Queue()
        self._frames.put_nowait(
            {
                "metadata": {"message_type": "session_welcome"},
                "payload": {"session": {"id": "guide-session"}},
            }
        )

    async def receive(self) -> Any:
        return await self._frames.get()

    async def close(self) -> None:
        return None


class _FakePlatformSession:
    """The platform edge of the guide's profile: validation and subscription."""

    def __init__(self) -> None:
        self._socket = _FakeEventSocket()

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(
            200,
            {"client_id": VALUES["TWITCH_CLIENT_ID"], "user_id": VALUES["TWITCH_BOT_USER_ID"]},
        )

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        assert url == EVENTSUB_SUBSCRIPTIONS_URL, url
        return FakeResponse(202, {"data": [{"id": "guide-subscription"}]})

    async def ws_connect(self, url: str) -> _FakeEventSocket:
        return self._socket

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
def test_the_guides_endpoint_values_are_complete_chat_completions_urls(guide: Path) -> None:
    """Each example ``MODEL_ENDPOINT`` is a full request URL (F15, R8).

    The adapter posts to the endpoint as given; a base URL ending in ``/v1``
    is a 404 the reader cannot diagnose.
    """

    values = _endpoint_values(guide)
    assert len(values) >= 2, values
    assert all(value.endswith("/chat/completions") for value in values), values


@pytest.mark.asyncio
@pytest.mark.parametrize("guide", GUIDES, ids=lambda path: path.name)
async def test_the_guides_profile_posts_to_the_guides_endpoint(
    guide: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fake-backed startup of the guide's profile posts to the guide's value (F15, R8).

    The printed profile is run through :func:`core.main.run` with each example
    ``MODEL_ENDPOINT`` the guide gives; only the platform session, the model
    transport and the audit writer are fakes. The brain's prepare-time probe
    is the request it makes, and its URL is exactly the guide's value — a
    complete Chat Completions URL, since the adapter adds no path of its own.
    """

    config = tmp_path / PROFILE_NAME
    config.write_text(_profile(guide), encoding="utf-8")
    real_load_config = application.load_config

    endpoints = _endpoint_values(guide)
    assert endpoints and all(value.endswith("/chat/completions") for value in endpoints)
    for endpoint in endpoints:
        model = FakeSession()
        diagnostics: list[str] = []

        def load_with_fakes(
            path: str | Path,
            *,
            environ: Mapping[str, str] | None = None,
            _model: FakeSession = model,
            _diagnostics: list[str] = diagnostics,
        ) -> dict[str, Any]:
            loaded = real_load_config(path, environ=environ)
            platform = _FakePlatformSession()
            loaded["modules"]["twitch"]["_session_factory"] = lambda: platform
            loaded["modules"]["brain"].update(
                {"_session_factory": lambda: _model, "diagnostic_reporter": _diagnostics.append}
            )
            loaded["modules"]["audit"]["_writer"] = lambda line: json.loads(line)
            return loaded

        monkeypatch.setattr(application, "load_config", load_with_fakes)
        stop = asyncio.Event()
        ready = asyncio.Event()
        task = asyncio.create_task(
            application.run(
                config,
                stop,
                environ={**VALUES, "MODEL_ENDPOINT": endpoint},
                ready_reporter=lambda message: ready.set(),
                diagnostic_reporter=diagnostics.append,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
        finally:
            stop.set()
        assert await asyncio.wait_for(task, timeout=5) == 0, diagnostics
        assert diagnostics == []

        posted = [call["url"] for call in model.probe_calls + model.post_calls]
        assert posted, f"{endpoint}: the brain posted nothing"
        assert set(posted) == {endpoint}


# --------------------------------------------------------------------------- #
# F17: the overlay the documents name is the one the code writes
# --------------------------------------------------------------------------- #

_OVERLAY_NAME = re.compile(r"(?<![\w.-])[\w.-]+\.local\.ya?ml(?![\w.-])")


@pytest.mark.parametrize(
    ("document", "profile"),
    [(GUIDES[0], PROFILE_NAME), (GUIDES[1], PROFILE_NAME), (README, README_PROFILE_NAME)],
    ids=lambda value: value.name if isinstance(value, Path) else value,
)
def test_the_documents_name_the_overlay_the_code_derives(document: Path, profile: str) -> None:
    """The overlay named is the one ``core/overlay.py`` derives for the profile (F17, R8).

    The name is computed by the overlay code, not written here as a literal:
    ``mon-compagnon.yaml`` gives ``mon-compagnon.local.yaml``. Every overlay
    file name the document prints must be that one.
    """

    text = _text(document)
    assert f"--config {profile}" in text, f"{document.name} does not use {profile}"
    derived = implicit_overlay_path(profile)
    assert derived is not None
    named = set(_OVERLAY_NAME.findall(text))
    assert named == {derived.name}, sorted(named)


def test_the_two_guides_are_a_translation_pair() -> None:
    """Each names the other, and both print the same profile.

    The blocks are compared as loaded YAML, not as text: the comments are
    translated, and the only literal that is legitimately translated is the
    fallback sentence the bot says. Everything else — every key, every bound,
    every reference — must be identical, or one language is teaching a profile the
    other does not.
    """

    english, french = GUIDES
    assert "Français](installation.fr.md)" in _text(english)
    assert "English](installation.md)" in _text(french)

    profiles = []
    for guide in GUIDES:
        profile = yaml.safe_load(_profile(guide))
        fallback = profile["modules"]["brain"]["fallback"]["text"]
        profile["modules"]["brain"]["fallback"]["text"] = "<translated>"
        profiles.append((profile, fallback))
    (english_profile, english_fallback), (french_profile, french_fallback) = profiles
    assert english_profile == french_profile, "the two profiles drifted apart"
    assert english_fallback != french_fallback, "the French guide kept the English sentence"

    titles = [_TITLE.search(_text(guide)).group(1) for guide in GUIDES]
    assert titles[0] != titles[1], "the two guides print the same title"
