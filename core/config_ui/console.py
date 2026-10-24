"""The console: « est-ce que ça marche ? » answered from the feed (phase 9 R7).

The console page shows the runtime state (running under this UI, not
running, or another process this UI does not supervise), each enabled module
ready or degraded — a degraded one with its cause and a next action — the
supervision feed's entries newest first, the configured grants, and a link to
the existing editor. Every sentence comes from the string table (R2); every
value shown is module- or runtime-owned data and is passed through the
caller's escape, which redacts the UI's secret set (R4).

:func:`read_feed` reads the runtime's feed file (``<base>.feed.jsonl``, plan
P2) with a bounded read, tolerating a missing or partly written file;
:func:`reason_sentence` maps every reason token the runtime emits to a
plain-language sentence, and an unknown token to the generic sentence naming
it, never to an empty line. :func:`render_outcome` shows what a Start or
Stop came to: the cause, a table sentence chosen from the outcome alone, and
the raw detail, the redacted stderr tail, behind a closed disclosure. This
module opens no socket and writes no file.
"""

from __future__ import annotations

import html
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.config_ui.i18n import StringTable
from core.contracts import (
    _INPUT_EVENT_TYPE,
    TRACE_ACTION_COMPLETED,
    TRACE_BRAIN_ADMISSION_ACCEPTED,
    TRACE_BRAIN_ADMISSION_REJECTED,
    TRACE_BRAIN_RUN_COMPLETED,
    TRACE_BRAIN_RUN_STARTED,
    TRACE_INPUT_TRIGGER_ACCEPTED,
    TRACE_INPUT_TRIGGER_REJECTED,
    TRACE_MODULE_DEGRADED,
    TRACE_MODULE_READY,
    TRACE_MODULE_STOPPED,
)
from core.main import (
    FEED_MAX_ENTRIES,
    FEED_MAX_ENTRY_BYTES,
    FEED_RUNTIME_STARTED,
    FEED_RUNTIME_STOPPING,
)

#: The most bytes :func:`read_feed` reads: the feed's full retention, each
#: entry at its bound plus its newline. A larger file is read from its end.
FEED_READ_BYTES = FEED_MAX_ENTRIES * (FEED_MAX_ENTRY_BYTES + 1)

#: The three runtime states the console names (R7).
STATE_RUNNING_HERE = "running_here"
STATE_NOT_RUNNING = "not_running"
STATE_UNSUPERVISED = "unsupervised"

#: A module's state on the console: the status record's, or unknown when no
#: live process reports it.
MODULE_READY = "ready"
MODULE_DEGRADED = "degraded"
MODULE_UNKNOWN = "unknown"

#: The prefix of every reason sentence's key: ``reason.<token>``.
REASON_KEY_PREFIX = "reason."

#: The decision prefixes a trigger reason carries (``rejected:<token>``).
_DECISION_PREFIXES = ("accepted:", "rejected:")

#: The link to the existing editor, which is unchanged (R7, A7).
EDITOR_LINK_PATH = "/"

#: The endpoint the page polls (R7), its cursor parameter, and the parameter
#: carrying the cursor entry's time, which tells one runtime's feed from the
#: next one's (each new runtime numbers its feed from 1 again).
FEED_ROUTE = "/feed"
FEED_CURSOR = "after"
FEED_CURSOR_AT = "at"

#: The longest cursor time the endpoint accepts: a feed time is far shorter.
FEED_CURSOR_AT_MAX = 64

#: How often the page polls the feed, in milliseconds.
POLL_INTERVAL_MS = 2000

#: The console's two state-changing routes (R7): CSRF-guarded POSTs.
START_ROUTE = "/start"
STOP_ROUTE = "/stop"

#: What a Start can come to (R7). The cause sentence is chosen from this
#: token alone — from the exit status and the startup phase, never from the
#: child's output — so it can quote nothing of the raw detail.
START_STARTED = "started"
START_UNCONFIRMED = "unconfirmed"
START_EXITED = "exited"
START_LAUNCH_FAILED = "launch_failed"
START_CLOSING = "closing"
START_ANOTHER_INSTANCE = "another_instance"
START_RUNNING_HERE = "running_here"

#: What a Stop can come to (R7).
STOP_STOPPED = "stopped"
STOP_UNCONFIRMED = "unconfirmed"
STOP_NOT_OURS = "not_ours"
STOP_NOTHING = "nothing"

