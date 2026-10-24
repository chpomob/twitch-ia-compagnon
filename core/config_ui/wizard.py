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

:class:`CredentialStore` keeps the collected values in the per-user
credential file (A1, R4) and :class:`WizardState` keeps the wizard's
progress beside the profile (A7); both replace their file atomically from a
temporary created owner-only, and the state records the digest of every file
the wizard wrote, so only those files are ever read back or replaced. The
credential file's ownership check and replacement hold a lock on its
directory, and a file absent at the check is published exclusively, so a
concurrent wizard never overwrites one it did not see.

The validators of screens 2 and 3 (R5, D3) — :func:`validate_platform`,
:func:`list_rewards` and :func:`validate_model` — each make one request
through an injected :class:`Transport` under their own bound on an injected
clock, read the endpoints from the chosen profile's data, and end with a
success or a value-free :class:`Failure` token that :func:`failure_sentences`
turns into a string-table cause and next action. Nothing they do is logged,
and no exception text or submitted value survives into a result.
:func:`aiohttp_transport` is the production transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import os
import re
import secrets as _secrets
import stat
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlencode

import yaml

from core.config_ui import locations
from core.config_ui.i18n import StringTable

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

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


# --------------------------------------------------------------------------- #
# The wizard's own files: the credential store (A1, R4) and the state (A7)
# --------------------------------------------------------------------------- #

#: The suffix appended to a profile's full file name to name its state file.
STATE_SUFFIX = ".wizard.json"

#: The state file format this module reads and writes.
STATE_FORMAT = 1

#: The only screen whose pass outlives its process (its inputs are
#: non-secret); every later screen is passed again after a restart (A7).
PERSISTED_SCREEN = 1

#: The five screens, in order (R5).
SCREEN_NUMBERS = (1, 2, 3, 4, 5)

#: Why the store uses its directory, as a value-free token screen 4 names
#: with the directory (A1): an owner-only directory on POSIX, the per-user
#: local application data the operating system restricts on Windows.
LOCATION_OWNER_ONLY = "owner_only_directory"
LOCATION_LOCAL_APPDATA = "local_appdata"

#: The refusal reasons (value-free tokens; screen 4 translates them).
REFUSAL_NO_DIRECTORY = "credential_directory_unset"
REFUSAL_UNAVAILABLE = "credential_directory_unavailable"
REFUSAL_NOT_OWNED = "credential_directory_not_owned"
REFUSAL_SHARED = "credential_directory_shared"
REFUSAL_NOT_WRITTEN = "file_not_written_by_wizard"
REFUSAL_CHANGED = "file_changed_outside_wizard"

_STATE_KEYS = frozenset(
    {"format", "profile", "parameters", "language", "passed", "written", "completed"}
)


class WizardStateError(ValueError):
    """A state file is malformed, or a save would hold a credential value.

    The message names the file or the field, never a value.
    """


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why the store neither read nor wrote: a value-free reason token and
    the directory or file it concerns (``None`` when the platform names no
    directory at all)."""

    reason: str
    path: Path | None


def file_digest(data: bytes) -> str:
    """The digest the state records for a file's bytes."""

    return hashlib.sha256(data).hexdigest()


def state_path(profile_path: str | Path) -> Path:
    """``<base>.wizard.json``: the base file's full name with the suffix
    appended, as the status record's default is formed (A7)."""

    path = Path(profile_path)
    return path.with_name(path.name + STATE_SUFFIX)


def _key(path: str | Path) -> str:
    return os.fspath(Path(path).absolute())


