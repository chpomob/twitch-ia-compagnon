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

:class:`Wizard` assembles them into the five screens (R5, A7): small
render/validate pairs over one in-memory fingerprint table, keyed per
process, so a screen opens only once the previous one passed for the
current inputs; screen 4 writes the generated profile and the credential
file — only over files the state owns byte for byte — and runs the
configuration check, and screen 5 completes the wizard only on a dry-run
record naming its run, its outcome and the ``dry_run`` origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import hmac
import html
import json
import os
import re
import secrets as _secrets
import shlex
import stat
import threading
import time
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


# --------------------------------------------------------------------------- #
# The five screens (R5, A7): their gating, their writes and their re-entry
# --------------------------------------------------------------------------- #

#: The routes the wizard's pages link and post to (the console is P13's).
WIZARD_ROUTE = "/wizard"
LANGUAGE_ROUTE = "/language"
CONSOLE_ROUTE = "/console"

#: The screen each declared parameter or credential is collected on.
SCREEN_OF = MappingProxyType({"choice": 1, "platform": 2, "model": 3})

#: The dry run's own bound, in seconds (R5).
DRY_RUN_TIMEOUT_SECONDS = 60.0

#: The origin a dry run's record must carry (``core.main.DRY_RUN_ORIGIN``).
DRY_RUN_ORIGIN = "dry_run"

#: The form field prefixes: a parameter is ``parameter.<profile>.<name>``
#: (each profile's choice parameters sit inside its option on screen 1), a
#: credential ``credential.<NAME>``.
PARAMETER_FIELD = "parameter."
CREDENTIAL_FIELD = "credential."

#: The failure tokens of the wizard's own checks (value-free; each has a
#: string-table cause and next action).
ORDER_REFUSED = "order_refused"
INPUT_MISSING = "input_missing"
INPUT_INVALID = "input_invalid"
PROFILE_UNKNOWN = "profile_unknown"
LANGUAGE_UNKNOWN = "language_unknown"
REWARD_NOT_PICKED = "reward_not_picked"
GENERATION_REFUSED = "generation_refused"
WRITE_FAILED = "write_failed"
CHECK_FAILED = "check_failed"
CHECK_FAILED_AT = "check_failed_at"
DRY_RUN_NO_RECORD = "dry_run_no_record"
DRY_RUN_INCOMPLETE = "dry_run_incomplete"
DRY_RUN_UNEXPECTED = "dry_run_unexpected"
DRY_RUN_TIMED_OUT = "dry_run_timed_out"
STATE_UNREADABLE = "state_unreadable"
#: A form posted with a CSRF token that is no longer the session's (P12F4
#: B6): a stale page, answered with the screen again, never acted on.
PAGE_EXPIRED = "page_expired"

#: Screen 4's refusal when a profile appeared at the target after the UI
#: detected the first run (the file is not the wizard's, A7).
REFUSAL_APPEARED = "profile_appeared"

#: A check diagnostic's field: ``module '<name>': field 'settings.<path>'``.
_DIAGNOSTIC_FIELD = re.compile(r"module '([A-Za-z0-9_]+)': field 'settings\.([A-Za-z0-9_.\[\]-]+)'")

#: The viewer a dry run's chat event is authored by (never the bot).
_DRY_RUN_AUTHOR = MappingProxyType({"id": "dry-run-viewer", "display_name": "Viewer"})
_DRY_RUN_VOICE = MappingProxyType({"id": "voice:streamer", "display_name": "streamer"})

_TIMED_OUT = object()


@dataclass
class WizardSeams:
    """What the wizard reaches outside the process, each injectable (D8):
    the platform's and the model's transports, the dry-run entry
    (``core.main.dry_run`` when ``None``) and the clock every bound reads."""

    platform_transport: Transport = aiohttp_transport
    model_transport: Transport = aiohttp_transport
    dry_runner: Callable[..., Any] | None = None
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class WizardPage:
    """One rendered wizard answer: its status, language, title and body."""

    status: int
    language: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class _Notice:
    """A success, or a cause and its next action: string-table sentences."""

    token: str
    text: str
    next_action: str = ""
    success: bool = False


async def _within(awaitable: Any, clock: Callable[[], float], timeout: float) -> Any:
    """Await *awaitable* under *timeout* on *clock* (and on the loop's clock,
    so a real stall ends too); :data:`_TIMED_OUT` once either passes. An
    answer arriving after the bound is no answer. The awaitable's own
    exception is raised."""

    started = clock()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            remaining = deadline - loop.time()
            if remaining <= 0 or clock() - started >= timeout:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                return _TIMED_OUT
            await asyncio.wait({task}, timeout=min(CLOCK_POLL_SECONDS, remaining))
    except asyncio.CancelledError:
        task.cancel()
        raise
    if clock() - started >= timeout:
        with contextlib.suppress(BaseException):
            task.result()
        return _TIMED_OUT
    return task.result()


def checked_field(diagnostics: Sequence[str]) -> str | None:
    """The profile field path the first check diagnostic names, as
    ``modules.<module>.<setting path>``; ``None`` when none names one. Only
    the path is taken, never the rest of the line."""

    for line in diagnostics:
        match = _DIAGNOSTIC_FIELD.search(line)
        if match is not None:
            return f"modules.{match.group(1)}.{match.group(2)}"
    return None


def dry_run_event(
    profile: Profile, document: Mapping[str, Any], parameters: Mapping[str, Any]
) -> dict[str, Any]:
    """The one fake event of *profile*'s kind the dry run drives (R5 screen 5).

    A schema-2 input event shaped as the input would publish it, on the
    channel the generated *document* names: a chat message mentioning the
    companion name, a redemption of the picked reward, or the speech
    candidate after the wake-word decision (its transcript faked, so no
    process starts). Its platform and source are the profile's data.
    """

    platform = profile.platform_module
    settings = _at(document, ("modules", platform))
    settings = settings if isinstance(settings, Mapping) else {}
    channel = settings.get("broadcaster_id")
    companion = settings.get("companion_name")
    payload: dict[str, Any] = {
        "platform": platform,
        "channel_id": channel if isinstance(channel, str) else "",
        "author": dict(_DRY_RUN_AUTHOR),
        "message_id": f"dry-run-{_secrets.token_hex(6)}",
        "text": f"{companion if isinstance(companion, str) else ''}, are you there?",
    }
    source = platform
    kind = profile.dry_run_event_kind
    if kind == "reward":
        payload["kind"] = kind
        payload["text"] = "dry run"
        payload["reward"] = {"id": str(parameters.get("reward_id", "")), "title": "dry run", "cost": 1}
    elif kind == "speech":
        wake = str(parameters.get("wake_word", ""))
        payload["kind"] = kind
        payload["text"] = f"{wake} are you there"
        payload["wake_word"] = {"detected": True, "phrase": wake}
        payload["author"] = dict(_DRY_RUN_VOICE)
        # The speech source is the enabled module whose settings hold the
        # wake word: data of the profile, never a name typed here.
        modules = document.get("modules")
        source = next(
            (
                name
                for name, value in (modules.items() if isinstance(modules, Mapping) else ())
                if isinstance(value, Mapping) and "wake_word" in value
            ),
            platform,
        )
    elif kind != "message":
        payload["kind"] = kind
    return {
        "type": "channel.chat.message",
        "payload": payload,
        "metadata": {"source": source, "schema_version": 2},
    }


