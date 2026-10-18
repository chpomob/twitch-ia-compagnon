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
diagnostic surface — at each **occurrence** of a configured key (or
``str(key)``) placed right after a ``.`` or a ``[`` — a dotted or indexed path
segment — in any spelling: an f-string, a ``'….{}'.format(key)``, a
``'….' + key`` concatenation or a ``'….%s' % key``; at each
``<text>.join(<configured key set>)`` in one of those; and at each bare
configured key handed to a **path builder** — any function of the same file
that returns an f-string interpolating one of its parameters, bare or handed
to a call such as the primitive, right after a ``.`` (the helper indirection
of ``core.main._mapping_path`` and ``core.contracts._key_label``, found, not
listed).

A surface is **routed** when one of these holds:

direct
    *Every* occurrence is routed by itself (F4, N1): it is an argument, at
    any depth, of :func:`mask_known` or a registered wrapper
    (:data:`WRAPPERS`) given its known-values argument, not an empty literal;
    or it is assigned to a name every read of which is routed; or it is
    handed to a function of the same file whose parameter is, at every read,
    routed. A wrapper is judged by its call alone — called without its
    values it routes nothing, whatever its body does with the empty default.
    A redactor call elsewhere in the function, on another string, routes
    nothing: that was probe D's fourth fixture.

guarded
    The function is reachable, through calls by name (``f()``, ``self.f()``)
    within its own file, from a registered guard entry (:data:`GUARDS`) that
    is itself direct or calls a direct sink; and every caller of the surface
    in that file is the entry or reachable from it, so the surface has no
    unguarded way out.

hook-routed
    In ``modules/``, the function is the module's manifest-declared
    ``settings_validator`` or reachable from it. What a hook returns or
    raises becomes text only through the one constructor,
    :func:`core.loader.hook_diagnostics`, which masks every known value out
    of the field and the reason and never reads an exception's text. That
    is not trusted, it is checked: :func:`hook_outcome_renderers` fails on any
    function of ``core/`` or ``modules/`` — the loader, a ``__main__`` command,
    an activation — that renders a hook's outcome, its field path, its reason
    or its ``str()``, anywhere but the constructor (probe C).

What the walk cannot see: a key path built by a path builder in another
file, a key smuggled through a container or an attribute, and a hook reached
by a name the file neither imports from the module package nor resolves from
``settings_validator``, and a hook's outcome handed to a helper of another
file (helpers of the same file are followed). Those are pinned by the behavioural tests
(``tests/test_main.py``, ``tests/test_loader.py``: AC10). Surfaces that format
a key path from data that is not configuration are listed in
:data:`NOT_CONFIGURATION`, and consumers that render a hook's outcome no
reporter reads in :data:`HOOK_OUTCOME_UNREPORTED`, each with the reason;
every entry must still be what it is listed as, so neither list can go stale
silently.
"""

from __future__ import annotations

import ast
import re
import shutil
import string
import sys
from collections.abc import Callable, Iterable
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


def _key_path_occurrences(function: ast.AST, builders: set[str]) -> list[ast.AST]:
    """The nodes at which *function* formats a configured key into a path.

    Each occurrence is the key expression itself — not the string around it —
    so whether it is routed is decided for that one key (F4, N1): an
    f-string, ``.format``, ``+`` concatenation and ``%`` all format the key
    after a ``.`` or a ``[``, and a path builder takes it as an argument.
    """

    nodes = _own_nodes(function)
    keys, key_sets = _configured_keys(nodes)

    def is_key(expression: ast.AST) -> bool:
        expression = _unwrapped(expression)
        return isinstance(expression, ast.Name) and expression.id in keys

    def is_key_list(expression: ast.AST) -> bool:
        return (
            isinstance(expression, ast.Call)
            and isinstance(expression.func, ast.Attribute)
            and expression.func.attr == "join"
            and bool(expression.args)
            and isinstance(expression.args[0], ast.Name)
            and expression.args[0].id in key_sets | keys
        )

    found: list[ast.AST] = []
    for node in nodes:
        if isinstance(node, ast.JoinedStr):
            for before, expression in _segments(node):
                if is_key(expression) and before.endswith((".", "[")):
                    found.append(_unwrapped(expression))
                elif is_key_list(expression):
                    found.append(expression)
        elif isinstance(node, ast.Call) and _called_name(node) in builders:
            found.extend(
                _unwrapped(argument) for argument in node.args[1:] if is_key(argument)
            )
        elif _is_format_call(node):
            for before, expression in _format_fields(node):  # type: ignore[arg-type]
                if is_key(expression) and before.endswith((".", "[")):
                    found.append(_unwrapped(expression))
                elif is_key_list(expression):
                    found.append(expression)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            operands = _added(node)
            for previous, operand in zip(operands, operands[1:]):
                if is_key(operand) and _trailing_text(previous).endswith((".", "[")):
                    found.append(_unwrapped(operand))
                elif is_key_list(operand):
                    found.append(operand)
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
        ):
            arguments = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            for before, expression in zip(_percent_prefixes(node.left.value), arguments):
                if is_key(expression) and before.endswith((".", "[")):
                    found.append(_unwrapped(expression))
                elif is_key_list(expression):
                    found.append(expression)
    unique: dict[int, ast.AST] = {id(node): node for node in found}
    return list(unique.values())


def _key_path_lines(function: ast.AST, builders: set[str]) -> list[int]:
    """The lines at which *function* formats a configured key into a path."""

    return sorted({node.lineno for node in _key_path_occurrences(function, builders)})


def _is_format_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    )


def _format_fields(call: ast.Call) -> list[tuple[str, ast.AST]]:
    """(the literal text right before it, argument) for each ``{}`` of a ``.format``."""

    template = call.func.value.value  # type: ignore[attr-defined]
    keywords = {argument.arg: argument.value for argument in call.keywords if argument.arg}
    found: list[tuple[str, ast.AST]] = []
    automatic = 0
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError:
        return found
    for literal, field_name, _spec, _conversion in parsed:
        if field_name is None:
            continue
        head = re.split(r"[.\[]", field_name, maxsplit=1)[0]
        if head == "":
            index: int | str = automatic
            automatic += 1
        elif head.isdigit():
            index = int(head)
        else:
            index = head
        if isinstance(index, int):
            if index < len(call.args):
                found.append((literal, call.args[index]))
        elif index in keywords:
            found.append((literal, keywords[index]))
    return found


def _percent_prefixes(template: str) -> list[str]:
    """The literal text right before each conversion of a ``%`` template."""

    prefixes: list[str] = []
    position = 0
    for match in re.finditer(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?([a-zA-Z%])", template):
        if match.group(1) == "%":
            continue
        prefixes.append(template[position : match.start()])
        position = match.end()
    return prefixes


def _added(node: ast.AST) -> list[ast.AST]:
    """The operands of a ``+`` chain, left to right."""

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _added(node.left) + _added(node.right)
    return [node]


def _trailing_text(node: ast.AST) -> str:
    """The literal text a string operand ends with, or ``""``."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        last = node.values[-1]
        if isinstance(last, ast.Constant):
            return str(last.value)
    return ""


def _parents(function: ast.AST) -> dict[int, ast.AST]:
    """child id → parent, for every node under *function*."""

    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(function):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


class _Routing:
    """Whether one key-path occurrence reaches the primitive, in one file.

    An occurrence is routed when it is an argument — at any depth — of a call
    to the primitive or a registered wrapper given its known values; when it
    is assigned to a name every read of which is itself routed; or when it is
    handed to a function of the same file whose parameter receiving it is, at
    every read, routed. Routing is decided per occurrence: a redactor call
    elsewhere in the function, on another string, routes nothing (F4, N1).
    """

    def __init__(self, functions: dict[str, list[ast.AST]]) -> None:
        self._functions = functions
        self._parents: dict[int, dict[int, ast.AST]] = {}
        self._nodes: dict[int, list[ast.AST]] = {}

    def unrouted(self, function: ast.AST, builders: set[str]) -> list[int]:
        """The lines of *function*'s key-path occurrences no routing call holds."""

        return sorted(
            {
                node.lineno
                for node in _key_path_occurrences(function, builders)
                if not self._routed(function, node, set())
            }
        )

    def _name_routed(self, function: ast.AST, name: str, seen: set[tuple[int, str]]) -> bool:
        if (id(function), name) in seen:
            return True
        seen.add((id(function), name))
        if id(function) not in self._nodes:
            self._nodes[id(function)] = _own_nodes(function)
        return all(
            self._routed(function, node, seen)
            for node in self._nodes[id(function)]
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)
        )

    def _routed(self, function: ast.AST, occurrence: ast.AST, seen: set[tuple[int, str]]) -> bool:
        if id(function) not in self._parents:
            self._parents[id(function)] = _parents(function)
        parents = self._parents[id(function)]
        node = occurrence
        while node is not function:
            parent = parents.get(id(node))
            if parent is None:
                return False
            if isinstance(parent, ast.keyword):
                keyword, node, parent = parent.arg, parent, parents.get(id(parent))
                if not isinstance(parent, ast.Call):
                    return False
                if _routes(parent):
                    return True
                return self._parameter_routed(parent, None, keyword, seen)
            if isinstance(parent, ast.Call) and node is not parent.func:
                if _routes(parent):
                    return True
                if any(node is argument for argument in parent.args):
                    index = next(i for i, a in enumerate(parent.args) if a is node)
                    if self._parameter_routed(parent, index, None, seen):
                        return True
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and (
                node is parent.value
            ):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                if not all(isinstance(target, ast.Name) for target in targets):
                    return False
                return all(
                    self._name_routed(function, target.id, seen)  # type: ignore[attr-defined]
                    for target in targets
                )
            if isinstance(parent, ast.stmt):
                return False
            node = parent
        return False

    def _parameter_routed(
        self,
        call: ast.Call,
        index: int | None,
        keyword: str | None,
        seen: set[tuple[int, str]],
    ) -> bool:
        """Whether the same-file callee of *call* routes the parameter it is handed."""

        func = call.func
        if isinstance(func, ast.Name):
            name, bound = func.id, False
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in ("self", "cls")
        ):
            name, bound = func.attr, True
        else:
            return False
        # A wrapper routes by its known values or not at all: called without
        # them, what its own body does with an empty default routes nothing.
        definitions = self._functions.get(name)
        if not definitions or name in WRAPPERS:
            return False
        for definition in definitions:
            arguments = definition.args  # type: ignore[attr-defined]
            positional = [a.arg for a in (*arguments.posonlyargs, *arguments.args)]
            if bound and positional[:1] in (["self"], ["cls"]):
                positional = positional[1:]
            if keyword is not None:
                parameter = keyword
                if parameter not in positional + [a.arg for a in arguments.kwonlyargs]:
                    return False
            elif index is not None and index < len(positional):
                parameter = positional[index]
            else:
                return False
            if not self._name_routed(definition, parameter, seen):
                return False
        return True


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
    builders: dict[str, set[str]] = {}
    routing: dict[str, _Routing] = {}
    for relative, name in surfaces(root):
        if (relative, name) in excluded:
            continue
        if relative not in trees:
            path = root / relative
            tree = ast.parse(path.read_text(encoding="utf-8"))
            trees[relative] = (path, _functions(tree))
            builders[relative] = _path_builders(tree)
            routing[relative] = _Routing(trees[relative][1])
        path, functions = trees[relative]
        missed = sorted(
            {
                line
                for definition in functions[name]
                for line in routing[relative].unrouted(definition, builders[relative])
            }
        )
        if not missed:
            continue
        if _is_guarded(functions, name, guards.get(relative, ())):
            continue
        hook = _settings_validator(path) if relative.startswith("modules/") else None
        if hook is not None and _is_guarded(functions, name, ((hook, None),)):
            continue
        unrouted[(relative, name)] = missed
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
# A settings hook's outcome becomes text only through the constructor
# --------------------------------------------------------------------------- #

