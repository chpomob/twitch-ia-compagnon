"""R3, AC11 (F4): every configuration diagnostic routes through one redaction primitive.

The credential-redaction class is closed by construction: there is one
primitive, :func:`core.contracts.mask_known`, and every function in ``core/``
and ``modules/`` that names a configured key in a diagnostic passes it
through that primitive — directly, through a registered wrapper, or behind a
registered guard that does. This module enumerates those functions with an
AST walk and fails on any that does none of the three, so a new emitting
surface that bypasses the primitive fails the suite.

Heuristics (what the walk recognises, and what it cannot)
-------------------------------------------------------

A **configured key** is a name the function binds from data rather than from
code:

* the target of a ``for`` loop or a comprehension — the first element of a
  tuple target, which is the key of ``.items()`` — over anything that is not
  a code constant (an ``UPPER_CASE`` name or attribute, a literal collection,
  ``range``/``enumerate``/``zip``/``reversed``, or ``.items()``/``.keys()``/
  ``sorted()``/``list()``… of one of those);
* a name assigned from an expression calling ``set(<non-constant>)`` — the
  ``unknown = sorted(set(entry) - KNOWN)`` idiom that lists unknown keys.

A function **formats a key path** — and is therefore a configuration
diagnostic surface — when it holds:

* an f-string interpolating a configured key (or ``str(key)``) right after a
  ``.`` or a ``[`` — a dotted or indexed path segment;
* an f-string interpolating ``<text>.join(<configured key set>)``;
* a call to a **path builder** with a bare configured key among its
  arguments; a path builder is any function of the same file that returns an
  f-string interpolating one of its parameters — bare, or handed to a call
  such as the primitive — right after a ``.`` (the helper indirection of
  ``core.main._mapping_path`` and ``core.contracts._key_label``, found, not
  listed).

A surface is **routed** when one of these holds:

direct
    The function calls :func:`mask_known` or a registered wrapper
    (:data:`WRAPPERS`) with its known-values argument supplied and not an
    empty literal. The check is per function, not per string: a function
    that routes one diagnostic is trusted to route its others, which the
    behavioural AC10 tests pin for the shipped surfaces.

guarded
    The function is reachable, through calls by name (``f()``, ``self.f()``)
    within its own file, from a registered guard entry (:data:`GUARDS`) that
    is itself direct or calls a direct sink; and every caller of the surface
    in that file is the entry or reachable from it, so the surface has no
    unguarded way out.

hook-routed
    In ``modules/``, the function is the module's manifest-declared
    ``settings_validator`` or reachable from it. What a hook returns is
    reported only by :meth:`core.loader.ModuleLoader._validate_settings`
    through :func:`core.loader._module_diagnostic`, which refuses a field
    carrying any known credential and masks the reason; both are asserted
    direct below. A module that also reports its own hook's output somewhere
    else is outside what a call graph by name can see.

What the walk cannot see: a key path built by ``%``/``.format``/``+``, a
path builder in another file, a key smuggled through a container, and a
function that routes one diagnostic but not another. Those are pinned by the
behavioural tests (``tests/test_main.py``, ``tests/test_loader.py``: AC10).
Surfaces that format a key path from data that is not configuration are
listed in :data:`NOT_CONFIGURATION` with the reason, and every entry must
still be a surface, so the list cannot go stale silently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_TREES = ("core", "modules")
PRIMITIVE = "mask_known"

#: Registered wrappers: name → (positional index, keyword) of the argument
#: carrying the known values. A call routes only when that argument is given.
WRAPPERS: dict[str, tuple[int | None, str | None]] = {
    PRIMITIVE: (1, "values"),
    # core/loader.py
    "_redact_values": (1, "values"),
    "_redact_known": (1, "redacted"),
    "_bounded": (1, "redacted"),
    "_quoted": (1, "redacted"),
    "_field_diagnostic": (None, "values"),
    "_field_error": (None, "values"),
    "_module_diagnostic": (None, "values"),
    # core/contracts.py and core/config_ui/__init__.py
    "_redact": (1, "secrets"),
    "_key_label": (2, "redact"),
    "validate_against_schema": (None, "redact"),
    "sanitize_trace": (None, "secrets"),
    # modules/streamerbot/__init__.py: masks the configured password
    "_redacted": (1, "settings"),
}

#: The local wrappers that are pure delegations: a single ``return`` holding
#: exactly one call, to :func:`mask_known` or to another of these (R3).
DELEGATIONS: dict[str, tuple[str, ...]] = {
    "core/contracts.py": ("_redact", "_key_label"),
    "core/loader.py": ("_redact_values", "_redact_known"),
    "core/config_ui/__init__.py": ("_redact",),
}

#: Guards: file → (entry, sink). Every surface of the file reachable from
#: *entry* is guarded when *sink* is direct and *entry* calls it (or is it).
GUARDS: dict[str, tuple[tuple[str, str], ...]] = {
    # Every ConfigurationError raised past the read leaves _load_documents
    # masked of the profile's credentials (R3, F4).
    "core/main.py": (("_load_documents", "_load_documents"),),
    # Save's per-field refusals are returned only through _refused, which
    # passes every line through the UI's redaction wrapper.
    "core/config_ui/__init__.py": (("save", "_refused"),),
}

#: Surfaces that format a key path from data that is not configuration.
NOT_CONFIGURATION: dict[tuple[str, str], str] = {
    ("core/contracts.py", "_freeze"): (
        "freezes action-spec and event metadata a module authors in code"
    ),
    ("core/contracts.py", "validate_schema"): (
        "validates a schema's own keywords; a schema is manifest-authored, never configured"
    ),
    ("core/contracts.py", "_validate_text_part"): "validates an event payload part",
    ("core/contracts.py", "_validate_image_ref_part"): "validates an event payload part",
    ("core/contracts.py", "_validate_audio_ref_part"): "validates an event payload part",
    ("core/contracts.py", "_validate_transcription"): "validates an event payload part",
    ("core/contracts.py", "_validate_delivery"): (
        "validates an ActionSpec's delivery block, which a manifest or a module declares"
    ),
    ("core/loader.py", "_manifest_actions"): (
        "parses a manifest's action declarations; a manifest is module-authored"
    ),
    ("core/loader.py", "_manifest_triggers"): (
        "parses a manifest's trigger declaration; a manifest is module-authored"
    ),
    ("core/loader.py", "_destination"): (
        "parses a manifest action's supported destination; called by _manifest_actions only"
    ),
    ("core/config_ui/__init__.py", "_path_text"): (
        "renders a path for the page; its integer segments are indexes and every "
        "response line it reaches passes the UI's redaction wrapper"
    ),
    ("modules/audio_input/__init__.py", "prepare"): (
        "a phase hook's exception: the coordinator reports the module, never the message"
    ),
    ("modules/audio_output/__init__.py", "prepare"): (
        "a phase hook's exception: the coordinator reports the module, never the message"
    ),
}

_CONSTANT_NAME = re.compile(r"_?[A-Z][A-Z0-9_]*\Z")
_CONSTANT_CALLS = frozenset({"range", "enumerate", "zip", "reversed"})
_TRANSPARENT_CALLS = frozenset({"sorted", "list", "tuple", "set", "frozenset"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #


def _is_constant(node: ast.AST) -> bool:
    """Whether iterating *node* yields code constants rather than data."""

    while True:
        if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Constant)):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _CONSTANT_CALLS:
                return True
            if isinstance(func, ast.Name) and func.id in _TRANSPARENT_CALLS and node.args:
                node = node.args[0]
                continue
            if isinstance(func, ast.Attribute) and func.attr in ("items", "keys", "values"):
                node = func.value
                continue
            return False
        if isinstance(node, ast.Name):
            return _CONSTANT_NAME.fullmatch(node.id) is not None
        if isinstance(node, ast.Attribute):
            return _CONSTANT_NAME.fullmatch(node.attr) is not None
        if isinstance(node, ast.Subscript):
            node = node.value
            continue
        return False


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """Every node of *function*, nested function bodies excluded."""

    found: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        found.append(node)
        if not isinstance(node, (*_FUNCTIONS, ast.Lambda, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _configured_keys(nodes: list[ast.AST]) -> tuple[set[str], set[str]]:
    """The names bound from data: (keys, key sets)."""

    keys: set[str] = set()
    key_sets: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            if _is_constant(node.iter):
                continue
            target = node.target
            if isinstance(target, (ast.Tuple, ast.List)) and target.elts:
                target = target.elts[0]
            if isinstance(target, ast.Name):
                keys.add(target.id)
        elif isinstance(node, ast.Assign) and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "set"
            and call.args
            and not _is_constant(call.args[0])
            for call in ast.walk(node.value)
        ):
            key_sets.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return keys, key_sets


def _unwrapped(node: ast.AST) -> ast.AST:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "str"
        and len(node.args) == 1
    ):
        return node.args[0]
    return node


def _segments(joined: ast.JoinedStr) -> list[tuple[str, ast.AST]]:
    """(the literal text right before it, expression) for each interpolation."""

    found: list[tuple[str, ast.AST]] = []
    before = ""
    for value in joined.values:
        if isinstance(value, ast.Constant):
            before = str(value.value)
        elif isinstance(value, ast.FormattedValue):
            found.append((before, value.value))
            before = ""
    return found


def _path_builders(tree: ast.Module) -> set[str]:
    """Functions returning an f-string with a bare parameter as a path segment."""

    builders: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, _FUNCTIONS):
            continue
        parameters = {argument.arg for argument in function.args.args}
        for node in _own_nodes(function):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.JoinedStr):
                continue
            if any(
                before.endswith(".") and _mentions(expression, parameters)
                for before, expression in _segments(node.value)
            ):
                builders.add(function.name)
    return builders


def _mentions(expression: ast.AST, parameters: set[str]) -> bool:
    """*expression* is a parameter, or a call handed one (``mask_known(key, …)``)."""

    expression = _unwrapped(expression)
    if isinstance(expression, ast.Name):
        return expression.id in parameters
    return isinstance(expression, ast.Call) and any(
        isinstance(argument, ast.Name) and argument.id in parameters
        for argument in expression.args
    )


def _key_path_lines(function: ast.AST, builders: set[str]) -> list[int]:
    """The lines at which *function* formats a configured key into a path."""

    nodes = _own_nodes(function)
    keys, key_sets = _configured_keys(nodes)
    lines: list[int] = []
    for node in nodes:
        if isinstance(node, ast.JoinedStr):
            for before, expression in _segments(node):
                expression = _unwrapped(expression)
                if (
                    isinstance(expression, ast.Name)
                    and expression.id in keys
                    and before.endswith((".", "["))
                ):
                    lines.append(node.lineno)
                elif (
                    isinstance(expression, ast.Call)
                    and isinstance(expression.func, ast.Attribute)
                    and expression.func.attr == "join"
                    and expression.args
                    and isinstance(expression.args[0], ast.Name)
                    and expression.args[0].id in key_sets | keys
                ):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Call) and _called_name(node) in builders:
            if any(
                isinstance(_unwrapped(argument), ast.Name)
                and _unwrapped(argument).id in keys  # type: ignore[attr-defined]
                for argument in node.args[1:]
            ):
                lines.append(node.lineno)
    return sorted(set(lines))


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _empty_literal(node: ast.AST) -> bool:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("tuple", "list", "set", "frozenset")
        and not node.args
    )


def _routes(call: ast.Call) -> bool:
    """Whether *call* is the primitive or a wrapper, given its known values."""

    name = _called_name(call)
    if name not in WRAPPERS:
        return False
    index, keyword = WRAPPERS[name]
    supplied: ast.AST | None = None
    if index is not None and len(call.args) > index:
        supplied = call.args[index]
    for argument in call.keywords:
        if argument.arg is not None and argument.arg == keyword:
            supplied = argument.value
    return supplied is not None and not _empty_literal(supplied)


def _is_direct(function: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and _routes(node) for node in _own_nodes(function)
    )


def _functions(tree: ast.Module) -> dict[str, list[ast.AST]]:
    found: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _FUNCTIONS):
            found.setdefault(node.name, []).append(node)
    return found


def _calls(function: ast.AST) -> set[str]:
    """The same-file functions *function* calls: ``f()``, ``self.f()``, ``cls.f()``.

    A call on any other receiver (``loader._validate_config()``) names
    another object's method and is not an edge of this file's graph.
    """

    found: set[str] = set()
    for node in _own_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            found.add(node.func.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("self", "cls")
        ):
            found.add(node.func.attr)
    return found


def _reachable(functions: dict[str, list[ast.AST]], entries: set[str]) -> set[str]:
    seen: set[str] = set()
    pending = [entry for entry in entries if entry in functions]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for definition in functions[name]:
            pending.extend(
                called
                for called in _calls(definition)
                if called in functions and called not in seen
            )
    return seen


def _callers(functions: dict[str, list[ast.AST]], target: str) -> set[str]:
    return {
        name
        for name, definitions in functions.items()
        if name != target and any(target in _calls(d) for d in definitions)
    }


def _settings_validator(module_file: Path) -> str | None:
    manifest = module_file.parent / "module.yaml"
    if module_file.name != "__init__.py" or not manifest.is_file():
        return None
    document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    hook = document.get("settings_validator") if isinstance(document, dict) else None
    return hook if isinstance(hook, str) else None


def surfaces(root: Path) -> dict[tuple[str, str], list[int]]:
    """Every configuration diagnostic surface under *root*: (file, function) → lines."""

    found: dict[tuple[str, str], list[int]] = {}
    for tree_name in _SOURCE_TREES:
        for path in sorted((root / tree_name).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            builders = _path_builders(tree)
            relative = path.relative_to(root).as_posix()
            for name, definitions in _functions(tree).items():
                lines = sorted(
                    {line for d in definitions for line in _key_path_lines(d, builders)}
                )
                if lines:
                    found[(relative, name)] = lines
    return found


def violations(
    root: Path,
    *,
    guards: dict[str, tuple[tuple[str, str], ...]] = GUARDS,
    excluded: dict[tuple[str, str], str] = NOT_CONFIGURATION,
) -> dict[tuple[str, str], list[int]]:
    """Every surface under *root* that is neither direct, guarded nor hook-routed."""

    unrouted: dict[tuple[str, str], list[int]] = {}
    trees: dict[str, tuple[Path, dict[str, list[ast.AST]]]] = {}
    for (relative, name), lines in surfaces(root).items():
        if (relative, name) in excluded:
            continue
        if relative not in trees:
            path = root / relative
            trees[relative] = (path, _functions(ast.parse(path.read_text(encoding="utf-8"))))
        path, functions = trees[relative]
        if all(_is_direct(definition) for definition in functions[name]):
            continue
        if _is_guarded(functions, name, guards.get(relative, ())):
            continue
        hook = _settings_validator(path) if relative.startswith("modules/") else None
        if hook is not None and _is_guarded(functions, name, ((hook, None),)):
            continue
        unrouted[(relative, name)] = lines
    return unrouted


def _is_guarded(
    functions: dict[str, list[ast.AST]],
    name: str,
    guards: tuple[tuple[str, str | None], ...],
) -> bool:
    for entry, sink in guards:
        if entry not in functions:
            continue
        if sink is not None:
            if sink not in functions or not all(_is_direct(d) for d in functions[sink]):
                continue
            if sink != entry and not all(sink in _calls(d) for d in functions[entry]):
                continue
        reach = _reachable(functions, {entry})
        if name not in reach:
            continue
        if name == entry or _callers(functions, name) <= reach:
            return True
    return False


# --------------------------------------------------------------------------- #
# The shipped tree
# --------------------------------------------------------------------------- #


def test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive() -> None:
    """AC11 (R3, F4): every function of ``core/`` and ``modules/`` that names a
    configured key in a diagnostic routes it through :func:`mask_known` —
    directly, by a registered wrapper, behind a registered guard, or as a
    settings hook the loader's guard reports."""

    found = surfaces(_ROOT)
    # The enumeration sees the surfaces this step routed, so it is not vacuous.
    for expected in (
        ("core/contracts.py", "_apply_object_schema"),
        ("core/loader.py", "_apply_trigger_configuration"),
        ("core/loader.py", "_trigger_policy"),
        ("core/main.py", "_validate_limits"),
        ("core/main.py", "_validate_actions"),
        ("core/config_ui/__init__.py", "_validate_setting"),
        ("modules/streamerbot/__init__.py", "_allowlist_diagnostics"),
    ):
        assert expected in found, expected
    assert violations(_ROOT) == {}