# The sentences the screens show, one literal key per entry so the AC5
# collector sees every key the screens use.
_PROFILE_NAMES: Mapping[str, Callable[[StringTable, str], str]] = {
    "profile.chat_qa.name": lambda table, language: table.sentence(language, "profile.chat_qa.name"),
    "profile.channel_points.name": lambda table, language: table.sentence(
        language, "profile.channel_points.name"
    ),
    "profile.spoken_keyword.name": lambda table, language: table.sentence(
        language, "profile.spoken_keyword.name"
    ),
    "profile.silent.name": lambda table, language: table.sentence(language, "profile.silent.name"),
    "profile.chat_qa.description": lambda table, language: table.sentence(
        language, "profile.chat_qa.description"
    ),
    "profile.channel_points.description": lambda table, language: table.sentence(
        language, "profile.channel_points.description"
    ),
    "profile.spoken_keyword.description": lambda table, language: table.sentence(
        language, "profile.spoken_keyword.description"
    ),
    "profile.silent.description": lambda table, language: table.sentence(
        language, "profile.silent.description"
    ),
}

_PARAMETER_LABELS: Mapping[str, Callable[[StringTable, str], str]] = {
    "companion_name": lambda table, language: table.sentence(language, "wizard.parameter.companion_name"),
    "reward_id": lambda table, language: table.sentence(language, "wizard.parameter.reward_id"),
    "wake_word": lambda table, language: table.sentence(language, "wizard.parameter.wake_word"),
    "recorder": lambda table, language: table.sentence(language, "wizard.parameter.recorder"),
    "detector": lambda table, language: table.sentence(language, "wizard.parameter.detector"),
    "transcription_endpoint": lambda table, language: table.sentence(
        language, "wizard.parameter.transcription_endpoint"
    ),
    "transcription_model": lambda table, language: table.sentence(
        language, "wizard.parameter.transcription_model"
    ),
    "model_endpoint": lambda table, language: table.sentence(language, "wizard.parameter.model_endpoint"),
    "model_name": lambda table, language: table.sentence(language, "wizard.parameter.model_name"),
}

_DETECTOR_LABELS: Mapping[str, Callable[[StringTable, str], str]] = {
    "kind": lambda table, language: table.sentence(language, "wizard.parameter.detector.kind"),
    "argv": lambda table, language: table.sentence(language, "wizard.parameter.detector.argv"),
    "endpoint": lambda table, language: table.sentence(language, "wizard.parameter.detector.endpoint"),
}

#: Credential labels by the setting they fill (setting names, never a platform's).
_CREDENTIAL_LABELS: Mapping[str, Callable[[StringTable, str], str]] = {
    "client_id": lambda table, language: table.sentence(language, "wizard.credential.client_id"),
    "client_secret": lambda table, language: table.sentence(language, "wizard.credential.client_secret"),
    "access_token": lambda table, language: table.sentence(language, "wizard.credential.access_token"),
    "api_key": lambda table, language: table.sentence(language, "wizard.credential.api_key"),
    "transcription.api_key": lambda table, language: table.sentence(
        language, "wizard.credential.transcription_api_key"
    ),
    "wake_word.detector.api_key": lambda table, language: table.sentence(
        language, "wizard.credential.detector_api_key"
    ),
}

_SCREEN_TITLES: Mapping[int, Callable[[StringTable, str], str]] = {
    1: lambda table, language: table.sentence(language, "wizard.screen1.title"),
    2: lambda table, language: table.sentence(language, "wizard.screen2.title"),
    3: lambda table, language: table.sentence(language, "wizard.screen3.title"),
    4: lambda table, language: table.sentence(language, "wizard.screen4.title"),
    5: lambda table, language: table.sentence(language, "wizard.screen5.title"),
}

_LOCATION_SENTENCES: Mapping[str, Callable[[StringTable, str, str], str]] = {
    LOCATION_OWNER_ONLY: lambda table, language, directory: table.sentence(
        language, "wizard.credentials.owner_only", directory=directory
    ),
    LOCATION_LOCAL_APPDATA: lambda table, language, directory: table.sentence(
        language, "wizard.credentials.local_appdata", directory=directory
    ),
}

#: Screen 4's refusals: (cause, next action), the cause naming the path.
_REFUSAL_SENTENCES: Mapping[str, Callable[[StringTable, str, str], tuple[str, str]]] = {
    REFUSAL_NO_DIRECTORY: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.credential_directory_unset"),
        table.sentence(language, "wizard.refusal.credential_directory_unset.next"),
    ),
    REFUSAL_UNAVAILABLE: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.credential_directory_unavailable", path=path),
        table.sentence(language, "wizard.refusal.credential_directory_unavailable.next"),
    ),
    REFUSAL_NOT_OWNED: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.credential_directory_not_owned", path=path),
        table.sentence(language, "wizard.refusal.credential_directory_not_owned.next"),
    ),
    REFUSAL_SHARED: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.credential_directory_shared", path=path),
        table.sentence(language, "wizard.refusal.credential_directory_shared.next"),
    ),
    REFUSAL_NOT_WRITTEN: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.file_not_written_by_wizard", path=path),
        table.sentence(language, "wizard.refusal.file_not_written_by_wizard.next"),
    ),
    REFUSAL_CHANGED: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.file_changed_outside_wizard", path=path),
        table.sentence(language, "wizard.refusal.file_changed_outside_wizard.next"),
    ),
    REFUSAL_APPEARED: lambda table, language, path: (
        table.sentence(language, "wizard.refusal.profile_appeared", path=path),
        table.sentence(language, "wizard.refusal.profile_appeared.next"),
    ),
}

