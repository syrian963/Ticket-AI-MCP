# SPDX-License-Identifier: MIT

"""Two languages for the interface, and they are not the same as the ticket's.

The distinction matters and is easy to collapse by accident. A German team may
well want the tool's own buttons in English; an English-speaking developer
joining a German board still has to write the ticket in German. So there are
two settings and they are independent:

- `TICKET_AI_UI_LANGUAGE` - the chrome: headings, buttons, labels.
- `TICKET_AI_TICKET_LANGUAGE` - what the tool tells you to write tickets in,
  overriding what it measured in the corpus.

English and German only. A third language here would be a translation table
nobody maintains and a fourth would be worse; these are the two this was built
against, and an unknown code falls back to English rather than showing keys.

Findings are translated too, and they are **not** kept here. They live in
`messages.py` as data - a code plus the measurements - and the sentence is
built at the moment of display. The argument for leaving them in English was
that they carry counts over a named sample, and a half-translated sentence with
a number in it reads worse than an English one; seeing a German page say
*Übereinstimmung mit dem Hausstil* and then *The ticket has no description at
all* settled that. This module holds the chrome only.
"""

from __future__ import annotations

import os

DEFAULT = "en"
SUPPORTED = ("en", "de")

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "Ticket AI",
        "app.tagline": "What a good ticket looks like here, measured.",
        "tab.style": "House style",
        "tab.draft": "Check a draft",
        "tab.open": "Open tickets",
        "style.none": "No house style learned yet. Run ticket-ai learn first.",
        "style.sample": "Built from {n} tickets",
        "style.template": "The template",
        "style.blocks": "Sections that travel together",
        "style.length": "Length",
        "style.habits": "Habits",
        # The rows under it. They were the dictionary's own keys, printed
        # straight into the page - so a German reader got a German heading
        # over a list reading "labelled, assigned, list, checklist".
        "habit.labelled": "labelled",
        "habit.assigned": "assigned",
        "habit.list": "a list",
        "habit.checklist": "a checklist",
        "habit.code": "a code block",
        "habit.screenshot": "a screenshot",
        "habit.cross_ref": "links another ticket",
        "style.labels": "Labels in use",
        "style.language": "Tickets here are written in",
        "style.measured": "Measured from",
        "draft.title": "Title",
        "draft.title.hint": "The one line people scan on the board",
        "draft.body": "Description",
        "draft.body.hint": "Markdown. The sections above are what this team uses.",
        "draft.labels": "Labels",
        "draft.labels.hint": "Comma separated",
        "draft.check": "Check it",
        "draft.checking": "Checking…",
        "draft.empty": "Write something first.",
        "draft.template": "Insert the template",
        "review.alignment": "Alignment with house style",
        "review.checks": "over {n} checks",
        "review.unmeasurable": "not measurable - no convention was consistent enough to check",
        "review.findings": "Findings",
        "review.passed": "Already right",
        "review.clean": "Nothing to flag. This matches how the team writes tickets.",
        "review.fix": "Fix",
        "open.load": "Load the backlog",
        "open.loading": "Reading the tracker…",
        "open.worst": "Least aligned first",
        "open.clean": "matches the conventions that were checked",
        "open.none": "No open tickets.",
        "severity.high": "high",
        "severity.medium": "medium",
        "severity.low": "low",
        "error": "Something went wrong",
        "note.alignment": (
            "Alignment is not quality. It measures distance from the tickets that "
            "historically got built here."
        ),
    },
    "de": {
        "app.title": "Ticket AI",
        "app.tagline": "Wie ein gutes Ticket hier aussieht, gemessen.",
        "tab.style": "Hausstil",
        "tab.draft": "Entwurf prüfen",
        "tab.open": "Offene Tickets",
        "style.none": "Noch kein Hausstil gelernt. Zuerst ticket-ai learn ausführen.",
        "style.sample": "Aus {n} Tickets ermittelt",
        "style.template": "Die Vorlage",
        "style.blocks": "Abschnitte, die zusammen auftreten",
        "style.length": "Länge",
        "style.habits": "Gewohnheiten",
        "habit.labelled": "mit Labels",
        "habit.assigned": "zugewiesen",
        "habit.list": "eine Liste",
        "habit.checklist": "eine Checkliste",
        "habit.code": "ein Code-Block",
        "habit.screenshot": "ein Screenshot",
        "habit.cross_ref": "verweist auf ein anderes Ticket",
        "style.labels": "Verwendete Labels",
        "style.language": "Tickets werden hier geschrieben auf",
        "style.measured": "Gemessen an",
        "draft.title": "Titel",
        "draft.title.hint": "Die eine Zeile, die auf dem Board gelesen wird",
        "draft.body": "Beschreibung",
        "draft.body.hint": "Markdown. Die Abschnitte oben sind die des Teams.",
        "draft.labels": "Labels",
        "draft.labels.hint": "Kommagetrennt",
        "draft.check": "Prüfen",
        "draft.checking": "Wird geprüft…",
        "draft.empty": "Erst etwas schreiben.",
        "draft.template": "Vorlage einfügen",
        "review.alignment": "Übereinstimmung mit dem Hausstil",
        "review.checks": "über {n} Prüfungen",
        "review.unmeasurable": "nicht messbar - keine Konvention war einheitlich genug",
        "review.findings": "Befunde",
        "review.passed": "Passt schon",
        "review.clean": "Nichts zu beanstanden. Das entspricht dem Hausstil.",
        "review.fix": "Lösung",
        "open.load": "Backlog laden",
        "open.loading": "Tracker wird gelesen…",
        "open.worst": "Geringste Übereinstimmung zuerst",
        "open.clean": "entspricht den geprüften Konventionen",
        "open.none": "Keine offenen Tickets.",
        "severity.high": "hoch",
        "severity.medium": "mittel",
        "severity.low": "niedrig",
        "error": "Etwas ist schiefgelaufen",
        "note.alignment": (
            "Übereinstimmung ist nicht Qualität. Gemessen wird der Abstand zu den "
            "Tickets, die hier tatsächlich umgesetzt wurden."
        ),
    },
}


def normalise(code: str | None) -> str:
    """Reduce a language code to one this build has strings for.

    `de-DE`, `de_AT` and `DE` are all German. Anything else is English,
    because a missing translation should read as plain English rather than as
    a key nobody can act on.
    """
    if not code:
        return DEFAULT
    short = code.strip().lower().replace("_", "-").split("-")[0]
    return short if short in SUPPORTED else DEFAULT


def ui_language(override: str | None = None) -> str:
    return normalise(override or os.environ.get("TICKET_AI_UI_LANGUAGE"))


def ticket_language(override: str | None = None) -> str | None:
    """What to tell people to write tickets in, or None to use the corpus.

    Unset is the useful default: the corpus already knows, and a measurement
    beats a setting. The override is for a team mid-switch, where what the
    board says is not what the board wants.
    """
    value = (override or os.environ.get("TICKET_AI_TICKET_LANGUAGE") or "").strip()
    if not value:
        return None
    return normalise(value)


class Translator:
    """Looks up a string, falling back to English and then to the key itself."""

    __slots__ = ("language",)

    def __init__(self, language: str | None = None) -> None:
        self.language = normalise(language)

    def __call__(self, key: str, **fields: object) -> str:
        table = _STRINGS.get(self.language, _STRINGS[DEFAULT])
        text = table.get(key) or _STRINGS[DEFAULT].get(key) or key
        return text.format(**fields) if fields else text