def test_ac11_the_registries_name_what_is_there() -> None:
    """The heuristics' lists cannot go stale: every non-configuration entry
    is still a surface, every guard entry and sink exists and the sink is
    direct, and every wrapper is defined somewhere in the tree."""

    found = surfaces(_ROOT)
    assert [entry for entry in NOT_CONFIGURATION if entry not in found] == []
    for relative, entries in GUARDS.items():
        functions = _functions(ast.parse((_ROOT / relative).read_text(encoding="utf-8")))
        for entry, sink in entries:
            assert entry in functions, (relative, entry)
            assert all(_is_direct(d) for d in functions[sink]), (relative, sink)
    defined = {
        node.name
        for tree_name in _SOURCE_TREES
        for path in (_ROOT / tree_name).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, _FUNCTIONS)
    }
    assert sorted(set(WRAPPERS) - defined) == []


def test_ac11_the_loader_hook_guard_and_the_settings_check_are_direct() -> None:
    """The hook-routed surfaces rely on the loader: its hook guard and its
    settings validation are themselves routed through the primitive, and
    Streamer.bot's hook routes its own diagnostics (AC10)."""

    loader = _functions(ast.parse((_ROOT / "core/loader.py").read_text(encoding="utf-8")))
    for name in ("_module_diagnostic", "_validate_settings", "_field_diagnostic"):
        assert all(_is_direct(d) for d in loader[name]), name
    bridge = _functions(
        ast.parse((_ROOT / "modules/streamerbot/__init__.py").read_text(encoding="utf-8"))
    )
    assert all(_is_direct(d) for d in bridge["validate_settings"])
    assert all(_is_direct(d) for d in bridge["_redacted"])