ACTION_START = "start"
ACTION_STOP = "stop"

Escape = Callable[[Any], str]


def _plain_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


# --------------------------------------------------------------------------- #
# Reading the feed
# --------------------------------------------------------------------------- #


def _entry(line: bytes) -> dict[str, Any] | None:
    """One feed line as an entry, or ``None`` when it is not a whole one."""

    try:
        document = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    sequence = document.get("seq")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        return None
    if not isinstance(document.get("fact"), str):
        return None
    return document


def read_feed(path: str | os.PathLike[str], after: int | None = None) -> list[dict[str, Any]]:
    """The feed's entries whose sequence is past *after*, oldest first.

    At most :data:`FEED_READ_BYTES` are read, from the end of the file; a
    missing or unreadable file reads as empty, and a line that is not a whole
    entry (a partly written last line, the cut first line of a file read from
    its end, anything that is not an object with a positive ``seq`` and a
    ``fact``) is skipped. Entries are returned in sequence order, each
    sequence once.
    """

    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - FEED_READ_BYTES)
            handle.seek(start)
            data = handle.read(FEED_READ_BYTES)
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        # The read began inside a line: its tail is not an entry.
        lines = lines[1:]
    entries: dict[int, dict[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        entry = _entry(line)
        if entry is None:
            continue
        if after is not None and entry["seq"] <= after:
            continue
        entries[entry["seq"]] = entry
    return [entries[sequence] for sequence in sorted(entries)]


def feed_cursor(entries: Sequence[Mapping[str, Any]], after: int | None = None) -> int:
    """The cursor after *entries*: the newest sequence, else *after* (or 0)."""

    sequences = [entry["seq"] for entry in entries if isinstance(entry.get("seq"), int)]
    return max(sequences, default=after or 0)


def _entry_time(entry: Mapping[str, Any] | None) -> str | None:
    value = None if entry is None else entry.get("at")
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class FeedPage:
    """One poll's answer: the entries past the cursor, oldest first, the new
    cursor and its entry's time, and whether the feed is a new runtime's."""

    entries: list[dict[str, Any]]
    cursor: int
    at: str | None
    reset: bool


def poll_feed(
    path: str | os.PathLike[str], after: int, at: str | None = None
) -> FeedPage:
    """The feed past cursor *after* (whose entry was written at *at*).

    A restarted runtime numbers its feed from 1 again and replaces the file,
    so a cursor from the previous runtime would hide the new one's entries.
    The feed is a new one — *reset*, and every retained entry is answered —
    when its newest sequence is below the cursor, or when *at* is given and
    the entry at the cursor is not the one written at *at* (the new runtime
    already passed the old cursor, or the cursor entry is no longer kept).
    """

    retained = read_feed(path)
    newest = feed_cursor(retained)
    marker = next((entry for entry in retained if entry["seq"] == after), None)
    reset = after > 0 and (newest < after or (bool(at) and _entry_time(marker) != at))
    if reset:
        entries = retained
        cursor = newest
    else:
        entries = [entry for entry in retained if entry["seq"] > after]
        cursor = feed_cursor(entries, after)
    if entries:
        time = _entry_time(entries[-1])
    elif reset:
        time = None
    else:
        time = _entry_time(marker) or at
    return FeedPage(entries, cursor, time, reset)


# --------------------------------------------------------------------------- #
# Reason sentences (R7, AC20)
# --------------------------------------------------------------------------- #


def _reason_data(table: StringTable, language: str, token: str) -> str | None:
    """The sentence the table holds for *token* itself, if any.

    Reason sentences are data keyed by the token the runtime emits: the
    table is read by ``reason.<token>``, and only a sentence without
    placeholders is one (AC20 checks every emitted token has one).
    """

    key = REASON_KEY_PREFIX + token
    if table.shape.get(key) != frozenset():
        return None
    sentences = table.sentences.get(language) or table.sentences.get("en") or {}
    text = sentences.get(key)
    return text if isinstance(text, str) and text.strip() else None


def _family_sentence(table: StringTable, language: str, token: str) -> str | None:
    """The sentence of a token built from a trigger's own name (R1, R3):
    ``<type>_match``, ``<type>_hook_error``, ``audience_<role>``, … The name
    is a placeholder value, never part of a key."""

    def subject(suffix: str) -> str | None:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
        return None

    if token.startswith("audience_"):
        role = token[len("audience_"):]
        if role.endswith("_untrusted") and len(role) > len("_untrusted"):
            return table.sentence(
                language, "reason.family.audience_untrusted", role=role[: -len("_untrusted")]
            )
        if role:
            return table.sentence(language, "reason.family.audience", role=role)
    if token.startswith("event_kind_") and len(token) > len("event_kind_"):
        return table.sentence(language, "reason.family.event_kind", kind=token[len("event_kind_"):])
    if (name := subject("_hook_error")) is not None:
        return table.sentence(language, "reason.family.hook_error", name=name)
    if (name := subject("_hook_timeout")) is not None:
        return table.sentence(language, "reason.family.hook_timeout", name=name)
    if (name := subject("_hook_unavailable")) is not None:
        return table.sentence(language, "reason.family.hook_unavailable", name=name)
    if (name := subject("_kind_mismatch")) is not None:
        return table.sentence(language, "reason.family.kind_mismatch", name=name)
    if (name := subject("_probability_miss")) is not None:
        return table.sentence(language, "reason.family.probability_miss", name=name)
    if (name := subject("_no_match")) is not None:
        return table.sentence(language, "reason.family.no_match", name=name)
    if (name := subject("_match")) is not None:
        return table.sentence(language, "reason.family.match", name=name)
    return None


def reason_sentence(token: Any, language: str, table: StringTable) -> str:
    """The plain-language sentence of reason *token* in *language* (R7).

    A trigger decision's ``accepted:``/``rejected:`` prefix and an invariant
    rejection's ``:<field>`` detail are not part of the sentence: the token
    before them is. A token with no sentence of its own, nor of its family,
    renders the generic sentence naming it — never an empty line.
    """

    language = table.language_or_default(language)
    text = str(token) if token is not None else ""
    for prefix in _DECISION_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    base = text.split(":", 1)[0]
    if base:
        found = _reason_data(table, language, base)
        if found is None:
            found = _family_sentence(table, language, base)
        if found is not None:
            return found
    return table.unknown_reason(language, str(token) if token is not None else "")


# --------------------------------------------------------------------------- #
# Runtime state and module health
# --------------------------------------------------------------------------- #


def runtime_state(*, alive: bool, supervised: bool, starting: bool = False) -> str:
    """The console's runtime state from the status record and the child (R7):
    a live record whose pid is this UI's child is running here; a live one
    with another pid is another process this UI does not supervise. With no
    live record, a child of this UI still alive (*starting*: it has not
    published a usable record yet) is running here too, so Stop is offered."""

    if not alive:
        return STATE_RUNNING_HERE if starting else STATE_NOT_RUNNING
    return STATE_RUNNING_HERE if supervised else STATE_UNSUPERVISED


@dataclass(frozen=True)
class ModuleHealth:
    """One enabled module as the console shows it."""

    name: str
    state: str
    #: The cause the module gave on its latest ``module.degraded``: its own
    #: text, shown as quoted data (it is never a sentence of the product).
    cause: str | None = None


def module_health(
    enabled: Sequence[str],
    reported: Mapping[str, str] | None,
    feed: Sequence[Mapping[str, Any]],
) -> list[ModuleHealth]:
    """Each enabled module's state from the live status record (*reported*,
    ``None`` when no live process reports one) and, for a degraded one, the
    cause of its latest ``module.degraded`` in the feed."""

    causes: dict[str, str | None] = {}
    for entry in feed:
        module = entry.get("module")
        if not isinstance(module, str):
            continue
        if entry.get("fact") == TRACE_MODULE_DEGRADED:
            reason = entry.get("reason")
            causes[module] = reason if isinstance(reason, str) and reason else None
        elif entry.get("fact") in (TRACE_MODULE_READY, TRACE_MODULE_STOPPED):
            causes.pop(module, None)
    health: list[ModuleHealth] = []
    for name in enabled:
        state = None if reported is None else reported.get(name)
        if state == MODULE_DEGRADED:
            health.append(ModuleHealth(name, MODULE_DEGRADED, causes.get(name)))
        elif state == MODULE_READY:
            health.append(ModuleHealth(name, MODULE_READY))
        else:
            health.append(ModuleHealth(name, MODULE_UNKNOWN))
    return health


@dataclass(frozen=True)
class Grant:
    """One configured grant: what the console lists (R7)."""

    rule_id: str
    action: str
    principal: str
    destination: str


def grants_of(rules: Sequence[Any], *, any_action: str = "*") -> list[Grant]:
    """The configured ``actions`` rules as grants, in order; an entry that is
    not a mapping is skipped (the editor reports it)."""

    def listing(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        if isinstance(value, Mapping):
            return ", ".join(f"{key}: {item}" for key, item in value.items())
        return str(value)

    grants: list[Grant] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        action = rule.get("action_name", any_action)
        grants.append(
            Grant(
                rule_id=listing(rule.get("rule_id")),
                action=any_action if action is None else listing(action),
                principal=listing(rule.get("principals")),
                destination=listing(rule.get("destination")),
            )
        )
    return grants


# --------------------------------------------------------------------------- #
# Rendering (R7, R4)
# --------------------------------------------------------------------------- #


def _text(entry: Mapping[str, Any], name: str) -> str:
    value = entry.get(name)
    return "" if value is None else str(value)


def _quoted(value: str, escape: Escape, css: str) -> str:
    return f'<q class="{css}">{escape(value)}</q>' if value else ""


def _reason_markup(entry: Mapping[str, Any], language: str, table: StringTable, escape: Escape) -> str:
    token = entry.get("reason")
    if token is None:
        token = entry.get("error_code")
    if token is None:
        return ""
    sentence = reason_sentence(token, language, table)
    return f' <span class="reason" data-reason="{escape(token)}">{escape(sentence)}</span>'


def _fact_markup(
    entry: Mapping[str, Any], language: str, table: StringTable, escape: Escape
) -> str:
    """The sentence of one feed entry, its data quoted and escaped."""

    fact = entry["fact"]
    module = _text(entry, "module")
    reason = _reason_markup(entry, language, table, escape)
    if fact == _INPUT_EVENT_TYPE:
        sentence = table.sentence(
            language,
            "console.fact.arrived",
            author=_text(entry, "author"),
            platform=_text(entry, "platform"),
            kind=_text(entry, "kind"),
        )
        return escape(sentence) + " " + _quoted(_text(entry, "excerpt"), escape, "excerpt")
    if fact == TRACE_INPUT_TRIGGER_ACCEPTED:
        sentence = table.sentence(
            language, "console.fact.trigger_accepted", input=_text(entry, "input")
        )
        return escape(sentence) + reason
    if fact == TRACE_INPUT_TRIGGER_REJECTED:
        sentence = table.sentence(
            language, "console.fact.trigger_rejected", input=_text(entry, "input")
        )
        return escape(sentence) + reason
    if fact == TRACE_BRAIN_ADMISSION_ACCEPTED:
        return escape(table.sentence(language, "console.fact.admission_accepted"))
    if fact == TRACE_BRAIN_ADMISSION_REJECTED:
        return escape(table.sentence(language, "console.fact.admission_rejected")) + reason
    if fact == TRACE_BRAIN_RUN_STARTED:
        return escape(
            table.sentence(language, "console.fact.run_started", run_id=_text(entry, "run_id"))
        )
    if fact == TRACE_BRAIN_RUN_COMPLETED:
        sentence = table.sentence(
            language,
            "console.fact.run_completed",
            run_id=_text(entry, "run_id"),
            status=_text(entry, "status"),
        )
        return escape(sentence) + reason
    if fact == TRACE_ACTION_COMPLETED:
        sentence = table.sentence(
            language, "console.fact.action_refused", action=_text(entry, "action")
        )
        return escape(sentence) + reason
    if fact == TRACE_MODULE_READY:
        return escape(table.sentence(language, "console.fact.module_ready", module=module))
    if fact == TRACE_MODULE_DEGRADED:
        return _degraded_markup(module, entry.get("reason"), language, table, escape)
    if fact == TRACE_MODULE_STOPPED:
        return escape(table.sentence(language, "console.fact.module_stopped", module=module))
    if fact == FEED_RUNTIME_STARTED:
        return escape(table.sentence(language, "console.fact.runtime_started"))
    if fact == FEED_RUNTIME_STOPPING:
        return escape(table.sentence(language, "console.fact.runtime_stopping"))
    return escape(table.sentence(language, "console.fact.other", fact=fact))


def _degraded_markup(
    module: str, cause: Any, language: str, table: StringTable, escape: Escape
) -> str:
    """A degraded module: the table's sentence, the module's own cause as
    quoted data (redacted by *escape*), and the table's next action."""

    sentence = escape(table.sentence(language, "console.module.degraded", module=module))
    if isinstance(cause, str) and cause:
        label = escape(table.sentence(language, "console.module.cause"))
        quoted = f' <span class="cause">{label} {_quoted(cause, escape, "cause-text")}</span>'
    else:
        quoted = (
            f' <span class="cause">{escape(table.sentence(language, "console.module.no_cause"))}</span>'
        )
    following = escape(table.sentence(language, "console.module.degraded.next"))
    return f'{sentence}{quoted} <span class="next">{following}</span>'


def render_feed_item(
    entry: Mapping[str, Any], language: str, table: StringTable, *, escape: Escape = _plain_escape
) -> str:
    """One feed entry as a list item, carrying its sequence and fact."""

    return (
        f'<li class="feed-item" data-seq="{int(entry["seq"])}" data-fact="{escape(entry["fact"])}">'
        f"{_fact_markup(entry, language, table, escape)}</li>"
    )


def render_feed_items(
    feed: Sequence[Mapping[str, Any]],
    language: str,
    table: StringTable,
    *,
    escape: Escape = _plain_escape,
) -> list[str]:
    """Every entry of *feed* as a list item, newest first."""

    ordered = sorted(feed, key=lambda entry: entry["seq"], reverse=True)
    return [render_feed_item(entry, language, table, escape=escape) for entry in ordered]


def _state_markup(state: str, language: str, table: StringTable, escape: Escape) -> str:
    if state == STATE_RUNNING_HERE:
        sentence = table.sentence(language, "console.state.running_here")
    elif state == STATE_UNSUPERVISED:
        sentence = table.sentence(language, "console.state.unsupervised")
    else:
        sentence = table.sentence(language, "console.state.not_running")
    return (
        f'<section id="runtime"><h2>{escape(table.sentence(language, "console.state.title"))}</h2>'
        f'<p id="runtime-state" data-state="{escape(state)}">{escape(sentence)}</p></section>'
    )


def _health_markup(
    health: Sequence[ModuleHealth], language: str, table: StringTable, escape: Escape
) -> str:
    rows: list[str] = []
    for module in health:
        if module.state == MODULE_READY:
            text = escape(table.sentence(language, "console.module.ready", module=module.name))
        elif module.state == MODULE_DEGRADED:
            text = _degraded_markup(module.name, module.cause, language, table, escape)
        else:
            text = escape(table.sentence(language, "console.module.unknown", module=module.name))
        rows.append(
            f'<li class="module" data-module="{escape(module.name)}" '
            f'data-module-state="{escape(module.state)}">{text}</li>'
        )
    if not rows:
        body = f'<p>{escape(table.sentence(language, "console.modules.none"))}</p>'
    else:
        body = f'<ul id="modules">{"".join(rows)}</ul>'
    return (
        f'<section id="health"><h2>{escape(table.sentence(language, "console.modules.title"))}</h2>'
        f"{body}</section>"
    )


def _grants_markup(grants: Sequence[Grant], language: str, table: StringTable, escape: Escape) -> str:
    """The configured grants, read-only: the console adds or widens none."""

    title = escape(table.sentence(language, "console.grants.title"))
    if not grants:
        none = escape(table.sentence(language, "console.grants.none"))
        return f'<section id="grants-section"><h2>{title}</h2><p id="grants-none">{none}</p></section>'
    headings = "".join(
        f"<th>{escape(text)}</th>"
        for text in (
            table.sentence(language, "console.grants.rule"),
            table.sentence(language, "console.grants.action"),
            table.sentence(language, "console.grants.principal"),
            table.sentence(language, "console.grants.destination"),
        )
    )
    rows = "".join(
        f'<tr class="grant"><td><code>{escape(grant.rule_id)}</code></td>'
        f"<td>{escape(grant.action)}</td><td>{escape(grant.principal)}</td>"
        f"<td>{escape(grant.destination)}</td></tr>"
        for grant in grants
    )
    return (
        f'<section id="grants-section"><h2>{title}</h2>'
        f'<table id="grants"><tr>{headings}</tr>{rows}</table></section>'
    )


@dataclass(frozen=True)
class RuntimeOutcome:
    """What one Start or Stop came to, as the console reports it (R7).

    *raw_detail* is the child's bounded stderr tail (or the launch error),
    already redacted by the caller: quoted data, never translated, shown
    only behind the closed disclosure. It never chooses the cause.
    """

    action: str
    outcome: str
    exit_status: int | None = None
    raw_detail: str = ""


def _outcome_sentences(outcome: RuntimeOutcome, language: str, table: StringTable) -> tuple[str, str]:
    """The cause and the next action of *outcome*: table sentences chosen by
    its action and outcome tokens only, with no placeholder."""

    if outcome.action == ACTION_STOP:
        sentences = {
            STOP_STOPPED: (
                table.sentence(language, "console.stop.stopped"),
                table.sentence(language, "console.stop.stopped.next"),
            ),
            STOP_UNCONFIRMED: (
                table.sentence(language, "console.stop.unconfirmed"),
                table.sentence(language, "console.stop.unconfirmed.next"),
            ),
            STOP_NOT_OURS: (
                table.sentence(language, "console.stop.not_ours"),
                table.sentence(language, "console.stop.not_ours.next"),
            ),
            STOP_NOTHING: (
                table.sentence(language, "console.stop.nothing"),
                table.sentence(language, "console.stop.nothing.next"),
            ),
        }
        return sentences.get(outcome.outcome, sentences[STOP_NOTHING])
    sentences = {
        START_STARTED: (
            table.sentence(language, "console.start.started"),
            table.sentence(language, "console.start.started.next"),
        ),
        START_UNCONFIRMED: (
            table.sentence(language, "console.start.unconfirmed"),
            table.sentence(language, "console.start.unconfirmed.next"),
        ),
        START_EXITED: (
            table.sentence(language, "console.start.exited"),
            table.sentence(language, "console.start.exited.next"),
        ),
        START_LAUNCH_FAILED: (
            table.sentence(language, "console.start.launch_failed"),
            table.sentence(language, "console.start.launch_failed.next"),
        ),
        START_CLOSING: (
            table.sentence(language, "console.start.closing"),
            table.sentence(language, "console.start.closing.next"),
        ),
        START_ANOTHER_INSTANCE: (
            table.sentence(language, "console.start.another_instance"),
            table.sentence(language, "console.start.another_instance.next"),
        ),
        START_RUNNING_HERE: (
            table.sentence(language, "console.start.running_here"),
            table.sentence(language, "console.start.running_here.next"),
        ),
    }
    return sentences.get(outcome.outcome, sentences[START_EXITED])


def outcome_title(outcome: RuntimeOutcome, language: str, table: StringTable) -> str:
    """The page title of a Start or Stop answer."""

    if outcome.action == ACTION_STOP:
        return table.sentence(language, "console.stop.title")
    return table.sentence(language, "console.start.title")


def render_outcome(
    outcome: RuntimeOutcome, language: str, table: StringTable, *, escape: Escape = _plain_escape
) -> str:
    """A Start or Stop outcome's two surfaces (R7, AC32).

    The **cause** — the table's sentence and next action in *language* — is
    always shown, outside any disclosure, and holds nothing of the raw
    detail. The **raw detail** — the redacted stderr tail, untranslated —
    is shown only inside one ``<details>`` element, closed by default, its
    label from the table; with no detail there is no disclosure.
    """

    language = table.language_or_default(language)
    cause, following = _outcome_sentences(outcome, language, table)
    parts = [
        f'<section id="runtime-outcome" data-action="{escape(outcome.action)}" '
        f'data-outcome="{escape(outcome.outcome)}">',
        f'<div id="outcome-cause"><p class="cause">{escape(cause)}</p>'
        f'<p class="next">{escape(following)}</p></div>',
    ]
    if outcome.exit_status is not None:
        status = int(outcome.exit_status)
        sentence = table.sentence(language, "console.start.exit_status", status=status)
        parts.append(f'<p id="exit-status" data-status="{status}">{escape(sentence)}</p>')
    if outcome.raw_detail:
        label = escape(table.sentence(language, "detail.disclosure"))
        parts.append(
            f'<details id="raw-detail"><summary>{label}</summary>'
            f"<pre>{escape(outcome.raw_detail)}</pre></details>"
        )
    parts.append("</section>")
    return "".join(parts)


def _controls_markup(state: str, csrf_field: str, language: str, table: StringTable, escape: Escape) -> str:
    """Start when nothing runs, Stop when the companion runs from this UI;
    another process this UI does not supervise gets neither (R7)."""

    if state == STATE_NOT_RUNNING:
        route, label, ident = START_ROUTE, table.sentence(language, "console.control.start"), "start"
    elif state == STATE_RUNNING_HERE:
        route, label, ident = STOP_ROUTE, table.sentence(language, "console.control.stop"), "stop"
    else:
        return ""
    return (
        f'<form id="{ident}-form" method="post" action="{route}">{csrf_field}'
        f'<button type="submit">{escape(label)}</button></form>'
    )


#: Polls :data:`FEED_ROUTE` with the newest sequence shown and its time, and
#: puts the new items first; a feed that is a new runtime's replaces the list.
#: The next poll is scheduled only once the previous one has answered, so no
#: two polls share a cursor, and at most :data:`FEED_MAX_ENTRIES` items are
#: kept, the oldest dropped first. The session cookie goes along (same
#: origin, relative URL).
_POLL_SCRIPT = (
    "(function () {"
    'var list = document.getElementById("feed");'
    "if (!list) { return; }"
    'var cursor = parseInt(list.getAttribute("data-cursor"), 10) || 0;'
    'var at = list.getAttribute("data-cursor-at") || "";'
    "function next() {"
    f"setTimeout(poll, {POLL_INTERVAL_MS});"
    "}"
    "function poll() {"
    f'fetch("{FEED_ROUTE}?{FEED_CURSOR}=" + cursor + "&{FEED_CURSOR_AT}=" + encodeURIComponent(at),'
    ' {credentials: "same-origin"})'
    ".then(function (answer) { return answer.ok ? answer.json() : null; })"
    ".then(function (document_) {"
    "if (!document_) { return; }"
    'if (document_.reset) { list.innerHTML = ""; }'
    "var items = document_.items || [];"
    "for (var i = items.length - 1; i >= 0; i--) {"
    'list.insertAdjacentHTML("afterbegin", items[i]);'
    "}"
    f"while (list.children.length > {FEED_MAX_ENTRIES}) {{"
    "list.removeChild(list.lastElementChild);"
    "}"
    'if (typeof document_.cursor === "number") { cursor = document_.cursor; }'
    'at = document_.at || "";'
    'list.setAttribute("data-cursor", String(cursor));'
    'list.setAttribute("data-cursor-at", at);'
    "}).catch(function () {}).then(next);"
    "}"
    "next();"
    "})();"
)


def render_console(
    state: str,
    feed: Sequence[Mapping[str, Any]],
    health: Sequence[ModuleHealth],
    grants: Sequence[Grant],
    language: str,
    table: StringTable,
    *,
    escape: Escape = _plain_escape,
    csrf_field: str = "",
    outcome: RuntimeOutcome | None = None,
) -> str:
    """The console page's body (R7): the runtime state, each enabled module,
    the feed newest first, the configured grants and the editor link.

    With *csrf_field* (the session's hidden CSRF input) the state carries
    its Start or Stop control; with *outcome*, the last Start or Stop's
    report comes first (:func:`render_outcome`).

    Every sentence is the table's in *language*; every value goes through
    *escape*, which the UI makes its redacting escape (R4).
    """

    language = table.language_or_default(language)
    items = render_feed_items(feed, language, table, escape=escape)
    cursor = feed_cursor(feed)
    newest = max(feed, key=lambda entry: entry["seq"], default=None)
    cursor_at = _entry_time(newest) or ""
    empty = (
        ""
        if items
        else f'<p id="feed-empty">{escape(table.sentence(language, "console.feed.empty"))}</p>'
    )
    title = escape(table.sentence(language, "console.title"))
    feed_title = escape(table.sentence(language, "console.feed.title"))
    editor = escape(table.sentence(language, "console.editor_link"))
    controls = _controls_markup(state, csrf_field, language, table, escape) if csrf_field else ""
    report = "" if outcome is None else render_outcome(outcome, language, table, escape=escape)
    return (
        f"<h1>{title}</h1>{report}"
        f"{_state_markup(state, language, table, escape)}{controls}"
        f"{_health_markup(health, language, table, escape)}"
        f'<section id="feed-section"><h2>{feed_title}</h2>{empty}'
        f'<ol id="feed" data-cursor="{cursor}" data-cursor-at="{escape(cursor_at)}">{"".join(items)}</ol></section>'
        f"{_grants_markup(grants, language, table, escape)}"
        f'<p><a id="editor-link" href="{EDITOR_LINK_PATH}">{editor}</a></p>'
        f"<script>{_POLL_SCRIPT}</script>"
    )
