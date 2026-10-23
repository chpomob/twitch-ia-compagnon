"""The first-run wizard: its ready-made profiles and their generator (R3, plan DD5).

A ready-made profile is one JSON file under ``profiles/`` (package data): the
string-table keys of its name and description, the parameters the streamer
supplies, the identities the platform's validation answer supplies, the
credential references it needs, the platform scopes and endpoints the
validators read (data, so this module names no platform — plan DD1), the
dry run's fake event kind and expected outcome, and a template of the
profile document.

The template is structural, never text: a slot is a whole string —
``"$parameter:<name>"``, ``"$identity:<name>"`` or ``"$credential:<NAME>"`` —
used as a value or as a mapping key, and :func:`generate_profile` replaces it
by the supplied value (any JSON type, so a list or a mapping stays one), the
supplied identity, or the ``${NAME}`` reference. A credential therefore only
ever appears as its reference, every reference is listed under ``secrets``,
and no supplied value may itself carry a ``${`` (the loader would resolve it).
A credential that lives inside a mapping parameter (an HTTP detector's
optional key) names that parameter instead of a slot: it is written, as its
reference, into the supplied mapping only when the streamer supplies it, and
a literal credential in the mapping stays refused.
:func:`render_profile_yaml` dumps the document; nothing here writes a file.

A platform input with every trigger off is expressed as a configured policy
that never matches (a ``probability`` rule of 0): no input declares an
explicit "off" policy, and the input stays connected.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: The shipped profiles directory (package data).
PROFILES_DIRECTORY = Path(__file__).resolve().parent / "profiles"

#: The profile file format this module reads.
PROFILE_FORMAT = 1

#: The parameter kinds a profile may declare.
PARAMETER_KINDS = frozenset({"text", "argv", "detector"})

#: The screens a parameter or a credential is collected on.
SCREENS = frozenset({"choice", "platform", "model"})

#: The dry-run outcomes a profile may expect (R5 screen 5).
DRY_RUN_OUTCOMES = frozenset({"completed", "not_triggered"})

#: Setting names that hold a credential inside a parameter value (the
#: detector's optional key): refused as a literal there, a credential is a
#: reference a parameter credential writes.
_CREDENTIAL_KEYS = frozenset({"api_key"})

_SLOT = re.compile(r"\$(parameter|identity|credential):(.+)")
_REFERENCE_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_PROFILE_KEYS = frozenset(
    {
        "format",
        "id",
        "order",
        "name_key",
        "description_key",
        "model_needed",
        "parameters",
        "identities",
        "credentials",
        "platform",
        "dry_run",
        "template",
        "note",
    }
)


class ProfileError(ValueError):
    """A profile file is malformed, or a generation input is refused.

    The message names the profile and the field, never a supplied value.
    """


@dataclass(frozen=True, slots=True)
class Parameter:
    """One value the streamer supplies, on the screen it is collected on."""

    name: str
    kind: str
    screen: str
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Credential:
    """One credential reference and the setting it fills.

    With *parameter* set, the setting lies inside that mapping parameter's
    value and the credential is optional: written only when supplied.
    """

    name: str
    module: str
    setting: str
    screen: str
    parameter: str | None = None

    @property
    def reference(self) -> str:
        return "${" + self.name + "}"


@dataclass(frozen=True, slots=True)
class Profile:
    """A ready-made profile, as loaded from its file (read-only)."""

    id: str
    order: int
    name_key: str
    description_key: str
    model_needed: bool
    parameters: tuple[Parameter, ...]
    identities: tuple[str, ...]
    credentials: tuple[Credential, ...]
    scopes: tuple[str, ...]
    platform_module: str
    validation_endpoint: str
    reward_list_endpoint: str
    dry_run_event_kind: str
    dry_run_outcome: str
    template: Mapping[str, Any]

    def parameter(self, name: str) -> Parameter | None:
        return next((item for item in self.parameters if item.name == name), None)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_profiles(directory: str | Path = PROFILES_DIRECTORY) -> tuple[Profile, ...]:
    """Every ``*.json`` profile of *directory*, in their declared order.

    Each file is validated as a whole — its stem is its ``id``, every slot of
    its template names a declared parameter, identity or credential, every
    declared one is used, and the template holds no ``${`` of its own — and a
    duplicate ``id`` or ``order`` is refused, so the order is unambiguous.
    """

    profiles: list[Profile] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise ProfileError(f"profile {path.stem!r}: is not readable JSON") from None
        profiles.append(_profile(path.stem, data))
    for attribute in ("id", "order"):
        values = [getattr(profile, attribute) for profile in profiles]
        if len(set(values)) != len(values):
            raise ProfileError(f"profiles: field {attribute!r} repeats")
    return tuple(sorted(profiles, key=lambda profile: profile.order))


def _profile(stem: str, data: Any) -> Profile:
    label = f"profile {stem!r}"
    if not isinstance(data, Mapping):
        raise ProfileError(f"{label}: must be a JSON object")
    if not set(data) <= _PROFILE_KEYS:
        raise ProfileError(f"{label}: holds a field the profile format does not declare")
    if data.get("format") != PROFILE_FORMAT:
        raise ProfileError(f"{label}: field 'format' must be {PROFILE_FORMAT}")
    if data.get("id") != stem:
        raise ProfileError(f"{label}: field 'id' must be the file name")
    order = data.get("order")
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        raise ProfileError(f"{label}: field 'order' must be a positive integer")
    model_needed = data.get("model_needed")
    if not isinstance(model_needed, bool):
        raise ProfileError(f"{label}: field 'model_needed' must be a boolean")

    parameters = tuple(
        _parameter(label, index, entry)
        for index, entry in enumerate(_list(label, data, "parameters"))
    )
    identities = tuple(
        _text(label, f"identities[{index}]", entry)
        for index, entry in enumerate(_list(label, data, "identities"))
    )
    credentials = tuple(
        _credential(label, index, entry)
        for index, entry in enumerate(_list(label, data, "credentials"))
    )
    for field_name, names in (
        ("parameters", [item.name for item in parameters]),
        ("identities", list(identities)),
        ("credentials", [item.name for item in credentials]),
    ):
        if len(set(names)) != len(names):
            raise ProfileError(f"{label}: field {field_name!r} repeats a name")

    platform = data.get("platform")
    if not isinstance(platform, Mapping):
        raise ProfileError(f"{label}: field 'platform' must be an object")
    scopes = tuple(
        _text(label, f"platform.scopes[{index}]", entry)
        for index, entry in enumerate(_list(label, platform, "scopes", "platform."))
    )
    endpoints = {
        key: _text(label, f"platform.{key}", platform.get(key))
        for key in ("module", "validation_endpoint", "reward_list_endpoint")
    }
    for key in ("validation_endpoint", "reward_list_endpoint"):
        if not endpoints[key].startswith("https://"):
            raise ProfileError(f"{label}: field 'platform.{key}' must be an https URL")

    dry_run = data.get("dry_run")
    if not isinstance(dry_run, Mapping) or set(dry_run) != {"event_kind", "expected_outcome"}:
        raise ProfileError(
            f"{label}: field 'dry_run' must hold exactly event_kind and expected_outcome"
        )
    event_kind = _text(label, "dry_run.event_kind", dry_run["event_kind"])
    outcome = dry_run["expected_outcome"]
    if outcome not in DRY_RUN_OUTCOMES:
        raise ProfileError(
            f"{label}: field 'dry_run.expected_outcome' must be one of "
            f"{', '.join(sorted(DRY_RUN_OUTCOMES))}"
        )

    template = data.get("template")
    if not isinstance(template, Mapping):
        raise ProfileError(f"{label}: field 'template' must be an object")
    if "secrets" in template:
        raise ProfileError(f"{label}: field 'template.secrets' is generated, never declared")
    used: dict[str, set[str]] = {"parameter": set(), "identity": set(), "credential": set()}
    _collect_slots(label, template, used)
    declared = {
        "parameter": {item.name for item in parameters},
        "identity": set(identities),
        "credential": {item.name for item in credentials if item.parameter is None},
    }
    for kind, names in declared.items():
        if used[kind] - names:
            raise ProfileError(
                f"{label}: template uses undeclared {kind} slots "
                f"{', '.join(sorted(used[kind] - names))}"
            )
        if names - used[kind]:
            raise ProfileError(
                f"{label}: declared {kind} slots unused by the template: "
                f"{', '.join(sorted(names - used[kind]))}"
            )
    by_name = {item.name: item for item in parameters}
    for credential in credentials:
        path = ("modules", credential.module, *credential.setting.split("."))
        if credential.parameter is not None:
            parameter = by_name.get(credential.parameter)
            if (
                parameter is None
                or parameter.kind != "detector"
                or path[-1] not in _CREDENTIAL_KEYS
                or _at(template, path[:-1]) != f"$parameter:{credential.parameter}"
            ):
                raise ProfileError(
                    f"{label}: credential {credential.name} does not fill "
                    f"modules.{credential.module}.{credential.setting} inside a mapping parameter"
                )
            continue
        if _at(template, path) != f"$credential:{credential.name}":
            raise ProfileError(
                f"{label}: credential {credential.name} does not fill "
                f"modules.{credential.module}.{credential.setting}"
            )

    return Profile(
        id=stem,
        order=order,
        name_key=_text(label, "name_key", data.get("name_key")),
        description_key=_text(label, "description_key", data.get("description_key")),
        model_needed=model_needed,
        parameters=parameters,
        identities=identities,
        credentials=credentials,
        scopes=scopes,
        platform_module=endpoints["module"],
        validation_endpoint=endpoints["validation_endpoint"],
        reward_list_endpoint=endpoints["reward_list_endpoint"],
        dry_run_event_kind=event_kind,
        dry_run_outcome=outcome,
        template=_read_only(template),
    )


def _list(label: str, data: Mapping[str, Any], key: str, prefix: str = "") -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ProfileError(f"{label}: field '{prefix}{key}' must be a list")
    return value


def _text(label: str, field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{label}: field {field_name!r} must be a non-empty string")
    return value


def _parameter(label: str, index: int, entry: Any) -> Parameter:
    field_name = f"parameters[{index}]"
    if not isinstance(entry, Mapping) or not set(entry) <= {"name", "kind", "screen", "source"}:
        raise ProfileError(f"{label}: field {field_name!r} must be a parameter object")
    kind = entry.get("kind")
    if kind not in PARAMETER_KINDS:
        raise ProfileError(f"{label}: field '{field_name}.kind' is not a known kind")
    screen = entry.get("screen")
    if screen not in SCREENS:
        raise ProfileError(f"{label}: field '{field_name}.screen' is not a known screen")
    source = entry.get("source")
    if source is not None:
        _text(label, f"{field_name}.source", source)
    return Parameter(_text(label, f"{field_name}.name", entry.get("name")), kind, screen, source)


def _credential(label: str, index: int, entry: Any) -> Credential:
    field_name = f"credentials[{index}]"
    required = {"name", "module", "setting", "screen"}
    if not isinstance(entry, Mapping) or not required <= set(entry) <= required | {"parameter"}:
        raise ProfileError(f"{label}: field {field_name!r} must be a credential object")
    parameter = entry.get("parameter")
    if parameter is not None:
        _text(label, f"{field_name}.parameter", parameter)
    name = _text(label, f"{field_name}.name", entry["name"])
    if not _REFERENCE_NAME.fullmatch(name):
        raise ProfileError(f"{label}: field '{field_name}.name' must be an environment name")
    if entry["screen"] not in SCREENS:
        raise ProfileError(f"{label}: field '{field_name}.screen' is not a known screen")
    return Credential(
        name,
        _text(label, f"{field_name}.module", entry["module"]),
        _text(label, f"{field_name}.setting", entry["setting"]),
        entry["screen"],
        parameter,
    )


def _collect_slots(label: str, value: Any, used: dict[str, set[str]]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _collect_slot(label, key, used)
            _collect_slots(label, item, used)
    elif isinstance(value, list):
        for item in value:
            _collect_slots(label, item, used)
    else:
        _collect_slot(label, value, used)


def _collect_slot(label: str, value: Any, used: dict[str, set[str]]) -> None:
    if not isinstance(value, str):
        return
    if "${" in value:
        raise ProfileError(f"{label}: the template holds a reference outside a credential slot")
    match = _SLOT.fullmatch(value)
    if match is not None:
        used[match.group(1)].add(match.group(2))
    elif value.startswith("$"):
        raise ProfileError(f"{label}: the template holds an unknown slot")


def _at(value: Any, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


def _read_only(value: Any) -> Any:
    """A deep copy the caller cannot share with the loaded profile."""

    return json.loads(json.dumps(value))


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def generate_profile(
    profile: Profile,
    parameters: Mapping[str, Any],
    identities: Mapping[str, str],
    credentials: Collection[str] = (),
) -> dict[str, Any]:
    """The profile document *profile* yields for these inputs (R3).

    *parameters* holds exactly the profile's declared parameters, the
    streamer's answers; *identities* exactly its declared identities, taken
    from the platform's validation answer and never typed; *credentials* the
    names of the optional (parameter) credentials the streamer supplied —
    their values are stored apart, like every other credential's. Each value
    is checked for its kind and refused when it carries a ``${`` (the loader
    would resolve it) or, inside a mapping, a literal credential setting.
    Every credential is written only as its ``${NAME}`` reference — an
    optional one into its parameter's mapping — and every reference written
    is listed under ``secrets``. Refusals raise :class:`ProfileError` naming
    the input, never its value.
    """

    label = f"profile {profile.id!r}"
    _exact(label, "parameters", parameters, [item.name for item in profile.parameters])
    _exact(label, "identities", identities, list(profile.identities))
    values: dict[str, dict[str, Any]] = {"parameter": {}, "identity": {}, "credential": {}}
    for parameter in profile.parameters:
        values["parameter"][parameter.name] = _parameter_value(
            label, parameter, parameters[parameter.name]
        )
    for name in profile.identities:
        value = identities[name]
        if not isinstance(value, str) or not value.strip() or "${" in value:
            raise ProfileError(f"{label}: identity {name!r} must be a non-empty plain string")
        values["identity"][name] = value
    optional = {item.name for item in profile.credentials if item.parameter is not None}
    if isinstance(credentials, (str, bytes)) or not all(
        isinstance(name, str) and name in optional for name in credentials
    ):
        # A name that is not an optional credential is typed input: never echoed.
        raise ProfileError(f"{label}: credentials hold a name that is not an optional credential")
    written = []
    for credential in profile.credentials:
        if credential.parameter is None:
            values["credential"][credential.name] = credential.reference
        elif credential.name in credentials:
            key = credential.setting.rsplit(".", 1)[-1]
            values["parameter"][credential.parameter][key] = credential.reference
        else:
            continue
        written.append(credential.reference)

    document = _substitute(profile.template, values)
    document["secrets"] = written
    return document


def _exact(label: str, field_name: str, supplied: Any, declared: list[str]) -> None:
    if not isinstance(supplied, Mapping):
        raise ProfileError(f"{label}: {field_name} must be a mapping")
    for name in declared:
        if name not in supplied:
            raise ProfileError(f"{label}: {field_name} missing {name!r}")
    # A supplied name that is not declared is never echoed: it is typed input.
    if any(name not in declared for name in supplied):
        raise ProfileError(f"{label}: {field_name} hold a name the profile does not declare")


def _parameter_value(label: str, parameter: Parameter, value: Any) -> Any:
    field_name = f"{label}: parameter {parameter.name!r}"
    if parameter.kind == "text":
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"{field_name} must be a non-empty string")
    elif parameter.kind == "argv":
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise ProfileError(f"{field_name} must be a non-empty list of non-empty strings")
    elif not isinstance(value, Mapping):
        # A detector's own fields are its module's to check (`--check-config`
        # reports a malformed one); only its shape as a mapping is required.
        raise ProfileError(f"{field_name} must be a mapping")
    _plain(field_name, value)
    return copy.deepcopy(value)


def _plain(field_name: str, value: Any) -> None:
    """Refuse a ``${`` anywhere, a credential key in a mapping, and any
    value that is not JSON data."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProfileError(f"{field_name} must use string keys")
            if key in _CREDENTIAL_KEYS:
                raise ProfileError(
                    f"{field_name} holds the credential {key!r}: a credential is a "
                    "reference, never a parameter"
                )
            _plain(field_name, key)
            _plain(field_name, item)
    elif isinstance(value, list):
        for item in value:
            _plain(field_name, item)
    elif isinstance(value, str):
        if "${" in value:
            raise ProfileError(f"{field_name} must not hold an environment reference")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ProfileError(f"{field_name} must be JSON data")


def _substitute(value: Any, values: Mapping[str, Mapping[str, Any]]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = _substitute(key, values)
            if not isinstance(name, str):
                raise ProfileError("template: a mapping key slot must receive a string")
            result[name] = _substitute(item, values)
        return result
    if isinstance(value, list):
        return [_substitute(item, values) for item in value]
    if isinstance(value, str):
        match = _SLOT.fullmatch(value)
        if match is not None:
            return copy.deepcopy(values[match.group(1)][match.group(2)])
    return value


def render_profile_yaml(document: Mapping[str, Any]) -> str:
    """*document* as the YAML profile the wizard writes (block style, the
    template's key order kept); it loads back to an equal document."""

    header = (
        "# Written by the first-run wizard. Every credential is an environment\n"
        "# reference whose value is kept in the per-user credential file, never here.\n"
    )
    return header + yaml.safe_dump(
        dict(document), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