def test_r3_local_wrappers_are_one_call_delegations_to_the_primitive() -> None:
    """R3: the primitive is defined once, and every local redaction wrapper
    is a single ``return`` making exactly one call — to :func:`mask_known`
    or to another such delegation."""

    definitions = [
        path.relative_to(_ROOT).as_posix()
        for tree_name in _SOURCE_TREES
        for path in (_ROOT / tree_name).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, _FUNCTIONS) and node.name == PRIMITIVE
    ]
    assert definitions == ["core/contracts.py"]
    delegations = {name for names in DELEGATIONS.values() for name in names}
    for relative, names in DELEGATIONS.items():
        tree = ast.parse((_ROOT / relative).read_text(encoding="utf-8"))
        for name in names:
            [definition] = [
                node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
            ]
            body = list(definition.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            assert len(body) == 1 and isinstance(body[0], ast.Return), (relative, name)
            calls = [node for node in ast.walk(body[0]) if isinstance(node, ast.Call)]
            assert len(calls) == 1, (relative, name)
            assert _called_name(calls[0]) in {PRIMITIVE} | delegations, (relative, name)


# --------------------------------------------------------------------------- #
# Self-test: the structural check fails on a surface that bypasses the primitive
# --------------------------------------------------------------------------- #

_BYPASSING_SOURCES = {
    "dotted": (
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ContractError(f'settings.{key}', 'is not an allowed property')\n"
    ),
    "items-and-str": (
        "def check(settings):\n"
        "    for key, value in settings.items():\n"
        "        if value is None:\n"
        "            raise ConfigurationError(f'limits.{str(key)}: is not a known limit')\n"
    ),
    "indexed": (
        "def check(block):\n"
        "    return [f'inputs[{name}]: is unknown' for name in block]\n"
    ),
    "listed-unknown-keys": (
        "def check(entry):\n"
        "    unknown = sorted(set(entry) - {'channels'})\n"
        "    if unknown:\n"
        "        raise ConfigurationError(f'triggers: unknown keys ({\", \".join(unknown)})')\n"
    ),
    "path-builder-indirection": (
        "def _path(parent, key):\n"
        "    return f'{parent}.{key}'\n"
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ConfigurationError(_path('settings', key) + ': unknown')\n"
    ),
    "wrapper-without-values": (
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise _field_error('m', f'settings.{key}', 'unknown', values=())\n"
    ),
}


def _fixture_tree(tmp_path: Path, source: str, *, relative: str = "core/fixture.py") -> Path:
    for tree_name in _SOURCE_TREES:
        (tmp_path / tree_name).mkdir(exist_ok=True)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("case", sorted(_BYPASSING_SOURCES))
def test_ac11_self_test_a_surface_formatting_a_key_path_without_the_primitive_fails(
    tmp_path: Path, case: str
) -> None:
    """AC11 self-test: a fixture source file that formats a configured key
    into a field path without the primitive — in each spelling the walk
    promises to see — makes the structural check fail, naming it."""

    root = _fixture_tree(tmp_path, _BYPASSING_SOURCES[case])

    assert list(violations(root)) == [("core/fixture.py", "check")]


@pytest.mark.parametrize(
    "source",
    [
        "def check(settings, known):\n"
        "    for key in settings:\n"
        "        raise ContractError(f'settings.{mask_known(key, known)}', 'unknown')\n",
        "def check(settings, known):\n"
        "    for key in settings:\n"
        "        raise _field_error('m', f'settings.{key}', 'unknown', values=known)\n",
        "def check(settings):\n"
        "    for key in KNOWN_KEYS:\n"
        "        raise ContractError(f'settings.{key}', 'is required')\n",
        "def check(settings):\n"
        "    for index, item in enumerate(settings):\n"
        "        raise ContractError(f'settings[{index}]', 'must be a mapping')\n",
    ],
)
def test_ac11_self_test_a_routed_or_constant_path_passes(
    tmp_path: Path, source: str
) -> None:
    """Positive control: the primitive, a wrapper given its values, a key
    from a code constant and an index are not violations."""

    assert violations(_fixture_tree(tmp_path, source)) == {}


def test_ac11_self_test_a_guard_covers_only_what_it_reaches(tmp_path: Path) -> None:
    """A guard covers a surface its entry reaches and its sink redacts; the
    same surface also called from outside the entry is a violation."""

    guarded = (
        "def entry(settings, known):\n"
        "    try:\n"
        "        check(settings)\n"
        "    except ValueError as exc:\n"
        "        raise ValueError(mask_known(str(exc), known))\n"
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ValueError(f'settings.{key}: unknown')\n"
    )
    guards = {"core/fixture.py": (("entry", "entry"),)}
    root = _fixture_tree(tmp_path, guarded)
    assert violations(root, guards=guards) == {}
    (root / "core/fixture.py").write_text(
        guarded + "def elsewhere(settings):\n    check(settings)\n", encoding="utf-8"
    )
    assert list(violations(root, guards=guards)) == [("core/fixture.py", "check")]


def test_ac11_self_test_a_module_hook_is_routed_only_when_declared(tmp_path: Path) -> None:
    """In ``modules/``, a helper of the manifest-declared settings hook is
    hook-routed; the same helper in a module that declares no hook is not."""

    source = (
        "def validate_settings(settings):\n"
        "    return _helper(settings)\n"
        "def _helper(settings):\n"
        "    return [f\"module 'm': field 'x.{key}': unknown\" for key in settings]\n"
    )
    root = _fixture_tree(tmp_path, source, relative="modules/fixture/__init__.py")
    assert list(violations(root)) == [("modules/fixture/__init__.py", "_helper")]
    (root / "modules/fixture/module.yaml").write_text(
        "settings_validator: validate_settings\n", encoding="utf-8"
    )
    assert violations(root) == {}