#: The one constructor of a hook's diagnostics, :func:`core.loader.hook_diagnostics`.
CONSTRUCTOR = "hook_diagnostics"

_CONSTRUCTOR_HOME = ("core/loader.py", CONSTRUCTOR)

#: Calls that read a property of a value and never its text.
_VALUE_FREE_CALLS = frozenset({"len", "bool", "isinstance", "type", "id", "callable", "isawaitable"})

#: Calls that render their argument as text, or keep it for a later render.
_RENDERING_CALLS = frozenset(
    {"str", "repr", "format", "print", "join", "write", "writelines", "append", "extend",
     "insert", "add", "update", "setdefault", "warn", "debug", "info", "warning", "error",
     "exception", "critical", "log"}
)

#: Consumers that render a hook's outcome but whose text no reporter reads,
#: each with the reason; every entry must still be a violation.
HOOK_OUTCOME_UNREPORTED: dict[tuple[str, str], str] = {
    (f"modules/{module}/__init__.py", "_Settings.from_mapping"): (
        "raises the hook's first diagnostic as the exception of activate(); the "
        "loader replaces every activation exception by its own value-free refusal "
        "and never reads its text (ModuleLoader._activate). Outside this step's "
        "files: reported for a follow-up rather than changed here"
    )
    for module in ("brain", "kick", "twitch", "youtube")
} | {
    ("core/loader.py", "ModuleLoader._abandon"): (
        "reached through _await_module with the task wrapping a settings hook's "
        "still-pending awaitable, which it appends to self._abandoned only so "
        "the loop's teardown observes the task's end (abandoned_tasks); the "
        "task's outcome is consumed by _consume_result and never rendered"
    ),
}