#: The wizard's own failures: (cause, next action) for the given names.
_WIZARD_SENTENCES: Mapping[str, Callable[[StringTable, str, Mapping[str, str]], tuple[str, str]]] = {
    ORDER_REFUSED: lambda table, language, names: (
        table.sentence(language, "wizard.order_refused"),
        table.sentence(language, "wizard.order_refused.next"),
    ),
    INPUT_MISSING: lambda table, language, names: (
        table.sentence(language, "wizard.input_missing", field=names["field"]),
        table.sentence(language, "wizard.input_missing.next"),
    ),
    INPUT_INVALID: lambda table, language, names: (
        table.sentence(language, "wizard.input_invalid", field=names["field"]),
        table.sentence(language, "wizard.input_invalid.next"),
    ),
    PROFILE_UNKNOWN: lambda table, language, names: (
        table.sentence(language, "wizard.profile_unknown"),
        table.sentence(language, "wizard.profile_unknown.next"),
    ),
    LANGUAGE_UNKNOWN: lambda table, language, names: (
        table.sentence(language, "wizard.language_unknown"),
        table.sentence(language, "wizard.language_unknown.next"),
    ),
    REWARD_NOT_PICKED: lambda table, language, names: (
        table.sentence(language, "wizard.reward_not_picked"),
        table.sentence(language, "wizard.reward_not_picked.next"),
    ),
    GENERATION_REFUSED: lambda table, language, names: (
        table.sentence(language, "wizard.generation_refused"),
        table.sentence(language, "wizard.generation_refused.next"),
    ),
    WRITE_FAILED: lambda table, language, names: (
        table.sentence(language, "wizard.write_failed", path=names["path"]),
        table.sentence(language, "wizard.write_failed.next"),
    ),
    CHECK_FAILED: lambda table, language, names: (
        table.sentence(language, "wizard.check_failed"),
        table.sentence(language, "wizard.check_failed.next"),
    ),
    CHECK_FAILED_AT: lambda table, language, names: (
        table.sentence(language, "wizard.check_failed_at", field=names["field"]),
        table.sentence(language, "wizard.check_failed_at.next"),
    ),
    DRY_RUN_NO_RECORD: lambda table, language, names: (
        table.sentence(language, "wizard.dry_run_no_record"),
        table.sentence(language, "wizard.dry_run_no_record.next"),
    ),
    DRY_RUN_INCOMPLETE: lambda table, language, names: (
        table.sentence(language, "wizard.dry_run_incomplete"),
        table.sentence(language, "wizard.dry_run_incomplete.next"),
    ),
    DRY_RUN_UNEXPECTED: lambda table, language, names: (
        table.sentence(language, "wizard.dry_run_unexpected"),
        table.sentence(language, "wizard.dry_run_unexpected.next"),
    ),
    DRY_RUN_TIMED_OUT: lambda table, language, names: (
        table.sentence(language, "wizard.dry_run_timed_out", seconds=names["seconds"]),
        table.sentence(language, "wizard.dry_run_timed_out.next"),
    ),
    STATE_UNREADABLE: lambda table, language, names: (
        table.sentence(language, "wizard.state_unreadable", path=names["path"]),
        table.sentence(language, "wizard.state_unreadable.next"),
    ),
    PAGE_EXPIRED: lambda table, language, names: (
        table.sentence(language, "wizard.page_expired"),
        table.sentence(language, "wizard.page_expired.next"),
    ),
}


def _argv_text(value: Any) -> str:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return shlex.join(value)
    return ""


