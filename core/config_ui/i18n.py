"""The string table of the first run: one data file per language (R2, plan DD4).

Every sentence the wizard and the console show comes from a JSON file under
``strings/``: a flat object mapping a key to a sentence, the file's stem being
the language code (``en.json`` is ``en``). ``en.json`` is the reference.
The shipped tables (:data:`SHIPPED`) must each be complete and well-formed —
neither may miss a key the other has — or the table refuses to load, with a
plain cause naming the table and how many keys it misses. Any other file is
offered only when it matches the reference exactly — the same key set, a
non-empty string for every key, and for every key the same set of
``{placeholder}`` names — so a third language is added by dropping a file
beside the shipped ones, with no code change, and an incomplete one is never
offered (it is listed in :attr:`StringTable.refused` with the same cause).

A language chosen for a session is never replaced by another: asking for a
language that is not offered raises :class:`LanguageUnavailable` with its
cause, so a French session refuses rather than render English (P19F3).

Each file names itself through :data:`NAME_KEY`, which :meth:`languages`
returns as the display name. The existing editor's text is not here (R2).
"""

from __future__ import annotations

import json
import string
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: The shipped strings directory (package data).
STRINGS_DIRECTORY = Path(__file__).resolve().parent / "strings"

#: The reference language: its key set and placeholder sets define the table.
REFERENCE = "en"

#: The languages the product ships: each must validate, or nothing loads.
SHIPPED = (REFERENCE, "fr")

#: The key holding each language's own display name.
NAME_KEY = "language.name"

#: The generic sentence for a reason token that has no sentence of its own;
#: its one placeholder is ``token``.
UNKNOWN_REASON_KEY = "reason.unknown"

_FORMATTER = string.Formatter()


class StringTableError(ValueError):
    """A shipped file is missing or malformed, or a lookup is invalid."""


class LanguageUnavailable(StringTableError):
    """A chosen language that is not offered: refused with its :attr:`cause`,
    never replaced by the default."""

    def __init__(self, language: str, cause: str) -> None:
        super().__init__(cause)
        self.language = language
        self.cause = cause


def placeholders(text: str) -> frozenset[str]:
    """The ``{name}`` placeholders of *text*.

    Only bare identifiers are placeholders: a positional (``{}``, ``{0}``),
    attribute, index or conversion field, or unbalanced braces, raise
    :class:`StringTableError`, so a sentence can never reach into a value.
    """

    try:
        fields = list(_FORMATTER.parse(text))
    except ValueError as error:
        raise StringTableError(f"malformed braces ({error})") from None
    names: set[str] = set()
    for _, name, spec, conversion in fields:
        if name is None:
            continue
        if not name.isidentifier() or spec or conversion:
            raise StringTableError(f"placeholder {{{name}}} is not a bare name")
        names.add(name)
    return frozenset(names)