def _settings_hooks(root: Path) -> dict[str, str]:
    """Each module package's import path → the hook its manifest declares."""

    hooks: dict[str, str] = {}
    for manifest in sorted((root / "modules").glob("*/module.yaml")):
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        hook = document.get("settings_validator") if isinstance(document, dict) else None
        if isinstance(hook, str):
            hooks[f"modules.{manifest.parent.name}"] = hook
    return hooks


def _hook_names(tree: ast.Module, relative: str, hooks: dict[str, str]) -> set[str]:
    """The names that call a settings hook in this file.

    A hook imported from its module package (``from modules.x import
    validate_settings``, under any alias), and, inside a module's own
    package, the hook it declares.
    """

    names: set[str] = set()
    package = ".".join(Path(relative).parent.parts)
    if package in hooks:
        names.add(hooks[package])
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        source = node.module or ""
        if node.level:
            parts = package.split(".")
            base = parts[: len(parts) - node.level + 1]
            source = ".".join([*base, source] if source else base)
        hook = hooks.get(source)
        names.update(alias.asname or alias.name for alias in node.names if alias.name == hook)
    return names


class _HookOutcome:
    """Where one function lets a settings hook's outcome become text.

    The outcome is what a hook call returns and, in a ``try`` whose body calls
    a hook, the exception its handler binds. It flows by assignment, by
    unpacking, by iteration and through any call that is not one of the
    stops below; handed to a function of the same file (a bare name, or
    ``self.``/``cls.`` within a class) it also taints the parameter it binds
    there (:attr:`handoffs`), so a helper that renders it is a violation too. It is **stopped** as an argument of :data:`CONSTRUCTOR`, of
    the primitive or a registered wrapper given its values, or of a call that
    reads no text (:data:`_VALUE_FREE_CALLS`). It is **rendered** in an
    f-string, a ``.format``, a ``+`` or ``%``, a rendering call
    (:data:`_RENDERING_CALLS`), an exception's arguments, a ``raise``, a
    ``return`` or ``yield``, or a store into an attribute or a container —
    anything that hands its text, or the outcome itself, onward.
    """

    def __init__(
        self,
        function: ast.AST,
        hook_names: set[str],
        *,
        parameters: set[str] | frozenset[str] = frozenset(),
        resolve: Callable[[ast.Call], ast.AST | None] = lambda call: None,
    ) -> None:
        self._function = function
        self._parameters = set(parameters)
        self._resolve = resolve
        #: Each same-file function this one hands the outcome to → the
        #: parameters the outcome binds there. Given *parameters*, the walk
        #: follows only them — the function's own hook calls are walked
        #: separately — and a ``return`` of them is not a render.
        self.handoffs: dict[int, tuple[ast.AST, set[str]]] = {}
        self._parents = _parents(function)
        self._nodes = _own_nodes(function)
        self._hook_names = set(hook_names) | {
            target.id
            for node in self._nodes
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "settings_validator"
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self._hooks = set(hook_names) - {"settings_validator"}

    def _is_hook_call(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name):
            return node.func.id in self._hook_names
        return isinstance(node.func, ast.Attribute) and (
            node.func.attr == "settings_validator" or node.func.attr in self._hooks
        )

    def rendered(self) -> list[int]:
        """The lines at which this function renders a hook's outcome."""

        tainted: set[str] = set(self._parameters)
        # An exception a hook raised is the outcome only inside the handler
        # that binds it: the same name bound by another handler is not.
        handlers: dict[str, list[ast.ExceptHandler]] = {}
        for node in self._nodes:
            if not self._parameters and isinstance(node, ast.Try) and any(
                self._is_hook_call(inner) for statement in node.body for inner in ast.walk(statement)
            ):
                for handler in node.handlers:
                    if handler.name:
                        handlers.setdefault(handler.name, []).append(handler)
        lines: set[int] = set()
        while True:
            before = len(tainted)
            for occurrence in self._nodes:
                if not (
                    (not self._parameters and self._is_hook_call(occurrence))
                    or (
                        isinstance(occurrence, ast.Name)
                        and isinstance(occurrence.ctx, ast.Load)
                        and (
                            occurrence.id in tainted
                            or self._inside(occurrence, handlers.get(occurrence.id, ()))
                        )
                    )
                ):
                    continue
                verdict, names = self._follow(occurrence)
                if verdict == "rendered":
                    lines.add(occurrence.lineno)
                tainted.update(names)
            if len(tainted) == before:
                return sorted(lines)

    def _inside(self, node: ast.AST, scopes: Iterable[ast.AST]) -> bool:
        ids = {id(scope) for scope in scopes}
        while node is not None and node is not self._function:
            if id(node) in ids:
                return True
            node = self._parents.get(id(node))
        return False

    def _follow(self, occurrence: ast.AST) -> tuple[str, set[str]]:
        node = occurrence
        while node is not self._function:
            parent = self._parents.get(id(node))
            if parent is None:
                return "dropped", set()
            if isinstance(parent, ast.keyword):
                node, parent = parent, self._parents.get(id(parent))
            if isinstance(parent, ast.Call) and node is not parent.func:
                name = _called_name(parent) or ""
                if name == CONSTRUCTOR or _routes(parent) or name in _VALUE_FREE_CALLS:
                    return "stopped", set()
                if name in _RENDERING_CALLS or name.endswith(("Error", "Exception", "Warning")):
                    return "rendered", set()
                self._hand_off(parent, node)
            elif isinstance(parent, (ast.FormattedValue, ast.JoinedStr)):
                return "rendered", set()
            elif isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Add, ast.Mod)):
                return "rendered", set()
            elif isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom)) and self._parameters:
                # A helper's result goes back to the caller that handed the
                # outcome in, which follows the call onward itself.
                return "returned", set()
            elif isinstance(parent, (ast.Raise, ast.Return, ast.Yield, ast.YieldFrom)):
                return "rendered", set()
            elif isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                if node is not parent.value:
                    return "dropped", set()
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                if any(not isinstance(t, (ast.Name, ast.Tuple, ast.List, ast.Starred)) for t in targets):
                    return "rendered", set()
                return "assigned", _bound_names(targets)
            elif isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)):
                if node is parent.iter:
                    return "assigned", _bound_names([parent.target])
                return "dropped", set()
            elif isinstance(parent, ast.stmt):
                return "dropped", set()
            node = parent
        return "dropped", set()


    def _hand_off(self, call: ast.Call, argument: ast.AST) -> None:
        """Taint the parameters *argument* binds in a same-file callee."""

        callee = self._resolve(call)
        if callee is None:
            return
        arguments = callee.args
        positional = [arg.arg for arg in (*arguments.posonlyargs, *arguments.args)]
        if isinstance(call.func, ast.Attribute):
            positional = positional[1:]
        bound: set[str] = set()
        if isinstance(argument, ast.keyword):
            if argument.arg is None:
                bound.update(positional, (arg.arg for arg in arguments.kwonlyargs))
                if arguments.kwarg:
                    bound.add(arguments.kwarg.arg)
            elif argument.arg in positional or argument.arg in {
                arg.arg for arg in arguments.kwonlyargs
            }:
                bound.add(argument.arg)
            elif arguments.kwarg:
                bound.add(arguments.kwarg.arg)
        else:
            index = next(i for i, arg in enumerate(call.args) if arg is argument)
            if any(isinstance(arg, ast.Starred) for arg in call.args[: index + 1]):
                bound.update(positional[index:])
                if arguments.vararg:
                    bound.add(arguments.vararg.arg)
            elif index < len(positional):
                bound.add(positional[index])
            elif arguments.vararg:
                bound.add(arguments.vararg.arg)
        if bound:
            _, names = self.handoffs.setdefault(id(callee), (callee, set()))
            names.update(bound)


