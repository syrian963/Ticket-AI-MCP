# SPDX-License-Identifier: MIT

"""The words for every finding, in each language.

Findings used to be built as English prose inside `review.py`, and the argument
for leaving them that way was that they carry counts and a half-translated
sentence with a number in it reads worse than an English one. Seeing it in
context killed that argument: a German page that says *Übereinstimmung mit dem
Hausstil* and then *The ticket has no description at all* is not carrying a
number carefully, it is just half-translated the other way round. Numbers
interpolate into German exactly as well as into English.

So a finding is now data - a code and the measurements behind it - and the
sentence is built here, at the moment of display, in whatever language the
reader asked for.

Two rules for anything added below:

- **Every message names its measurement.** That is the whole difference
  between this tool and a linter with opinions, and it has to survive
  translation. A German string that drops the count is a worse regression than
  no German string.
- **Both languages carry the same numbers.** The parameters are shared, so a
  translation cannot quietly say something the English does not.
"""

from __future__ import annotations

from typing import Any

from .i18n import DEFAULT, normalise

# what / why / fix, per finding code, per language. The `why` half is the one
# that carries the evidence; if a template here has no placeholder in it,
# something has gone wrong.
_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "empty_description": {
        "en": {
            "what": "The ticket has no description at all.",
            "why": (
                "Every one of the {total} tickets that shipped in {project} had one, "
                "with a median of {median} characters."
            ),
            "fix": "Write the description before anything else here is worth checking.",
        },
        "de": {
            "what": "Das Ticket hat überhaupt keine Beschreibung.",
            "why": (
                "Alle {total} Tickets, die in {project} umgesetzt wurden, hatten eine, "
                "im Median {median} Zeichen."
            ),
            "fix": "Zuerst die Beschreibung schreiben - alles andere hier prüft sich erst danach.",
        },
    },
    "missing_section": {
        "en": {
            "what": "No {heading!r} section.",
            "why": "{count} of the {total} exemplar tickets ({pct}) have one.",
            "fix": "Add the {heading!r} heading and fill it in.",
        },
        "de": {
            "what": "Kein Abschnitt {heading!r}.",
            "why": "{count} von {total} Beispieltickets ({pct}) haben einen.",
            "fix": "Die Überschrift {heading!r} ergänzen und ausfüllen.",
        },
    },
    "missing_conditional_section": {
        "en": {
            "what": "Has a {when!r} section but no {then!r}.",
            "why": (
                "{count} of the {of} exemplar tickets with a {when!r} section ({pct}) "
                "also have {then!r} - against {baseline} of tickets overall. "
                "The two go together on this board."
            ),
            "fix": "Add the {then!r} section.",
        },
        "de": {
            "what": "Hat einen Abschnitt {when!r}, aber kein {then!r}.",
            "why": (
                "{count} von {of} Beispieltickets mit {when!r} ({pct}) haben auch "
                "{then!r} - gegenüber {baseline} aller Tickets. "
                "Die beiden gehören auf diesem Board zusammen."
            ),
            "fix": "Den Abschnitt {then!r} ergänzen.",
        },
    },
    "short_description": {
        "en": {
            "what": "The description is {chars} characters.",
            "why": (
                "The shortest quarter of tickets that shipped here start at {p25} "
                "characters; the median is {median}."
            ),
            "fix": "Say what should happen, and how anyone will know it did.",
        },
        "de": {
            "what": "Die Beschreibung hat {chars} Zeichen.",
            "why": (
                "Das kürzeste Viertel der hier umgesetzten Tickets beginnt bei {p25} "
                "Zeichen, der Median liegt bei {median}."
            ),
            "fix": "Beschreiben, was passieren soll - und woran man merkt, dass es passiert ist.",
        },
    },
    "no_labels": {
        "en": {
            "what": "The ticket has no labels.",
            "why": "{pct} of the exemplars are labelled.",
            "fix": "Add the usual ones: {suggestions}",
        },
        "de": {
            "what": "Das Ticket hat keine Labels.",
            "why": "{pct} der Beispieltickets sind gelabelt.",
            "fix": "Die üblichen setzen: {suggestions}",
        },
    },
    "missing_label_group": {
        "en": {
            "what": "No {group}:: label.",
            "why": "Roughly {pct} of the exemplars carry one.",
            "fix": "Set the {group}:: label so the board picks this up.",
        },
        "de": {
            "what": "Kein {group}::-Label.",
            "why": "Etwa {pct} der Beispieltickets haben eines.",
            "fix": "Das {group}::-Label setzen, damit das Board das Ticket aufnimmt.",
        },
    },
    "no_checklist": {
        "en": {
            "what": "No checklist.",
            "why": "{pct} of the exemplars have one.",
            "fix": "Break the work into checkboxes the way the other tickets do.",
        },
        "de": {
            "what": "Keine Checkliste.",
            "why": "{pct} der Beispieltickets haben eine.",
            "fix": "Die Arbeit in Checkboxen aufteilen, wie in den anderen Tickets.",
        },
    },
    "no_screenshot": {
        "en": {
            "what": "No screenshot or attachment.",
            "why": "{pct} of the exemplars have one.",
            "fix": "Attach the screen this is about.",
        },
        "de": {
            "what": "Kein Screenshot und kein Anhang.",
            "why": "{pct} der Beispieltickets haben einen.",
            "fix": "Den betroffenen Bildschirm anhängen.",
        },
    },
    "no_cross_reference": {
        "en": {
            "what": "Nothing linked to another ticket or change.",
            "why": "{pct} of the exemplars have one.",
            "fix": "Link the ticket this follows from, if there is one.",
        },
        "de": {
            "what": "Keine Verknüpfung zu einem anderen Ticket oder Change.",
            "why": "{pct} der Beispieltickets haben eine.",
            "fix": "Das vorangehende Ticket verlinken, falls es eines gibt.",
        },
    },
    "title_prefix": {
        "en": {
            "what": "The title does not start with a marker like {prefix!r}.",
            "why": "{pct} of the exemplar titles do.",
            "fix": "Prefix the title the way the others are prefixed ({prefix}).",
        },
        "de": {
            "what": "Der Titel beginnt nicht mit einem Marker wie {prefix!r}.",
            "why": "{pct} der Beispieltitel tun das.",
            "fix": "Den Titel wie die anderen kennzeichnen ({prefix}).",
        },
    },
    "language_mismatch": {
        "en": {
            "what": "Written in {found}.",
            "why": "The exemplar tickets in this project are in {expected}.",
            "fix": "Match the language the rest of the board is in.",
        },
        "de": {
            "what": "Auf {found} geschrieben.",
            "why": "Die Beispieltickets in diesem Projekt sind auf {expected}.",
            "fix": "Die Sprache des übrigen Boards verwenden.",
        },
    },
}


def render(code: str, part: str, params: dict[str, Any], language: str | None = None) -> str:
    """One half of one finding, in one language.

    An unknown code returns the code itself rather than raising. A finding is
    something a person is reading in a report; a KeyError deep in a formatter
    would take the whole report with it over a message nobody had written yet.
    """
    lang = normalise(language)
    entry = _MESSAGES.get(code)
    if not entry:
        return code
    template = entry.get(lang, entry.get(DEFAULT, {})).get(part) or entry.get(DEFAULT, {}).get(
        part, ""
    )
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        # A template asking for a parameter nobody passed is an authoring
        # mistake, and it should show up as an obviously wrong sentence rather
        # than as a crash in the middle of a review.
        return template


def codes() -> tuple[str, ...]:
    return tuple(sorted(_MESSAGES))