def _replace_owner_only(path: Path, data: bytes, *, exclusive: bool = False) -> bool:
    """Write *data* to *path* atomically: a temporary beside it created with
    mode 0600 at ``os.open`` (no window where it is wider), then
    ``os.replace``. On any failure the temporary is removed.

    With *exclusive* the file is published only if *path* is still absent
    (``os.link`` on POSIX, ``os.rename`` on Windows, both refusing an
    existing target) and ``False`` is returned, nothing replaced, when it is
    not. A POSIX filesystem without hard links falls back to ``os.replace``:
    the caller holds :func:`_directory_lock`, so no wizard wrote it since.
    """

    temporary = path.with_name(f".{path.name}.{_secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if not exclusive:
            os.replace(temporary, path)
        elif os.name == "nt":
            os.rename(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise
            except OSError:
                os.replace(temporary, path)
            else:
                os.unlink(temporary)
    except FileExistsError:
        _discard(temporary)
        return False
    except BaseException:
        _discard(temporary)
        raise
    return True


def _discard(temporary: Path) -> None:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def _directory_lock(directory: Path) -> Iterator[None]:
    """Hold an exclusive ``flock`` on *directory* itself, so concurrent
    wizards check a credential file's ownership and replace it one at a
    time and no lock file is left beside the credentials. Where ``flock``
    is unavailable (Windows) nothing is held: exclusive publication still
    keeps a second wizard from overwriting a file it did not see."""

    if fcntl is None:
        yield
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


class WizardState:
    """The wizard's progress for one profile, in ``<base>.wizard.json`` (A7).

    It holds the profile choice and its non-secret parameters, the language,
    the digest of each file screen 4 wrote, and ``completed``. Screen passes
    are kept for the current process only: :meth:`save` persists screen 1's
    pass and no other, so a restart resumes the inputs, never a success. The
    file is replaced atomically, owner read/write only on POSIX, and never
    holds a credential value (:meth:`save` refuses one).
    """

    def __init__(self, profile_path: str | Path) -> None:
        self.path = state_path(profile_path)
        self.profile: str | None = None
        self.parameters: dict[str, Any] = {}
        self.language: str | None = None
        self.completed = False
        self.written: dict[str, str] = {}
        self._passed: set[int] = set()
        self.exists = self.path.exists()
        if self.exists:
            self._load()

    def _load(self) -> None:
        label = f"wizard state {os.fspath(self.path)!r}"
        try:
            data = json.loads(self.path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            raise WizardStateError(f"{label}: is not readable JSON") from None
        if not isinstance(data, Mapping) or not set(data) <= _STATE_KEYS:
            raise WizardStateError(f"{label}: is not a wizard state object")
        if data.get("format") != STATE_FORMAT:
            raise WizardStateError(f"{label}: field 'format' must be {STATE_FORMAT}")
        profile, language = data.get("profile"), data.get("language")
        parameters, written = data.get("parameters", {}), data.get("written", {})
        passed, completed = data.get("passed", []), data.get("completed", False)
        if not all(value is None or isinstance(value, str) for value in (profile, language)):
            raise WizardStateError(f"{label}: fields 'profile' and 'language' must be strings")
        if not isinstance(parameters, Mapping):
            raise WizardStateError(f"{label}: field 'parameters' must be an object")
        if not isinstance(written, Mapping) or not all(
            isinstance(digest, str) for digest in written.values()
        ):
            raise WizardStateError(f"{label}: field 'written' must map paths to digests")
        if passed not in ([], [PERSISTED_SCREEN]):
            raise WizardStateError(f"{label}: field 'passed' may only record screen 1")
        if not isinstance(completed, bool):
            raise WizardStateError(f"{label}: field 'completed' must be a boolean")
        self.profile, self.language = profile, language
        self.parameters = copy.deepcopy(dict(parameters))
        self.written = dict(written)
        self._passed = set(passed)
        self.completed = completed

    # -- passes (this process only, beyond screen 1) ------------------------ #

    def passed(self, screen: int) -> bool:
        return screen in self._passed

    def pass_screen(self, screen: int) -> None:
        if screen not in SCREEN_NUMBERS:
            raise ValueError(f"unknown screen {screen!r}")
        self._passed.add(screen)

    def invalidate(self, screen: int) -> None:
        """Forget the pass of *screen* and of every later screen (R5)."""

        self._passed = {passed for passed in self._passed if passed < screen}

    # -- ownership ---------------------------------------------------------- #

    def record(self, path: str | Path) -> None:
        """Record the digest of the bytes *path* holds now (screen 4 wrote it)."""

        with open(path, "rb") as handle:
            self.written[_key(path)] = file_digest(handle.read())

    def owns(self, path: str | Path) -> bool:
        """True only while *path*'s bytes hash to the digest recorded for it.

        A path the state records no digest for is never opened.
        """

        recorded = self.written.get(_key(path))
        if recorded is None:
            return False
        try:
            with open(path, "rb") as handle:
                return file_digest(handle.read()) == recorded
        except OSError:
            return False

    # -- persistence -------------------------------------------------------- #

    def document(self) -> dict[str, Any]:
        return {
            "format": STATE_FORMAT,
            "profile": self.profile,
            "parameters": copy.deepcopy(self.parameters),
            "language": self.language,
            "passed": [PERSISTED_SCREEN] if PERSISTED_SCREEN in self._passed else [],
            "written": dict(sorted(self.written.items())),
            "completed": self.completed,
        }

    def save(
        self, store: CredentialStore | None = None, submitted: Collection[str] = ()
    ) -> None:
        """Replace the state file atomically (0600 on POSIX).

        Refused, with nothing written, when the bytes would contain one of
        *store*'s values or a *submitted* credential value, in plain or
        JSON-escaped form.
        """

        data = (json.dumps(self.document(), indent=2, ensure_ascii=False) + "\n").encode()
        held = set(submitted) | (set(store.values()) if store is not None else set())
        for value in held:
            if not value:
                continue
            forms = {value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1]}
            if any(form.encode() in data for form in forms):
                raise WizardStateError(
                    f"wizard state {os.fspath(self.path)!r}: would hold a credential value"
                )
        _replace_owner_only(self.path, data)
        self.exists = True


class CredentialStore:
    """The per-user credential file of one profile (A1, R4).

    The file is ``<profile-file-name>.credentials.json`` in the per-user
    credential directory and holds only ``{NAME: value}`` for the names the
    profile lists under ``secrets``. Where the platform restricts nothing — on
    POSIX a directory not owned by the user or writable by group or others,
    on Windows ``LOCALAPPDATA`` unset or not an existing directory — nothing
    is written and a :class:`Refusal` says why. An existing file is read or
    replaced only while the profile's :class:`WizardState` owns it; otherwise
    it is neither opened nor written, and the refusal names its path.
    """

    def __init__(
        self, profile_path: str | Path, platform: str, environ: Mapping[str, str]
    ) -> None:
        self.platform = platform
        self.environ = dict(environ)
        self.directory = locations.credential_directory(platform, environ)
        self.path = (
            None
            if self.directory is None
            else self.directory / locations.credential_file_name(profile_path)
        )
        self._values: dict[str, str] = {}

    @property
    def location_reason(self) -> str:
        """Why this directory (a token screen 4 names with it, A1)."""

        return LOCATION_LOCAL_APPDATA if self.platform == locations.WINDOWS else LOCATION_OWNER_ONLY

    def values(self) -> tuple[str, ...]:
        """The values this store wrote or read in this process."""

        return tuple(self._values.values())

    def check_directory(self) -> Refusal | None:
        """Make sure the directory exists and the platform restricts it.

        On POSIX it is created 0700 if absent, then must be a directory owned
        by the user and writable by neither group nor others; on Windows
        ``LOCALAPPDATA`` must name an existing directory (no ACL is read or
        rewritten: the product claims no more than the platform gives).
        """

        if self.platform == locations.WINDOWS:
            local = self.environ.get("LOCALAPPDATA")
            if not local:
                return Refusal(REFUSAL_NO_DIRECTORY, None)
            if not Path(local).is_dir():
                return Refusal(REFUSAL_UNAVAILABLE, Path(local))
            assert self.directory is not None
            try:
                self.directory.mkdir(exist_ok=True)
            except OSError:
                return Refusal(REFUSAL_UNAVAILABLE, self.directory)
            if not self.directory.is_dir():
                return Refusal(REFUSAL_UNAVAILABLE, self.directory)
            return None

        assert self.directory is not None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            status = os.stat(self.directory)
        except OSError:
            return Refusal(REFUSAL_UNAVAILABLE, self.directory)
        if not stat.S_ISDIR(status.st_mode):
            return Refusal(REFUSAL_UNAVAILABLE, self.directory)
        if status.st_uid != os.getuid():
            return Refusal(REFUSAL_NOT_OWNED, self.directory)
        if status.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return Refusal(REFUSAL_SHARED, self.directory)
        return None

    def _ownership(self, state: WizardState) -> Refusal | None:
        assert self.path is not None
        if _key(self.path) not in state.written:
            return Refusal(REFUSAL_NOT_WRITTEN, self.path)
        if not state.owns(self.path):
            return Refusal(REFUSAL_CHANGED, self.path)
        return None

    def write(
        self, values: Mapping[str, str], secrets: Collection[str], state: WizardState
    ) -> Refusal | None:
        """Replace the file with *values* for the names *secrets* lists.

        *secrets* is the profile's ``secrets`` list (``${NAME}`` references,
        or bare names). On success the digest is recorded in *state* (the
        caller saves it) and ``None`` is returned; otherwise the refusal, with
        nothing written anywhere.
        """

        names = {
            entry[2:-1] if entry.startswith("${") and entry.endswith("}") else entry
            for entry in secrets
        }
        held = {name: values[name] for name in sorted(names) if name in values}
        if not all(isinstance(value, str) for value in held.values()):
            raise TypeError("credential values must be strings")
        refusal = self.check_directory()
        if refusal is not None:
            return refusal
        assert self.path is not None and self.directory is not None
        data = (json.dumps(held, indent=2, ensure_ascii=False) + "\n").encode()
        # Two profiles of one file name in different directories share this
        # path: the check and the replacement are one step for every wizard,
        # and a file absent at the check is only ever created, never replaced.
        with _directory_lock(self.directory):
            absent = not os.path.lexists(self.path)
            refusal = None if absent else self._ownership(state)
            if refusal is not None:
                return refusal
            if not _replace_owner_only(self.path, data, exclusive=absent):
                return Refusal(REFUSAL_NOT_WRITTEN, self.path)
        state.written[_key(self.path)] = file_digest(data)
        self._values = held
        return None

    def read(self, state: WizardState) -> dict[str, str] | Refusal:
        """The stored ``{NAME: value}``, read only while *state* owns the file.

        An absent file holds nothing (``{}``); a file the state does not own
        is never opened and the refusal names it.
        """

        refusal = self.check_directory()
        if refusal is not None:
            return refusal
        assert self.path is not None
        if not os.path.lexists(self.path):
            return {}
        recorded = state.written.get(_key(self.path))
        if recorded is None:
            return Refusal(REFUSAL_NOT_WRITTEN, self.path)
        # One read: the bytes hashed are the bytes parsed.
        try:
            with open(self.path, "rb") as handle:
                raw = handle.read()
            if file_digest(raw) != recorded:
                return Refusal(REFUSAL_CHANGED, self.path)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return Refusal(REFUSAL_CHANGED, self.path)
        if not isinstance(data, Mapping) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in data.items()
        ):
            return Refusal(REFUSAL_CHANGED, self.path)
        self._values = dict(data)
        return dict(data)


# --------------------------------------------------------------------------- #
# The validators of screens 2 and 3 (R5, D3) and their failure map
# --------------------------------------------------------------------------- #

#: Each validator's own bound, in seconds (R5).
PLATFORM_TIMEOUT_SECONDS = 10.0
MODEL_TIMEOUT_SECONDS = 30.0

#: The most of an answer's body a validator reads; a longer one is no
#: answer it can trust.
MAX_ANSWER_BYTES = 1 << 20

#: How often, in real seconds, a pending request looks at the injected clock:
#: a fake clock moved past the bound ends the wait within one look.
CLOCK_POLL_SECONDS = 0.05

#: The stage a failure belongs to: the token check, the reward list, the model.
STAGE_PLATFORM = "platform"
STAGE_REWARDS = "rewards"
STAGE_MODEL = "model"

#: The failure tokens (value-free; :func:`failure_sentences` translates them).
CONNECTION_REFUSED = "connection_refused"
TIMEOUT = "timeout"
UNAUTHORIZED = "unauthorized"
CLIENT_ID_MISMATCH = "client_id_mismatch"
MISSING_SCOPE = "missing_scope"
MALFORMED_ANSWER = "malformed_answer"
NO_REWARDS = "no_rewards"
NOT_FOUND = "not_found"
NOT_A_COMPLETION = "not_a_completion"
EMPTY_ANSWER = "empty_answer"

#: The tokens each stage may end with.
PLATFORM_FAILURES = (
    CONNECTION_REFUSED, TIMEOUT, UNAUTHORIZED, CLIENT_ID_MISMATCH, MISSING_SCOPE, MALFORMED_ANSWER,
)
REWARD_FAILURES = (CONNECTION_REFUSED, TIMEOUT, UNAUTHORIZED, MALFORMED_ANSWER, NO_REWARDS)
MODEL_FAILURES = (
    CONNECTION_REFUSED, TIMEOUT, UNAUTHORIZED, NOT_FOUND, NOT_A_COMPLETION, EMPTY_ANSWER,
)

#: The one question the model check asks; any non-empty text answers it.
MODEL_PROBE_PROMPT = "Reply with one short word."
MODEL_PROBE_MAX_TOKENS = 16

# A value a header can carry: printable ASCII without spaces.
_HEADER_VALUE = re.compile(r"[\x21-\x7e]+")


@dataclass(frozen=True, slots=True)
class HttpAnswer:
    """What a transport returns: the status and at most
    :data:`MAX_ANSWER_BYTES` + 1 bytes of the body."""

    status: int
    body: bytes


class Transport(Protocol):
    """One HTTP exchange. *timeout* is the validator's remaining budget, a
    hint the transport may honour; the validator enforces its bound itself.
    A connection failure raises :class:`OSError` (``ConnectionError``), an
    exhausted budget :class:`TimeoutError`."""

    async def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpAnswer: ...


@dataclass(frozen=True, slots=True)
class Failure:
    """Why a validator did not pass: its stage, its token, and the names a
    sentence shows (the missing scope names, profile data, never secrets).
    It never holds an exception text or a submitted value."""

    stage: str
    token: str
    scopes: tuple[str, ...] = ()
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class PlatformValidation:
    """Screen 2's token check: a failure, or the channel the token names.

    On success *identities* fills every identity the profile declares (the
    broadcaster and the bot) with the answer's user id: never typed (R5).
    """

    failure: Failure | None
    login: str | None = None
    user_id: str | None = None
    identities: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def passed(self) -> bool:
        return self.failure is None


@dataclass(frozen=True, slots=True)
class Reward:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class RewardList:
    """The channel's custom rewards (A5), or why they could not be listed."""

    failure: Failure | None
    rewards: tuple[Reward, ...] = ()

    @property
    def passed(self) -> bool:
        return self.failure is None


@dataclass(frozen=True, slots=True)
class ModelValidation:
    """Screen 3's completion check: a failure, an answer, or not needed (A4)."""

    failure: Failure | None
    not_needed: bool = False

    @property
    def passed(self) -> bool:
        return self.failure is None


async def _exchange(
    stage: str,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    transport: Transport,
    clock: Callable[[], float],
    timeout: float,
) -> HttpAnswer | Failure:
    """One request under the validator's own bound on *clock*: an answer
    arriving once *timeout* has elapsed is no answer. Nothing is logged —
    neither the URL nor a header — and no exception text survives."""

    started = clock()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    request = asyncio.ensure_future(
        transport(method, url, headers=headers, body=body, timeout=timeout)
    )
    # The bound holds on both clocks: the injected one, looked at while the
    # request is pending, and the loop's, so a real stall ends too.
    try:
        while not request.done():
            remaining = deadline - loop.time()
            if remaining <= 0 or clock() - started >= timeout:
                request.cancel()
                with contextlib.suppress(BaseException):
                    await request
                return Failure(stage, TIMEOUT, seconds=timeout)
            await asyncio.wait({request}, timeout=min(CLOCK_POLL_SECONDS, remaining))
    except asyncio.CancelledError:
        request.cancel()
        raise
    try:
        answer = request.result()
    except (TimeoutError, asyncio.TimeoutError):
        return Failure(stage, TIMEOUT, seconds=timeout)
    except Exception:
        # Refused, reset, unresolvable or an address no client accepts: the
        # endpoint was not reached. The exception's text is never kept.
        return Failure(stage, CONNECTION_REFUSED)
    if clock() - started >= timeout:
        return Failure(stage, TIMEOUT, seconds=timeout)
    if (
        not isinstance(answer, HttpAnswer)
        or isinstance(answer.status, bool)
        or not isinstance(answer.status, int)
        or not isinstance(answer.body, (bytes, bytearray))
    ):
        return Failure(stage, MALFORMED_ANSWER if stage != STAGE_MODEL else NOT_A_COMPLETION)
    return answer


def _decoded(answer: HttpAnswer) -> Any:
    """The JSON body of *answer*, or ``None`` when it is not one."""

    if len(answer.body) > MAX_ANSWER_BYTES:
        return None
    try:
        return json.loads(bytes(answer.body).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # RecursionError: nesting deep enough to exhaust the decoder.
        return None


def _plain_word(value: Any) -> bool:
    return isinstance(value, str) and _HEADER_VALUE.fullmatch(value) is not None and "${" not in value


async def validate_platform(
    profile: Profile,
    token: str,
    client_id: str,
    *,
    transport: Transport,
    clock: Callable[[], float],
    timeout: float = PLATFORM_TIMEOUT_SECONDS,
) -> PlatformValidation:
    """Screen 2: one GET of the profile's validation endpoint (R5).

    It passes only when the answer is a 200 naming the submitted client id,
    every scope the profile needs, a login and a user id; otherwise one of
    :data:`PLATFORM_FAILURES`. A submitted value no header can carry is
    refused as the endpoint would refuse it, with no request.
    """

    token, client_id = token.strip(), client_id.strip()
    if not _plain_word(token):
        return PlatformValidation(Failure(STAGE_PLATFORM, UNAUTHORIZED))
    answer = await _exchange(
        STAGE_PLATFORM,
        "GET",
        profile.validation_endpoint,
        {"Authorization": f"OAuth {token}"},
        None,
        transport,
        clock,
        timeout,
    )
    if isinstance(answer, Failure):
        return PlatformValidation(answer)
    if answer.status == 401:
        return PlatformValidation(Failure(STAGE_PLATFORM, UNAUTHORIZED))
    data = _decoded(answer) if answer.status == 200 else None
    if (
        not isinstance(data, Mapping)
        or not _plain_word(data.get("login"))
        or not _plain_word(data.get("user_id"))
        or not isinstance(data.get("client_id"), str)
        or not isinstance(data.get("scopes"), list)
        or not all(isinstance(scope, str) for scope in data["scopes"])
    ):
        return PlatformValidation(Failure(STAGE_PLATFORM, MALFORMED_ANSWER))
    if data["client_id"] != client_id:
        return PlatformValidation(Failure(STAGE_PLATFORM, CLIENT_ID_MISMATCH))
    missing = tuple(scope for scope in profile.scopes if scope not in set(data["scopes"]))
    if missing:
        return PlatformValidation(Failure(STAGE_PLATFORM, MISSING_SCOPE, scopes=missing))
    user_id = data["user_id"]
    return PlatformValidation(
        None,
        login=data["login"],
        user_id=user_id,
        identities=MappingProxyType({name: user_id for name in profile.identities}),
    )


async def list_rewards(
    profile: Profile,
    token: str,
    client_id: str,
    user_id: str,
    *,
    transport: Transport,
    clock: Callable[[], float],
    timeout: float = PLATFORM_TIMEOUT_SECONDS,
) -> RewardList:
    """The channel's custom rewards: one bounded GET of the profile's reward
    list endpoint for *user_id* (A5). The credentials travel in headers
    only; the query names the channel's user id, which is not a secret. An
    empty list is :data:`NO_REWARDS`: the reward must be created first."""

    token, client_id = token.strip(), client_id.strip()
    if not (_plain_word(token) and _plain_word(client_id)):
        return RewardList(Failure(STAGE_REWARDS, UNAUTHORIZED))
    if not _plain_word(user_id):
        return RewardList(Failure(STAGE_REWARDS, MALFORMED_ANSWER))
    answer = await _exchange(
        STAGE_REWARDS,
        "GET",
        f"{profile.reward_list_endpoint}?{urlencode({'broadcaster_id': user_id})}",
        {"Authorization": f"Bearer {token}", "Client-Id": client_id},
        None,
        transport,
        clock,
        timeout,
    )
    if isinstance(answer, Failure):
        return RewardList(answer)
    if answer.status == 401:
        return RewardList(Failure(STAGE_REWARDS, UNAUTHORIZED))
    data = _decoded(answer) if answer.status == 200 else None
    entries = data.get("data") if isinstance(data, Mapping) else None
    if not isinstance(entries, list) or not all(
        isinstance(entry, Mapping)
        and isinstance(entry.get("id"), str)
        and entry["id"].strip()
        and isinstance(entry.get("title"), str)
        and entry["title"].strip()
        for entry in entries
    ):
        return RewardList(Failure(STAGE_REWARDS, MALFORMED_ANSWER))
    if not entries:
        return RewardList(Failure(STAGE_REWARDS, NO_REWARDS))
    return RewardList(None, tuple(Reward(entry["id"], entry["title"]) for entry in entries))


async def validate_model(
    endpoint: str,
    model: str,
    api_key: str,
    *,
    transport: Transport,
    clock: Callable[[], float],
    timeout: float = MODEL_TIMEOUT_SECONDS,
    profile: Profile | None = None,
) -> ModelValidation:
    """Screen 3: exactly one chat-completions request to *endpoint* (R5).

    It passes only when the answer is a chat completion whose first choice
    holds non-empty text; otherwise one of :data:`MODEL_FAILURES`. For a
    *profile* that needs no model (A4) it is not needed and no request is
    made. A key no header can carry is refused as the endpoint would refuse
    it, with no request.
    """

    if profile is not None and not profile.model_needed:
        return ModelValidation(None, not_needed=True)
    api_key = api_key.strip()
    if not _plain_word(api_key):
        return ModelValidation(Failure(STAGE_MODEL, UNAUTHORIZED))
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": MODEL_PROBE_PROMPT}],
            "max_tokens": MODEL_PROBE_MAX_TOKENS,
        }
    ).encode()
    answer = await _exchange(
        STAGE_MODEL,
        "POST",
        endpoint.strip(),
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body,
        transport,
        clock,
        timeout,
    )
    if isinstance(answer, Failure):
        return ModelValidation(answer)
    if answer.status in (401, 403):
        return ModelValidation(Failure(STAGE_MODEL, UNAUTHORIZED))
    if answer.status == 404:
        return ModelValidation(Failure(STAGE_MODEL, NOT_FOUND))
    data = _decoded(answer) if 200 <= answer.status < 300 else None
    choices = data.get("choices") if isinstance(data, Mapping) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content", None) if isinstance(message, Mapping) else 0
    if content is not None and not isinstance(content, str):
        return ModelValidation(Failure(STAGE_MODEL, NOT_A_COMPLETION))
    if content is None or not content.strip():
        return ModelValidation(Failure(STAGE_MODEL, EMPTY_ANSWER))
    return ModelValidation(None)