def _bound_names(targets: list[ast.AST]) -> set[str]:
    names: set[str] = set()
    for target in targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def hook_outcome_renderers(
    root: Path,
    *,
    unreported: dict[tuple[str, str], str] = HOOK_OUTCOME_UNREPORTED,
) -> dict[tuple[str, str], list[int]]:
    """Every function of ``core/`` and ``modules/`` that renders a settings
    hook's outcome — its field path, its reason or its ``str()`` — anywhere
    but :data:`CONSTRUCTOR` (R3, F4)."""

    hooks = _settings_hooks(root)
    found: dict[tuple[str, str], list[int]] = {}
    for tree_name in _SOURCE_TREES:
        for path in sorted((root / tree_name).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names = _hook_names(tree, relative, hooks)
            functions = _qualified_functions(tree)
            by_name = dict(functions)
            parameters: dict[int, set[str]] = {id(d): set() for _, d in functions}
            while True:
                lines_of: dict[str, list[int]] = {}
                grew = False
                for name, definition in functions:
                    if (relative, name) == _CONSTRUCTOR_HOME or (relative, name) in unreported:
                        continue
                    resolve = _same_file_callee(name, by_name)
                    walks = [_HookOutcome(definition, names, resolve=resolve)]
                    if parameters[id(definition)]:
                        walks.append(
                            _HookOutcome(
                                definition,
                                names,
                                parameters=parameters[id(definition)],
                                resolve=resolve,
                            )
                        )
                    lines_of[name] = sorted({line for walk in walks for line in walk.rendered()})
                    for walk in walks:
                        for callee, bound in walk.handoffs.values():
                            if not bound <= parameters[id(callee)]:
                                parameters[id(callee)] |= bound
                                grew = True
                if not grew:
                    break
            found.update(
                ((relative, name), lines) for name, lines in lines_of.items() if lines
            )
    return found


def _same_file_callee(
    caller: str, functions: dict[str, ast.AST]
) -> Callable[[ast.Call], ast.AST | None]:
    """Resolve a call in *caller* to a function of the same file: a bare name
    to a module-level function, ``self.m``/``cls.m`` to a method of the
    caller's class."""

    owner = caller.rpartition(".")[0]

    def resolve(call: ast.Call) -> ast.AST | None:
        if isinstance(call.func, ast.Name):
            return functions.get(call.func.id)
        if (
            owner
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"self", "cls"}
        ):
            return functions.get(f"{owner}.{call.func.attr}")
        return None

    return resolve