def _read(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StringTableError(f"cannot be read as JSON ({type(error).__name__})") from None
    if not isinstance(document, dict) or not document:
        raise StringTableError("is not a non-empty object")
    for key, value in document.items():
        if not isinstance(value, str) or not value.strip():
            raise StringTableError(f"key {key!r} is not a non-empty string")
    return document


def _shape(sentences: Mapping[str, str]) -> dict[str, frozenset[str]]:
    shape: dict[str, frozenset[str]] = {}
    for key, text in sentences.items():
        try:
            shape[key] = placeholders(text)
        except StringTableError as error:
            raise StringTableError(f"key {key!r}: {error}") from None
    return shape


def _keys(count: int) -> str:
    return f"{count} key" if count == 1 else f"{count} keys"


def _mismatch(
    name: str, reference: Mapping[str, frozenset[str]], other: Mapping[str, frozenset[str]]
) -> str:
    """The plain cause *other* (the file *name*) does not validate against
    *reference*: the table and a count, never the keys or sentences."""

    missing = len(reference.keys() - other.keys())
    if missing:
        return f"{name}: {_keys(missing)} missing"
    extra = len(other.keys() - reference.keys())
    if extra:
        return f"{name}: {_keys(extra)} extra"
    different = sum(1 for key in reference if reference[key] != other[key])
    return f"{name}: placeholders differ for {_keys(different)}" if different else ""


@dataclass(frozen=True)
class StringTable:
    """The offered languages and their sentences; build it with :meth:`load`."""

    sentences: Mapping[str, Mapping[str, str]]
    shape: Mapping[str, frozenset[str]]
    refused: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path | str = STRINGS_DIRECTORY) -> StringTable:
        """Read every ``*.json`` of *directory* and keep the complete ones.

        Raises :class:`StringTableError` when ``en.json`` is absent or not a
        well-formed table (a non-empty object of non-empty strings with bare
        placeholders, holding :data:`NAME_KEY` and :data:`UNKNOWN_REASON_KEY`),
        and when a :data:`SHIPPED` table is absent, malformed, or misses a key
        another shipped table has: an incomplete shipped table is a refusal,
        never a withdrawn language.
        """

        directory = Path(directory)
        reference_path = directory / f"{REFERENCE}.json"
        try:
            reference = _read(reference_path)
            shape = _shape(reference)
        except StringTableError as error:
            raise StringTableError(f"{reference_path.name} {error}") from None
        for required in (NAME_KEY, UNKNOWN_REASON_KEY):
            if required not in shape:
                raise StringTableError(f"{reference_path.name} lacks the key {required!r}")
        if shape[UNKNOWN_REASON_KEY] != {"token"}:
            raise StringTableError(f"{UNKNOWN_REASON_KEY!r} must have exactly {{token}}")
        sentences: dict[str, Mapping[str, str]] = {REFERENCE: reference}
        for code in SHIPPED:
            if code == REFERENCE:
                continue
            path = directory / f"{code}.json"
            try:
                candidate = _read(path)
                other = _shape(candidate)
            except StringTableError as error:
                raise StringTableError(f"{path.name} {error}") from None
            # The reference is checked the same way: it misses what it lacks.
            missing = len(other.keys() - shape.keys())
            if missing:
                raise StringTableError(f"{reference_path.name}: {_keys(missing)} missing")
            reason = _mismatch(path.name, shape, other)
            if reason:
                raise StringTableError(reason)
            sentences[code] = candidate
        refused: dict[str, str] = {}
        for path in sorted(directory.glob("*.json")):
            code = path.stem
            if code in SHIPPED:
                continue
            try:
                candidate = _read(path)
                reason = _mismatch(path.name, shape, _shape(candidate))
            except StringTableError as error:
                reason = f"{path.name} {error}"
            if reason:
                refused[code] = reason
            else:
                sentences[code] = candidate
        return cls(sentences=sentences, shape=shape, refused=refused)

    def languages(self) -> list[tuple[str, str]]:
        """``(code, display name)`` of every offered language, by code."""

        return [(code, self.sentences[code][NAME_KEY]) for code in sorted(self.sentences)]

    def offers(self, language: str) -> bool:
        return language in self.sentences

    def cause(self, language: str) -> str:
        """Why *language* is not offered: its file's refusal, when it has one."""

        return self.refused.get(language, "language not offered")

    def language_or_default(self, language: str | None) -> str:
        """*language* when offered; the reference (the R2 default) only when
        none is chosen. A chosen language that is not offered raises
        :class:`LanguageUnavailable`: a session never changes language."""

        if not language:
            return REFERENCE
        if not self.offers(language):
            raise LanguageUnavailable(language, self.cause(language))
        return language

    def sentence(self, language: str, key: str, **values: object) -> str:
        """The sentence *key* in *language*, formatted with *values*.

        *values* must name exactly the key's placeholders: a missing or an
        extra value raises :class:`StringTableError`, as do an unoffered
        language and an unknown key.
        """

        if language not in self.sentences:
            raise StringTableError(f"language {language!r} is not offered")
        if key not in self.shape:
            raise StringTableError(f"unknown string key {key!r}")
        expected = self.shape[key]
        if set(values) != expected:
            raise StringTableError(
                f"key {key!r} takes {sorted(expected)}, given {sorted(values)}"
            )
        text = self.sentences[language][key]
        return text.format_map({name: str(value) for name, value in values.items()})

    def unknown_reason(self, language: str, token: str) -> str:
        """The generic sentence for a reason with no sentence, naming *token*."""

        return self.sentence(language, UNKNOWN_REASON_KEY, token=token)