class Wizard:
    """The five screens over one profile (R5, A7): render/validate pairs and
    one fingerprint table.

    Screen *n*'s fingerprint is SHA-256 over screen *n − 1*'s fingerprint,
    its own non-secret inputs and, for each of its credentials, the HMAC
    under *key* (minted per process, held in memory only) of the effective
    value — the one submitted, else the stored one kept. A screen counts as
    passed only while the fingerprint its success recorded is its current
    one and every earlier screen is passed, so a changed input — a replaced
    token included — invalidates that screen and every later one by
    construction; an unchanged resubmission invalidates nothing. Changing a
    screen's inputs also drops the recorded passes from it on, so going back
    to an earlier value revives none. The table is never written: the state
    file persists screen 1's pass only (A7), and a new process — a new key —
    passes screens 2–5 again.

    Submitted credentials are held in memory until screen 4 stores them in
    the credential file (A1); no page, redirect or link ever carries one,
    and a credential input is rendered empty, "kept" when a value is held.
    The wizard never starts the runtime.
    """

    def __init__(
        self,
        profile_path: Path,
        *,
        key: bytes,
        table: StringTable,
        first_run: bool,
        stored: Mapping[str, str],
        environ: Callable[[], Mapping[str, str]],
        checker: Callable[[Path, Mapping[str, str]], tuple[bool, Sequence[str]]],
        runner: Callable[[Any], Any],
        seams: WizardSeams | None = None,
        on_stored: Callable[[Mapping[str, str]], None] = lambda values: None,
        on_submitted: Callable[[str], None] = lambda value: None,
        escape: Callable[[Any], str] = lambda value: html.escape(str(value), quote=True),
        profiles: Sequence[Profile] | None = None,
    ) -> None:
        self.profile_path = Path(profile_path)
        self._key = key
        self.table = table
        #: Whether no profile existed when the UI started (A2): a profile
        #: found at the target later appeared after detection (AC16).
        self.first_run = first_run
        self._stored = dict(stored)
        self._environ = environ
        self._checker = checker
        self._runner = runner
        self.seams = WizardSeams() if seams is None else seams
        self._on_stored = on_stored
        self._on_submitted = on_submitted
        self._escape = escape
        self.profiles = tuple(load_profiles() if profiles is None else profiles)
        self._by_id = {profile.id: profile for profile in self.profiles}
        self.store = CredentialStore(profile_path, locations.running_platform(), os.environ)
        self._lock = threading.RLock()
        #: Screen → the fingerprint its success was recorded for (memory only).
        self._passes: dict[int, str] = {}
        self._submitted: dict[str, str] = {}
        self._identities: dict[str, str] = {}
        self._login: str | None = None
        self._validations: dict[str, PlatformValidation] = {}
        self._rewards: dict[str, RewardList] = {}
        self._record: Any = None
        #: The non-secret values of an expired submission, shown in place of
        #: the recorded ones while :meth:`expired` renders (P12F4 B6).
        self._draft: dict[str, str] = {}
        self.unreadable = False
        try:
            self.state = WizardState(profile_path)
        except WizardStateError:
            self.state = WizardState.__new__(WizardState)
            self.state.path = state_path(profile_path)
            self.state.profile, self.state.parameters = None, {}
            self.state.language, self.state.completed = None, False
            self.state.written, self.state._passed, self.state.exists = {}, set(), True
            self.unreadable = True
        if not self.unreadable and self.state.passed(PERSISTED_SCREEN):
            # A restart resumes screen 1 only (A7), and only while its
            # recorded inputs still pass.
            if self._choice_failure() is None:
                self._passes[1] = self.fingerprint_of(1)
            else:
                self.state.invalidate(1)

    # -- inputs -------------------------------------------------------------- #

    @property
    def language(self) -> str:
        return self.table.language_or_default(self.state.language)

    @property
    def profile(self) -> Profile | None:
        return self._by_id.get(self.state.profile or "")

    def _effective(self, name: str) -> str:
        return self._submitted.get(name) or self._stored.get(name) or ""

    def credential_digest(self, value: str) -> str:
        """The keyed HMAC a fingerprint covers for one credential value."""

        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _screen_items(self, screen: int) -> tuple[list[Parameter], list[Credential]]:
        profile = self.profile
        if profile is None:
            return [], []
        return (
            [item for item in profile.parameters if SCREEN_OF[item.screen] == screen],
            [item for item in profile.credentials if SCREEN_OF[item.screen] == screen],
        )

    def fingerprint_of(self, screen: int) -> str:
        """Screen *screen*'s fingerprint over the current inputs (see class)."""

        digest = hashlib.sha256(b"" if screen == 1 else self.fingerprint_of(screen - 1).encode())
        parameters, credentials = self._screen_items(screen)
        plain: dict[str, Any] = {"screen": screen}
        if screen == 1:
            plain["profile"] = self.state.profile
        plain["parameters"] = {item.name: self.state.parameters.get(item.name) for item in parameters}
        digest.update(json.dumps(plain, sort_keys=True, ensure_ascii=False).encode())
        for credential in sorted(credentials, key=lambda item: item.name):
            value = self._effective(credential.name)
            mark = self.credential_digest(value) if value else "-"
            digest.update(f"\0{credential.name}\0{mark}".encode())
        return digest.hexdigest()

    def passed(self, screen: int) -> bool:
        """Whether *screen* and every earlier one passed for the current inputs."""

        with self._lock:
            return all(
                self._passes.get(number) == self.fingerprint_of(number) for number in range(1, screen + 1)
            )

    def passed_screens(self) -> tuple[int, ...]:
        return tuple(number for number in SCREEN_NUMBERS if self.passed(number))

    def _first_open(self) -> int:
        return next((number for number in SCREEN_NUMBERS if not self.passed(number)), 5)

    def _record_pass(self, screen: int) -> None:
        self._passes[screen] = self.fingerprint_of(screen)
        self.state.pass_screen(screen)

    def _changed(self, screen: int, before: str) -> None:
        """Drop the passes from *screen* on when its inputs changed (R5)."""

        if self.fingerprint_of(screen) != before:
            self._drop(screen)

    def _drop(self, screen: int) -> None:
        for number in SCREEN_NUMBERS:
            if number >= screen:
                self._passes.pop(number, None)
        self.state.invalidate(screen)
        self._record = None

    def _save_state(self) -> None:
        self.state.save(self.store, tuple(self._submitted.values()))

    def _take_credentials(self, fields: Mapping[str, str], credentials: Sequence[Credential]) -> None:
        for credential in credentials:
            value = fields.get(CREDENTIAL_FIELD + credential.name, "").strip()
            if value:
                self._on_submitted(value)
                self._submitted[credential.name] = value

    # -- the pages ----------------------------------------------------------- #

    def get(self, query: Mapping[str, str], csrf_field: str) -> WizardPage:
        """A screen: ``?screen=n`` when every earlier screen passed, else the
        first open screen with the order refusal (409); ``?view=profile``
        is screen 4's read-only profile view."""

        with self._lock:
            if self.unreadable:
                return self._unreadable(csrf_field)
            if query.get("view") == "profile":
                return self._profile_view(csrf_field)
            requested = self._screen_number(query.get("screen"))
            first_open = self._first_open()
            if requested is None:
                return self._render(first_open, csrf_field)
            if requested > first_open:
                return self._render(first_open, csrf_field, self._notice(ORDER_REFUSED), status=409)
            return self._render(requested, csrf_field)

    def post(self, fields: Sequence[tuple[str, str]], csrf_field: str) -> WizardPage:
        """Submit one screen: refused (409, the first open screen re-rendered,
        nothing validated) unless every earlier screen passed."""

        with self._lock:
            if self.unreadable:
                return self._unreadable(csrf_field)
            posted = {name: text for name, text in fields}
            screen = self._screen_number(posted.get("screen"))
            if screen is None:
                screen = self._first_open()
            if screen > 1 and not self.passed(screen - 1):
                return self._render(self._first_open(), csrf_field, self._notice(ORDER_REFUSED), status=409)
            handler = {
                1: self._submit_choice,
                2: self._submit_platform,
                3: self._submit_model,
                4: self._submit_profile,
                5: self._submit_dry_run,
            }[screen]
            return self._render(screen, csrf_field, handler(posted))

    def expired(self, fields: Sequence[tuple[str, str]], csrf_field: str) -> WizardPage:
        """A submission whose CSRF token is no longer the session's (P12F4
        B6): nothing of it is validated, stored or kept. Its screen — the
        first open one when it names none or one not open yet — is rendered
        again (403) with the page-expired notice, a fresh *csrf_field* and the
        submitted non-secret values in place, so the streamer checks them and
        sends again. A credential is never echoed: its input stays empty."""

        with self._lock:
            if self.unreadable:
                return self._unreadable(csrf_field)
            posted = {name: text for name, text in fields}
            screen = self._screen_number(posted.get("screen"))
            first_open = self._first_open()
            draft: dict[str, str] = {}
            if screen is None or screen > first_open:
                screen = first_open
            else:
                draft = {name: text for name, text in posted.items() if not name.startswith(CREDENTIAL_FIELD)}
            self._draft = draft
            try:
                return self._render(screen, csrf_field, self._notice(PAGE_EXPIRED), status=403)
            finally:
                self._draft = {}

    def expired_notice(self) -> str:
        """The page-expired notice's markup, for the pages that are not the
        wizard's (the console's, the editor's, P12F4 B6)."""

        with self._lock:
            return self._notice_markup(self._notice(PAGE_EXPIRED))

    def set_language(self, language: str) -> bool:
        """Persist *language* in the state file (DD3); ``False`` when it is not
        offered. A profile the wizard never ran for (it exists, no state) gets
        a state that is ``completed`` and owns nothing, so the wizard is
        never offered for it."""

        with self._lock:
            if self.unreadable or not self.table.offers(language):
                return False
            if not self.state.exists and self.profile_path.exists():
                self.state.completed = True
            self.state.language = language
            self._save_state()
            return True

    @staticmethod
    def _screen_number(text: str | None) -> int | None:
        if text is None or text not in {str(number) for number in SCREEN_NUMBERS}:
            return None
        return int(text)

    # -- screen 1: language, profile and its parameters ---------------------- #

    def _choice_failure(self) -> _Notice | None:
        profile = self.profile
        if profile is None:
            return self._notice(PROFILE_UNKNOWN)
        parameters, credentials = self._screen_items(1)
        label = f"profile {profile.id!r}"
        for parameter in parameters:
            value = self.state.parameters.get(parameter.name)
            if value in (None, "", []):
                return self._notice(INPUT_MISSING, field=self._parameter_label(parameter.name))
            try:
                _parameter_value(label, parameter, value)
            except ProfileError:
                return self._notice(INPUT_INVALID, field=self._parameter_label(parameter.name))
        for credential in credentials:
            if credential.parameter is None and not self._effective(credential.name):
                return self._notice(INPUT_MISSING, field=self._credential_label(credential))
        return None

    def _submit_choice(self, posted: Mapping[str, str]) -> _Notice:
        before = self.fingerprint_of(1)
        language = posted.get("language", "")
        if language and not self.table.offers(language):
            return self._notice(LANGUAGE_UNKNOWN)
        if language:
            self.state.language = language
        chosen = posted.get("profile", "")
        profile = self._by_id.get(chosen)
        if profile is None:
            self._save_state()
            return self._notice(PROFILE_UNKNOWN)
        if self.state.profile != profile.id:
            declared = {item.name for item in profile.parameters}
            self.state.parameters = {
                name: value for name, value in self.state.parameters.items() if name in declared
            }
            self.state.profile = profile.id
        failure: _Notice | None = None
        for parameter in profile.parameters:
            if SCREEN_OF[parameter.screen] != 1:
                continue
            value, invalid = self._parsed(posted, profile, parameter)
            self.state.parameters[parameter.name] = value
            if invalid and failure is None:
                failure = self._notice(INPUT_INVALID, field=self._parameter_label(parameter.name))
        self._take_credentials(posted, [item for item in profile.credentials if SCREEN_OF[item.screen] == 1])
        self._changed(1, before)
        failure = failure or self._choice_failure()
        if failure is None:
            self._record_pass(1)
        self._save_state()
        if failure is not None:
            return failure
        return _Notice("passed", self.table.sentence(self.language, "wizard.screen1.passed"), success=True)

    def _parsed(self, posted: Mapping[str, str], profile: Profile, parameter: Parameter) -> tuple[Any, bool]:
        """A parameter's posted value as its kind holds it, and whether the
        text could not be read as that kind."""

        name = f"{PARAMETER_FIELD}{profile.id}.{parameter.name}"
        if parameter.kind == "text":
            return posted.get(name, "").strip(), False
        if parameter.kind == "argv":
            try:
                return shlex.split(posted.get(name, "")), False
            except ValueError:
                return [], True
        kind = posted.get(f"{name}.kind", "").strip()
        detector: dict[str, Any] = {"kind": kind} if kind else {}
        try:
            argv = shlex.split(posted.get(f"{name}.argv", ""))
        except ValueError:
            return detector, True
        endpoint = posted.get(f"{name}.endpoint", "").strip()
        if argv:
            detector["argv"] = argv
        if endpoint:
            detector["endpoint"] = endpoint
        return detector, False

    # -- screen 2: the platform credential and the reward -------------------- #

    def _platform_credentials(self) -> tuple[Credential | None, Credential | None]:
        profile = self.profile
        if profile is None:
            return None, None
        found = {
            item.setting: item
            for item in profile.credentials
            if item.module == profile.platform_module and item.parameter is None
        }
        return found.get("access_token"), found.get("client_id")

    def _platform_key(self, profile: Profile, token: str, client_id: str) -> str:
        """The key a platform validation and reward list are kept under.

        The profile is part of it: its scopes are part of what passed.
        """

        return f"{profile.id}:{self.credential_digest(token)}:{self.credential_digest(client_id)}"

    def _submit_platform(self, posted: Mapping[str, str]) -> _Notice:
        profile = self.profile
        assert profile is not None
        before = self.fingerprint_of(2)
        parameters, credentials = self._screen_items(2)
        self._take_credentials(posted, credentials)
        for parameter in parameters:
            self.state.parameters[parameter.name] = posted.get(
                f"{PARAMETER_FIELD}{profile.id}.{parameter.name}", ""
            ).strip()
        self._changed(2, before)
        self._save_state()
        if self.passed(2):
            return self._platform_success()
        for credential in credentials:
            if not self._effective(credential.name):
                return self._notice(INPUT_MISSING, field=self._credential_label(credential))
        token_credential, client_credential = self._platform_credentials()
        token = self._effective(token_credential.name) if token_credential else ""
        client_id = self._effective(client_credential.name) if client_credential else ""
        key = self._platform_key(profile, token, client_id)
        validation = self._validations.get(key)
        if validation is None:
            validation = self._run(
                validate_platform(
                    profile, token, client_id,
                    transport=self.seams.platform_transport, clock=self.seams.clock,
                )
            )
            if not validation.passed:
                assert validation.failure is not None
                return self._failure(validation.failure)
            self._validations[key] = validation
        self._identities = dict(validation.identities)
        self._login = validation.login
        reward = next((item for item in parameters if item.source == "reward_list"), None)
        if reward is not None:
            rewards = self._rewards.get(key)
            if rewards is None:
                rewards = self._run(
                    list_rewards(
                        profile, token, client_id, validation.user_id or "",
                        transport=self.seams.platform_transport, clock=self.seams.clock,
                    )
                )
                if not rewards.passed:
                    assert rewards.failure is not None
                    return self._failure(rewards.failure)
                self._rewards[key] = rewards
            if self.state.parameters.get(reward.name) not in {item.id for item in rewards.rewards}:
                return self._notice(REWARD_NOT_PICKED)
        self._record_pass(2)
        self._save_state()
        return self._platform_success()

    def _platform_success(self) -> _Notice:
        assert self._login is not None
        return _Notice(
            "passed",
            self.table.sentence(self.language, "platform.connected", login=self._login),
            success=True,
        )

    # -- screen 3: the model ------------------------------------------------- #

    def _submit_model(self, posted: Mapping[str, str]) -> _Notice:
        profile = self.profile
        assert profile is not None
        before = self.fingerprint_of(3)
        parameters, credentials = self._screen_items(3)
        self._take_credentials(posted, credentials)
        for parameter in parameters:
            self.state.parameters[parameter.name] = posted.get(
                f"{PARAMETER_FIELD}{profile.id}.{parameter.name}", ""
            ).strip()
        self._changed(3, before)
        self._save_state()
        if not profile.model_needed:
            validation = self._run(
                validate_model(
                    "", "", "", transport=self.seams.model_transport, clock=self.seams.clock,
                    profile=profile,
                )
            )
        elif self.passed(3):
            validation = ModelValidation(None)
        else:
            for parameter in parameters:
                if not self.state.parameters.get(parameter.name):
                    return self._notice(INPUT_MISSING, field=self._parameter_label(parameter.name))
            for credential in credentials:
                if not self._effective(credential.name):
                    return self._notice(INPUT_MISSING, field=self._credential_label(credential))
            api_key = next(
                (self._effective(item.name) for item in credentials if item.setting == "api_key"), ""
            )
            validation = self._run(
                validate_model(
                    str(self.state.parameters.get("model_endpoint", "")),
                    str(self.state.parameters.get("model_name", "")),
                    api_key,
                    transport=self.seams.model_transport,
                    clock=self.seams.clock,
                    profile=profile,
                )
            )
        if not validation.passed:
            assert validation.failure is not None
            return self._failure(validation.failure)
        self._record_pass(3)
        self._save_state()
        return _Notice("passed", model_sentence(self.table, self.language, validation), success=True)

    # -- screen 4: write the profile and the credentials, then check --------- #

    def owns_profile(self) -> bool:
        return self.state.owns(self.profile_path)

    def _profile_refusal(self) -> Refusal | None:
        path = self.profile_path
        if not os.path.lexists(path):
            return None
        if _key(path) in self.state.written:
            return None if self.state.owns(path) else Refusal(REFUSAL_CHANGED, path)
        return Refusal(REFUSAL_APPEARED if self.first_run else REFUSAL_NOT_WRITTEN, path)

    def _credential_values(self, profile: Profile) -> tuple[dict[str, str], list[str]]:
        values: dict[str, str] = {}
        optional: list[str] = []
        for credential in profile.credentials:
            value = self._effective(credential.name)
            if not value:
                continue
            values[credential.name] = value
            if credential.parameter is not None:
                optional.append(credential.name)
        return values, optional

    def _submit_profile(self, posted: Mapping[str, str]) -> _Notice:
        profile = self.profile
        assert profile is not None
        # Passing screen 4 again writes and checks again: until it passes,
        # neither it nor screen 5 counts (A7).
        self._drop(4)
        values, optional = self._credential_values(profile)
        parameters = {
            item.name: self.state.parameters.get(item.name) for item in profile.parameters
        }
        try:
            document = generate_profile(profile, parameters, self._identities, optional)
        except ProfileError:
            return self._notice(GENERATION_REFUSED)
        data = render_profile_yaml(document).encode("utf-8")
        refusal = self._profile_refusal() or self.store.check_directory()
        if refusal is None and self.store.path is not None and os.path.lexists(self.store.path):
            refusal = self.store._ownership(self.state)
        if refusal is not None:
            return self._refusal(refusal)
        path = self.profile_path
        try:
            if not _replace_owner_only(path, data, exclusive=not os.path.lexists(path)):
                return self._refusal(Refusal(REFUSAL_APPEARED, path))
        except OSError:
            return self._notice(WRITE_FAILED, path=os.fspath(path))
        self.state.written[_key(path)] = file_digest(data)
        try:
            refusal = self.store.write(values, document[SECRETS_LIST], self.state)
        except OSError:
            self._save_state()
            return self._notice(WRITE_FAILED, path=os.fspath(self.store.path))
        self._save_state()
        if refusal is not None:
            return self._refusal(refusal)
        self._stored = dict(self.store._values)
        self._on_stored(dict(self._stored))
        try:
            passed, diagnostics = self._checker(path, dict(self._environ()))
        except Exception:
            # A check that could not run is a failed check; its text is never kept.
            passed, diagnostics = False, []
        if not passed:
            field_path = checked_field(diagnostics)
            if field_path is None:
                return self._notice(CHECK_FAILED)
            return self._notice(CHECK_FAILED_AT, field=field_path)
        self._record_pass(4)
        self._save_state()
        return _Notice("passed", self.table.sentence(self.language, "wizard.check_passed"), success=True)

    # -- screen 5: the dry run ----------------------------------------------- #

    def _submit_dry_run(self, posted: Mapping[str, str]) -> _Notice:
        profile = self.profile
        assert profile is not None
        if self.passed(5) and self._record is not None:
            return self._dry_run_success(self._record)
        try:
            read_back = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            read_back = None
        document = read_back if isinstance(read_back, Mapping) else {}
        event = dry_run_event(profile, document, self.state.parameters)
        runner = self.seams.dry_runner
        if runner is None:
            from core.main import dry_run as runner  # the production entry (DD6)
        clock = self.seams.clock
        try:
            record = self._run(
                _within(
                    runner(
                        self.profile_path,
                        event,
                        environ=dict(self._environ()),
                        clock=clock,
                        timeout=DRY_RUN_TIMEOUT_SECONDS,
                    ),
                    clock,
                    DRY_RUN_TIMEOUT_SECONDS,
                )
            )
        except Exception as error:
            # The entry's reason is a value-free token; its text is never kept.
            if getattr(error, "reason", None) == "timed_out":
                return self._notice(DRY_RUN_TIMED_OUT, seconds=f"{DRY_RUN_TIMEOUT_SECONDS:g}")
            return self._notice(DRY_RUN_NO_RECORD)
        if record is _TIMED_OUT:
            return self._notice(DRY_RUN_TIMED_OUT, seconds=f"{DRY_RUN_TIMEOUT_SECONDS:g}")
        if record is None:
            return self._notice(DRY_RUN_NO_RECORD)
        run_id = getattr(record, "run_id", None)
        outcome = getattr(record, "outcome", None)
        origin = getattr(record, "origin", None)
        named = getattr(record, "event", None)
        if (
            not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(outcome, str)
            or not outcome.strip()
            or origin != DRY_RUN_ORIGIN
            or not isinstance(named, Mapping)
            or named.get("message_id") != event["payload"]["message_id"]
        ):
            return self._notice(DRY_RUN_INCOMPLETE)
        if outcome != profile.dry_run_outcome:
            return self._notice(DRY_RUN_UNEXPECTED)
        self._record = record
        self._record_pass(5)
        self.state.completed = True
        self._save_state()
        return self._dry_run_success(record)

    def _dry_run_success(self, record: Any) -> _Notice:
        if record.outcome == DRY_RUN_OUTCOME_NOT_TRIGGERED:
            text = self.table.sentence(
                self.language, "wizard.dry_run_not_triggered",
                run_id=record.run_id, reason=str(getattr(record, "reason", "")),
            )
        else:
            text = self.table.sentence(self.language, "wizard.dry_run_completed", run_id=record.run_id)
        return _Notice("passed", text, success=True)

    # -- helpers ------------------------------------------------------------- #

    def _run(self, coroutine: Any) -> Any:
        return self._runner(coroutine)

    def _notice(self, token: str, **names: str) -> _Notice:
        cause, next_action = _WIZARD_SENTENCES[token](self.table, self.language, names)
        return _Notice(token, cause, next_action)

    def _failure(self, failure: Failure) -> _Notice:
        cause, next_action = failure_sentences(self.table, self.language, failure)
        return _Notice(failure.token, cause, next_action)

    def _refusal(self, refusal: Refusal) -> _Notice:
        path = "" if refusal.path is None else os.fspath(refusal.path)
        cause, next_action = _REFUSAL_SENTENCES[refusal.reason](self.table, self.language, path)
        return _Notice(refusal.reason, cause, next_action)

    def _parameter_label(self, name: str) -> str:
        label = _PARAMETER_LABELS.get(name)
        if label is None:
            return self.table.sentence(self.language, "wizard.parameter.other", name=name)
        return label(self.table, self.language)

    def _credential_label(self, credential: Credential) -> str:
        label = _CREDENTIAL_LABELS.get(credential.setting)
        if label is None:
            return self.table.sentence(self.language, "wizard.credential.other", name=credential.name)
        return label(self.table, self.language)

    def _say(self, key: str) -> str:
        """A sentence without placeholders, through the literal-key maps."""

        return self._escape(_PLAIN_SENTENCES[key](self.table, self.language))

    # -- rendering ----------------------------------------------------------- #

    def _page(self, body: str, status: int = 200) -> WizardPage:
        title = self.table.sentence(self.language, "wizard.title")
        return WizardPage(status, self.language, title, body)

    def _language_form(self, csrf_field: str) -> str:
        options = "".join(
            f'<option value="{self._escape(code)}"{" selected" if code == self.language else ""}>'
            f"{self._escape(name)}</option>"
            for code, name in self.table.languages()
        )
        return (
            f'<form method="post" action="{LANGUAGE_ROUTE}" class="language">{csrf_field}'
            f'<label for="language-choice">{self._say("wizard.language.label")}</label> '
            f'<select id="language-choice" name="language">{options}</select> '
            f'<button type="submit">{self._say("wizard.language.change")}</button></form>'
        )

    def _notice_markup(self, notice: _Notice | None) -> str:
        if notice is None:
            return ""
        if notice.success:
            return f'<p class="success" data-outcome="{self._escape(notice.token)}">{self._escape(notice.text)}</p>'
        return (
            f'<div class="failure" data-cause="{self._escape(notice.token)}">'
            f'<p class="cause">{self._escape(notice.text)}</p>'
            f'<p class="next-action">{self._escape(notice.next_action)}</p></div>'
        )

    def _render(
        self, screen: int, csrf_field: str, notice: _Notice | None = None, *, status: int = 200
    ) -> WizardPage:
        passed = self.passed(screen)
        content = {
            1: self._choice_screen,
            2: self._platform_screen,
            3: self._model_screen,
            4: self._profile_screen,
            5: self._dry_run_screen,
        }[screen](csrf_field, notice)
        title = _SCREEN_TITLES[screen](self.table, self.language)
        steps = self.table.sentence(self.language, "wizard.step", number=screen, total=len(SCREEN_NUMBERS))
        nav = ""
        if passed and screen < 5:
            nav = (
                f'<p><a id="next" href="{WIZARD_ROUTE}?screen={screen + 1}">'
                f'{self._say("wizard.next")}</a></p>'
            )
        elif passed and self.state.completed:
            nav = f'<p><a id="console" href="{CONSOLE_ROUTE}">{self._say("wizard.console")}</a></p>'
        back = (
            f'<p><a href="{WIZARD_ROUTE}?screen={screen - 1}">{self._say("wizard.back")}</a></p>'
            if screen > 1
            else ""
        )
        body = (
            f"{self._language_form(csrf_field)}"
            f'<main id="wizard" data-screen="{screen}" data-passed="{"true" if passed else "false"}">'
            f'<p class="step">{self._escape(steps)}</p><h1>{self._escape(title)}</h1>'
            f"{self._notice_markup(notice)}{content}{nav}{back}</main>"
        )
        return self._page(body, status)

    def _form(self, screen: int, csrf_field: str, inner: str, button: str) -> str:
        return (
            f'<form method="post" action="{WIZARD_ROUTE}" id="screen-form">{csrf_field}'
            f'<input type="hidden" name="screen" value="{screen}">{inner}'
            f'<button type="submit">{button}</button></form>'
        )

    def _text_input(self, name: str, label: str, value: str) -> str:
        value = self._draft.get(name, value)
        identifier = self._escape(name.replace(".", "-"))
        return (
            f'<div class="field"><label for="{identifier}">{self._escape(label)}</label> '
            f'<input type="text" id="{identifier}" name="{self._escape(name)}" '
            f'value="{self._escape(value)}"></div>'
        )

    def _credential_input(self, credential: Credential) -> str:
        held = bool(self._effective(credential.name))
        name = CREDENTIAL_FIELD + credential.name
        identifier = self._escape(name.replace(".", "-"))
        kept = f' <span class="kept">{self._say("wizard.kept")}</span>' if held else ""
        return (
            f'<div class="field"><label for="{identifier}">'
            f"{self._escape(self._credential_label(credential))}</label> "
            f'<input type="password" id="{identifier}" name="{self._escape(name)}" value="" '
            f'autocomplete="off" data-state="{"kept" if held else "empty"}">{kept}</div>'
        )

    def _parameter_inputs(self, profile: Profile, parameter: Parameter) -> str:
        name = f"{PARAMETER_FIELD}{profile.id}.{parameter.name}"
        current = self.state.parameters.get(parameter.name) if self.state.profile == profile.id else None
        label = self._parameter_label(parameter.name)
        if parameter.kind == "text":
            return self._text_input(name, label, current if isinstance(current, str) else "")
        if parameter.kind == "argv":
            return self._text_input(name, label, _argv_text(current))
        detector = current if isinstance(current, Mapping) else {}
        kind = detector.get("kind")
        endpoint = detector.get("endpoint")
        return (
            f'<fieldset class="detector"><legend>{self._escape(label)}</legend>'
            + self._text_input(
                f"{name}.kind", _DETECTOR_LABELS["kind"](self.table, self.language),
                kind if isinstance(kind, str) else "",
            )
            + self._text_input(
                f"{name}.argv", _DETECTOR_LABELS["argv"](self.table, self.language),
                _argv_text(detector.get("argv")),
            )
            + self._text_input(
                f"{name}.endpoint", _DETECTOR_LABELS["endpoint"](self.table, self.language),
                endpoint if isinstance(endpoint, str) else "",
            )
            + "</fieldset>"
        )

    def _choice_screen(self, csrf_field: str, notice: _Notice | None) -> str:
        options = []
        chosen = self._draft.get("profile", self.state.profile)
        language = self._draft.get("language", "")
        if not self.table.offers(language):
            language = self.language
        for profile in self.profiles:
            checked = " checked" if profile.id == chosen else ""
            name = _PROFILE_NAMES.get(profile.name_key)
            description = _PROFILE_NAMES.get(profile.description_key)
            inner = "".join(
                self._parameter_inputs(profile, parameter)
                for parameter in profile.parameters
                if SCREEN_OF[parameter.screen] == 1
            ) + "".join(
                self._credential_input(credential)
                for credential in profile.credentials
                if SCREEN_OF[credential.screen] == 1
            )
            if not inner:
                # Nothing of its own on this screen (P12F4 M5): say so where
                # its inputs would be, so the option does not read as broken.
                inner = f'<p class="nothing-here">{self._say("wizard.profile.nothing_here")}</p>'
            options.append(
                f'<fieldset class="profile-option" data-profile="{self._escape(profile.id)}"><legend>'
                f'<label><input type="radio" name="profile" value="{self._escape(profile.id)}"{checked}> '
                f'<span class="profile-name">{self._escape(name(self.table, self.language) if name else profile.id)}'
                f"</span></label></legend>"
                f'<p class="profile-description">'
                f'{self._escape(description(self.table, self.language) if description else "")}</p>'
                f"{inner}</fieldset>"
            )
        languages = "".join(
            f'<option value="{self._escape(code)}"{" selected" if code == language else ""}>'
            f"{self._escape(display)}</option>"
            for code, display in self.table.languages()
        )
        inner = (
            f'<div class="field"><label for="screen-language">{self._say("wizard.language.label")}</label> '
            f'<select id="screen-language" name="language">{languages}</select></div>'
            f'<p>{self._say("wizard.profile.choose")}</p>{"".join(options)}'
        )
        return self._form(1, csrf_field, inner, self._say("wizard.save"))

    def _platform_screen(self, csrf_field: str, notice: _Notice | None) -> str:
        profile = self.profile
        if profile is None:
            return ""
        parameters, credentials = self._screen_items(2)
        inner = "".join(self._credential_input(credential) for credential in credentials)
        token_credential, client_credential = self._platform_credentials()
        rewards = self._rewards.get(
            self._platform_key(
                profile,
                self._effective(token_credential.name) if token_credential else "",
                self._effective(client_credential.name) if client_credential else "",
            )
        )
        for parameter in parameters:
            if parameter.source != "reward_list":
                continue
            if rewards is None or not rewards.rewards:
                continue
            picked = self._draft.get(
                f"{PARAMETER_FIELD}{profile.id}.{parameter.name}", self.state.parameters.get(parameter.name)
            )
            choices = "".join(
                f'<label class="reward"><input type="radio" '
                f'name="{self._escape(f"{PARAMETER_FIELD}{profile.id}.{parameter.name}")}" '
                f'value="{self._escape(reward.id)}"{" checked" if reward.id == picked else ""}> '
                f"{self._escape(reward.title)}</label>"
                for reward in rewards.rewards
            )
            inner += (
                f'<fieldset class="rewards"><legend>{self._escape(self._parameter_label(parameter.name))}'
                f"</legend>{choices}</fieldset>"
            )
        return self._form(2, csrf_field, inner, self._say("wizard.check"))

    def _model_screen(self, csrf_field: str, notice: _Notice | None) -> str:
        profile = self.profile
        if profile is None:
            return ""
        if not profile.model_needed:
            text = self._escape(self.table.sentence(self.language, "model.not_needed"))
            return f'<p class="not-needed">{text}</p>' + self._form(
                3, csrf_field, "", self._say("wizard.continue")
            )
        parameters, credentials = self._screen_items(3)
        inner = "".join(
            self._text_input(
                f"{PARAMETER_FIELD}{profile.id}.{parameter.name}",
                self._parameter_label(parameter.name),
                str(self.state.parameters.get(parameter.name) or ""),
            )
            for parameter in parameters
        ) + "".join(self._credential_input(credential) for credential in credentials)
        return self._form(3, csrf_field, inner, self._say("wizard.check"))

    def _location_markup(self) -> str:
        directory = self.store.directory
        if directory is None:
            return ""
        sentence = _LOCATION_SENTENCES[self.store.location_reason](
            self.table, self.language, os.fspath(directory)
        )
        return f'<p class="credential-location">{self._escape(sentence)}</p>'

    def _profile_screen(self, csrf_field: str, notice: _Notice | None) -> str:
        view = ""
        if self.owns_profile():
            view = (
                f'<p><a id="view-profile" href="{WIZARD_ROUTE}?view=profile">'
                f'{self._say("wizard.view_profile")}</a></p>'
            )
        written = self._escape(
            self.table.sentence(self.language, "wizard.profile_target", path=os.fspath(self.profile_path))
        )
        return (
            f'<p class="profile-target">{written}</p>{self._location_markup()}'
            + self._form(4, csrf_field, "", self._say("wizard.write"))
            + view
        )

    def _dry_run_screen(self, csrf_field: str, notice: _Notice | None) -> str:
        if self.state.completed and self.passed(5):
            return ""
        return f'<p>{self._say("wizard.dry_run.explain")}</p>' + self._form(
            5, csrf_field, "", self._say("wizard.dry_run.start")
        )

    def _profile_view(self, csrf_field: str) -> WizardPage:
        """The generated profile, read-only (R5 screen 4, D6): a non-editable
        rendering with no form, redacted like every response."""

        title = self._escape(self.table.sentence(self.language, "wizard.profile_view.title"))
        back = f'<p><a href="{WIZARD_ROUTE}?screen=4">{self._say("wizard.back")}</a></p>'
        if not self.owns_profile():
            notice = self._refusal(
                Refusal(REFUSAL_CHANGED if os.path.lexists(self.profile_path) else REFUSAL_NOT_WRITTEN,
                        self.profile_path)
            )
            return self._page(f"<h1>{title}</h1>{self._notice_markup(notice)}{back}", 409)
        try:
            text = self.profile_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return self._page(
                f"<h1>{title}</h1>{self._notice_markup(self._notice(WRITE_FAILED, path=os.fspath(self.profile_path)))}{back}",
                409,
            )
        return self._page(
            f'<h1>{title}</h1><pre id="profile-yaml" class="read-only">{self._escape(text)}</pre>{back}'
        )

    def _unreadable(self, csrf_field: str) -> WizardPage:
        notice = self._notice(STATE_UNREADABLE, path=os.fspath(self.state.path))
        return self._page(f"{self._language_form(csrf_field)}{self._notice_markup(notice)}", 409)