def _qualified_functions(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Every function of *tree* by its dotted name (``_Settings.from_mapping``)."""

    found: list[tuple[str, ast.AST]] = []
    pending: list[tuple[str, ast.AST]] = [("", tree)]
    while pending:
        prefix, scope = pending.pop()
        for node in ast.iter_child_nodes(scope):
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                name = prefix + node.name
                if isinstance(node, _FUNCTIONS):
                    found.append((name, node))
                pending.append((name + ".", node))
    return found


# --------------------------------------------------------------------------- #
# The shipped tree
# --------------------------------------------------------------------------- #


def test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive() -> None:
    """AC11 (R3, F4): every function of ``core/`` and ``modules/`` that names a
    configured key in a diagnostic routes it through :func:`mask_known` —
    directly, by a registered wrapper, behind a registered guard, or as a
    settings hook the loader's guard reports — and no function renders a
    settings hook's outcome except the one constructor (F4, probes C and D)."""

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
    # F4, N1: no reporter renders a settings hook's outcome but the constructor.
    assert hook_outcome_renderers(_ROOT) == {}


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
    # Every hook-outcome exemption is still a renderer, and the constructor
    # the check trusts is defined once, where the exemption says it is.
    renderers = hook_outcome_renderers(_ROOT, unreported={})
    assert [entry for entry in HOOK_OUTCOME_UNREPORTED if entry not in renderers] == []
    constructors = [
        path.relative_to(_ROOT).as_posix()
        for tree_name in _SOURCE_TREES
        for path in (_ROOT / tree_name).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, _FUNCTIONS) and node.name == CONSTRUCTOR
    ]
    assert constructors == [_CONSTRUCTOR_HOME[0]]


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
    # Probe D of the gate's first pass (F4, N1): the same raw surface in the
    # spellings the per-function heuristic let through.
    "probe-d-format": (
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ValueError('settings.{}: unknown'.format(key))\n"
    ),
    "probe-d-concatenation": (
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ValueError('settings.' + key + ': unknown')\n"
    ),
    "probe-d-unrelated-redactor": (
        "def check(settings, known):\n"
        "    safe = mask_known('fixed', known)\n"
        "    for key in settings:\n"
        "        raise ValueError(f'settings.{key}: unknown')\n"
    ),
    "percent": (
        "def check(settings):\n"
        "    for key in settings:\n"
        "        raise ValueError('settings.%s: unknown' % key)\n"
    ),
    "label-then-raw": (
        "def check(settings, known):\n"
        "    for key in settings:\n"
        "        label = f'settings.{key}'\n"
        "        mask_known(label, known)\n"
        "        raise ValueError(label)\n"
    ),
    "helper-wrapper-without-values": (
        "def _report(path):\n"
        "    raise _field_error('m', path, 'unknown')\n"
        "def check(settings):\n"
        "    for key in settings:\n"
        "        _report(f'settings.{key}')\n"
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
        "def check(settings, known):\n"
        "    for key in settings:\n"
        "        raise ValueError(mask_known('settings.{}: unknown'.format(key), known))\n",
        "def _report(path, known):\n"
        "    raise _field_error('m', path, 'unknown', values=known)\n"
        "def check(settings, known):\n"
        "    for key in settings:\n"
        "        label = 'settings.' + key\n"
        "        _report(label, known)\n",
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


# --------------------------------------------------------------------------- #
# Probe D, replayed: the real AC11 test over the real tree plus one fixture
# --------------------------------------------------------------------------- #

_PROBE_D_FIXTURE = "core/_gate_virtual_fixture.py"

_PROBE_D_BODIES = {
    "f-string": "        raise ValueError(f'settings.{key}: unknown')\n",
    "format": "        raise ValueError('settings.{}: unknown'.format(key))\n",
    "concatenation": "        raise ValueError('settings.' + key + ': unknown')\n",
    "unrelated-redactor": (
        "        safe = mask_known('fixed', known)\n"
        "        raise ValueError(f'settings.{key}: unknown')\n"
    ),
}


@pytest.fixture(scope="module")
def real_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the shipped ``core/`` and ``modules/`` the fixture is added to."""

    root = tmp_path_factory.mktemp("real-tree")
    for tree_name in _SOURCE_TREES:
        shutil.copytree(
            _ROOT / tree_name, root / tree_name, ignore=shutil.ignore_patterns("__pycache__")
        )
    return root


def _with_probe_d_fixture(
    root: Path, body: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / _PROBE_D_FIXTURE).write_text(
        "from core.contracts import mask_known\n"
        "def check(settings, known):\n"
        "    for key in settings:\n" + body,
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_ROOT", root)


@pytest.mark.parametrize("case", sorted(_PROBE_D_BODIES))
def test_ac11_probe_d_every_spelling_fails_the_real_structural_test(
    real_tree: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """F4, N1 (probe D): the gate appended a virtual fixture to the real scan
    and ran the unchanged AC11 test. Each of its four bodies is a negative
    control: the test must fail and name the fixture."""

    _with_probe_d_fixture(real_tree, _PROBE_D_BODIES[case], monkeypatch)
    try:
        with pytest.raises(AssertionError):
            test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive()
        assert list(violations(real_tree)) == [(_PROBE_D_FIXTURE, "check")]
    finally:
        (real_tree / _PROBE_D_FIXTURE).unlink()


def test_ac11_probe_d_the_routed_spelling_passes_the_real_structural_test(
    real_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control of probe D: the same fixture, its key path built
    through the primitive, passes the real test."""

    _with_probe_d_fixture(
        real_tree,
        "        raise ValueError(mask_known('settings.' + key, known) + ': unknown')\n",
        monkeypatch,
    )
    try:
        test_ac11_every_configuration_diagnostic_surface_routes_through_the_primitive()
    finally:
        (real_tree / _PROBE_D_FIXTURE).unlink()


# --------------------------------------------------------------------------- #
# Self-test: a hook's outcome rendered outside the constructor fails
# --------------------------------------------------------------------------- #

_HOOK_MODULE = (
    "def validate_settings(settings):\n"
    "    return [f\"module 'fixture': field '{key}': unknown\" for key in settings]\n"
)

_HOOK_RENDERERS = {
    # Probe C's shape: the pre-fix offline command joined the hook's entries.
    "joined": (
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    diagnostics = validate_settings(settings)\n"
        "    if diagnostics:\n"
        "        raise SystemExit('; '.join(diagnostics))\n"
    ),
    "printed-entry": (
        "from modules.fixture import validate_settings as check\n"
        "def main(settings):\n"
        "    for line in check(settings):\n"
        "        print(f'error: {line}')\n"
    ),
    "raised-exception-text": (
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    try:\n"
        "        validate_settings(settings)\n"
        "    except Exception as exc:\n"
        "        return 'refused: ' + str(exc)\n"
    ),
    "first-entry-as-exception": (
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    diagnostics = validate_settings(settings)\n"
        "    if diagnostics:\n"
        "        raise ValueError(diagnostics[0])\n"
    ),
    "resolved-attribute": (
        "def main(entry_point, settings):\n"
        "    validator = entry_point.settings_validator\n"
        "    outcome = validator(settings)\n"
        "    return outcome\n"
    ),
    "unrelated-constructor-call": (
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    outcome = validate_settings(settings)\n"
        "    hook_diagnostics('fixture', 'validate_settings', [], settings=settings)\n"
        "    return '; '.join(outcome)\n"
    ),
}


def _hook_tree(tmp_path: Path, consumer: str) -> Path:
    root = _fixture_tree(tmp_path, _HOOK_MODULE, relative="modules/fixture/__init__.py")
    (root / "modules/fixture/module.yaml").write_text(
        "settings_validator: validate_settings\n", encoding="utf-8"
    )
    (root / "modules/fixture/__main__.py").write_text(consumer, encoding="utf-8")
    return root


@pytest.mark.parametrize("case", sorted(_HOOK_RENDERERS))
def test_f4_self_test_a_hook_outcome_rendered_outside_the_constructor_fails(
    tmp_path: Path, case: str
) -> None:
    """F4 (R3): a reporter that renders a settings hook's entries, its field,
    its reason or what it raised — joined, interpolated, concatenated, raised,
    returned onward — is a violation naming it."""

    root = _hook_tree(tmp_path, _HOOK_RENDERERS[case])

    assert list(hook_outcome_renderers(root)) == [("modules/fixture/__main__.py", "main")]


_HELPER_RENDERERS = {
    # The review's shape: the outcome handed to a helper that prints it.
    "helper-printed": (
        "from modules.fixture import validate_settings\n"
        "def report(lines):\n"
        "    print('; '.join(lines))\n"
        "def main(settings):\n"
        "    report(validate_settings(settings))\n",
        "report",
    ),
    "helper-by-keyword-through-a-name": (
        "from modules.fixture import validate_settings\n"
        "def _report(prefix, *, lines):\n"
        "    for line in lines:\n"
        "        print(prefix + line)\n"
        "def main(settings):\n"
        "    outcome = validate_settings(settings)\n"
        "    _report('error: ', lines=outcome)\n",
        "_report",
    ),
    "helper-of-a-helper": (
        "from modules.fixture import validate_settings\n"
        "def _emit(text):\n"
        "    raise SystemExit(text)\n"
        "def _relay(entries):\n"
        "    _emit(entries)\n"
        "def main(settings):\n"
        "    _relay(validate_settings(settings))\n",
        "_emit",
    ),
    "method-on-self": (
        "from modules.fixture import validate_settings\n"
        "class Command:\n"
        "    def _report(self, lines):\n"
        "        return f'refused: {lines}'\n"
        "    def run(self, settings):\n"
        "        self._report(validate_settings(settings))\n",
        "Command._report",
    ),
}


@pytest.mark.parametrize("case", sorted(_HELPER_RENDERERS))
def test_f4_self_test_a_hook_outcome_rendered_by_a_same_file_helper_fails(
    tmp_path: Path, case: str
) -> None:
    """F4 (R3): handing the outcome to a helper of the same file does not
    hide it — the helper that renders it is the violation."""

    consumer, renderer = _HELPER_RENDERERS[case]

    assert list(hook_outcome_renderers(_hook_tree(tmp_path, consumer))) == [
        ("modules/fixture/__main__.py", renderer)
    ]


@pytest.mark.parametrize(
    "consumer",
    [
        # A helper given the outcome that only counts it, and one that is
        # given only an unrelated argument.
        "from modules.fixture import validate_settings\n"
        "def report(lines, label):\n"
        "    print(f'{label}: {len(lines)} refused')\n"
        "def main(settings):\n"
        "    report(validate_settings(settings), 'fixture')\n",
        # An exception bound under the same name by a handler whose try calls
        # no hook is not the hook's outcome.
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    try:\n"
        "        int(settings)\n"
        "    except ValueError as exc:\n"
        "        print(exc)\n"
        "    try:\n"
        "        outcome = validate_settings(settings)\n"
        "    except Exception as exc:\n"
        "        outcome = exc\n"
        "    return len(outcome)\n",
        # The shipped offline command's shape: the constructor renders.
        "from modules.fixture import validate_settings\n"
        "def main(settings, config):\n"
        "    try:\n"
        "        outcome = validate_settings(settings)\n"
        "    except Exception as exc:\n"
        "        outcome = exc\n"
        "    diagnostics = hook_diagnostics('fixture', 'validate_settings', outcome,\n"
        "                                   settings=settings, profile=config)\n"
        "    if diagnostics:\n"
        "        raise SystemExit('; '.join(diagnostics))\n",
        # An activation that counts the refusals and never shows one.
        "from modules.fixture import validate_settings\n"
        "def main(settings):\n"
        "    diagnostics = validate_settings(settings)\n"
        "    if diagnostics:\n"
        "        raise ValueError(f'settings were refused ({len(diagnostics)} diagnostics)')\n",
    ],
)
def test_f4_self_test_a_hook_outcome_through_the_constructor_passes(
    tmp_path: Path, consumer: str
) -> None:
    """Positive control: an outcome handed only to the constructor, or only
    counted, is not rendered."""

    assert hook_outcome_renderers(_hook_tree(tmp_path, consumer)) == {}


# --------------------------------------------------------------------------- #
# Probe C: the offline settings consumer prints nothing unredacted
# --------------------------------------------------------------------------- #

_PROBE_C_SECRET = "review-secret-4521"

_PROBE_C_DOCUMENT = (
    "modules:\n"
    "  streamerbot:\n"
    f"    password: {_PROBE_C_SECRET}\n"
    "  viewer_memory:\n"
    "    directory: /tmp/phase6-unused-memory\n"
    f"    {_PROBE_C_SECRET}: 1\n"
)


def test_f4_probe_c_the_offline_command_never_echoes_a_credential_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F4 (probe C): another module's credential, used as an unknown key of
    ``viewer_memory``, is refused with a non-zero exit and appears nowhere in
    stdout or stderr — the in-memory document is the gate's, no file is read."""

    from modules.viewer_memory.__main__ import main

    read: list[Path] = []

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        read.append(self)
        return _PROBE_C_DOCUMENT

    monkeypatch.setattr(Path, "read_text", read_text)

    status = main(["stats", "--config", "/virtual/gate.yaml"])

    captured = capsys.readouterr()
    assert read == [Path("/virtual/gate.yaml")]
    assert status != 0
    assert all(
        _PROBE_C_SECRET[:end] not in captured.out + captured.err
        for end in range(8, len(_PROBE_C_SECRET) + 1)
    )
    assert captured.err == (
        "viewer_memory: module 'viewer_memory': field 'validate_settings': "
        "is not a setting this module declares\n"
    )