async def aiohttp_transport(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpAnswer:
    """The production :class:`Transport`: one ``aiohttp`` request with no
    redirect followed, its body read up to :data:`MAX_ANSWER_BYTES` + 1.

    The UI runs a validator using it on its socket-free loop; its session —
    and so its real connector — is created here only, never in a test
    (AC33). A client failure becomes a ``ConnectionError`` without its text.
    """

    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.request(
                method, url, headers=dict(headers), data=body, allow_redirects=False
            ) as response:
                received = bytearray()
                while len(received) <= MAX_ANSWER_BYTES:
                    chunk = await response.content.read(65536)
                    if not chunk:
                        break
                    received += chunk
                return HttpAnswer(response.status, bytes(received[: MAX_ANSWER_BYTES + 1]))
    except (TimeoutError, asyncio.TimeoutError):
        # Distinct classes before Python 3.11.
        raise TimeoutError("the validator's bound elapsed") from None
    except (aiohttp.ClientError, ValueError):
        raise ConnectionError("the endpoint was not reached") from None


# Every failure's cause and next action, one literal key pair per token, so
# the AC5 collector sees every key the map uses.
_FAILURE_SENTENCES: Mapping[tuple[str, str], Callable[[StringTable, str, Failure], tuple[str, str]]] = {
    (STAGE_PLATFORM, CONNECTION_REFUSED): lambda table, language, failure: (
        table.sentence(language, "platform.connection_refused"),
        table.sentence(language, "platform.connection_refused.next"),
    ),
    (STAGE_PLATFORM, TIMEOUT): lambda table, language, failure: (
        table.sentence(language, "platform.timeout", seconds=f"{failure.seconds:g}"),
        table.sentence(language, "platform.timeout.next"),
    ),
    (STAGE_PLATFORM, UNAUTHORIZED): lambda table, language, failure: (
        table.sentence(language, "platform.unauthorized"),
        table.sentence(language, "platform.unauthorized.next"),
    ),
    (STAGE_PLATFORM, CLIENT_ID_MISMATCH): lambda table, language, failure: (
        table.sentence(language, "platform.client_id_mismatch"),
        table.sentence(language, "platform.client_id_mismatch.next"),
    ),
    (STAGE_PLATFORM, MISSING_SCOPE): lambda table, language, failure: (
        table.sentence(language, "platform.missing_scope", scopes=", ".join(failure.scopes)),
        table.sentence(language, "platform.missing_scope.next"),
    ),
    (STAGE_PLATFORM, MALFORMED_ANSWER): lambda table, language, failure: (
        table.sentence(language, "platform.malformed_answer"),
        table.sentence(language, "platform.malformed_answer.next"),
    ),
    (STAGE_REWARDS, CONNECTION_REFUSED): lambda table, language, failure: (
        table.sentence(language, "rewards.connection_refused"),
        table.sentence(language, "rewards.connection_refused.next"),
    ),
    (STAGE_REWARDS, TIMEOUT): lambda table, language, failure: (
        table.sentence(language, "rewards.timeout", seconds=f"{failure.seconds:g}"),
        table.sentence(language, "rewards.timeout.next"),
    ),
    (STAGE_REWARDS, UNAUTHORIZED): lambda table, language, failure: (
        table.sentence(language, "rewards.unauthorized"),
        table.sentence(language, "rewards.unauthorized.next"),
    ),
    (STAGE_REWARDS, MALFORMED_ANSWER): lambda table, language, failure: (
        table.sentence(language, "rewards.malformed_answer"),
        table.sentence(language, "rewards.malformed_answer.next"),
    ),
    (STAGE_REWARDS, NO_REWARDS): lambda table, language, failure: (
        table.sentence(language, "rewards.no_rewards"),
        table.sentence(language, "rewards.no_rewards.next"),
    ),
    (STAGE_MODEL, CONNECTION_REFUSED): lambda table, language, failure: (
        table.sentence(language, "model.connection_refused"),
        table.sentence(language, "model.connection_refused.next"),
    ),
    (STAGE_MODEL, TIMEOUT): lambda table, language, failure: (
        table.sentence(language, "model.timeout", seconds=f"{failure.seconds:g}"),
        table.sentence(language, "model.timeout.next"),
    ),
    (STAGE_MODEL, UNAUTHORIZED): lambda table, language, failure: (
        table.sentence(language, "model.unauthorized"),
        table.sentence(language, "model.unauthorized.next"),
    ),
    (STAGE_MODEL, NOT_FOUND): lambda table, language, failure: (
        table.sentence(language, "model.not_found"),
        table.sentence(language, "model.not_found.next"),
    ),
    (STAGE_MODEL, NOT_A_COMPLETION): lambda table, language, failure: (
        table.sentence(language, "model.not_a_completion"),
        table.sentence(language, "model.not_a_completion.next"),
    ),
    (STAGE_MODEL, EMPTY_ANSWER): lambda table, language, failure: (
        table.sentence(language, "model.empty_answer"),
        table.sentence(language, "model.empty_answer.next"),
    ),
}


def failure_sentences(table: StringTable, language: str, failure: Failure) -> tuple[str, str]:
    """The plain-language cause of *failure* and the exact next action, from
    the string table (R5): never an exception text nor a submitted value."""

    return _FAILURE_SENTENCES[(failure.stage, failure.token)](table, language, failure)


def platform_sentence(table: StringTable, language: str, validation: PlatformValidation) -> str:
    """Screen 2's success: the channel the token names (R5)."""

    assert validation.passed and validation.login is not None
    return table.sentence(language, "platform.connected", login=validation.login)


def model_sentence(table: StringTable, language: str, validation: ModelValidation) -> str:
    """Screen 3's success: answered, or not needed (A4)."""

    assert validation.passed
    if validation.not_needed:
        return table.sentence(language, "model.not_needed")
    return table.sentence(language, "model.answered")