#: The sentences without placeholders the screens show (literal keys, AC5).
_PLAIN_SENTENCES: Mapping[str, Callable[[StringTable, str], str]] = {
    "wizard.language.label": lambda table, language: table.sentence(language, "wizard.language.label"),
    "wizard.language.change": lambda table, language: table.sentence(language, "wizard.language.change"),
    "wizard.next": lambda table, language: table.sentence(language, "wizard.next"),
    "wizard.back": lambda table, language: table.sentence(language, "wizard.back"),
    "wizard.console": lambda table, language: table.sentence(language, "wizard.console"),
    "wizard.kept": lambda table, language: table.sentence(language, "wizard.kept"),
    "wizard.save": lambda table, language: table.sentence(language, "wizard.save"),
    "wizard.check": lambda table, language: table.sentence(language, "wizard.check"),
    "wizard.continue": lambda table, language: table.sentence(language, "wizard.continue"),
    "wizard.write": lambda table, language: table.sentence(language, "wizard.write"),
    "wizard.view_profile": lambda table, language: table.sentence(language, "wizard.view_profile"),
    "wizard.profile.choose": lambda table, language: table.sentence(language, "wizard.profile.choose"),
    "wizard.profile.nothing_here": lambda table, language: table.sentence(
        language, "wizard.profile.nothing_here"
    ),
    "wizard.dry_run.explain": lambda table, language: table.sentence(language, "wizard.dry_run.explain"),
    "wizard.dry_run.start": lambda table, language: table.sentence(language, "wizard.dry_run.start"),
}

#: The not-triggered outcome a silent profile's record carries.
DRY_RUN_OUTCOME_NOT_TRIGGERED = "not_triggered"

#: The generated document's list of credential references.
SECRETS_LIST = "secrets"
