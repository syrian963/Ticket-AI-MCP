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
    # The same finding, when the two outcome groups were big enough to compare.
    # One rate invites a shrug; two rates are an argument, so this variant is
    # used whenever `contrast.py` had the evidence to support it.
    "missing_section_contrast": {
        "en": {
            "what": "No {heading!r} section.",
            "why": (
                "{shipped} of the tickets that shipped have one, against {stalled} of "
                "the ones that stalled - closed with nothing merged, or reopened, or "
                "left waiting on questions."
            ),
            "fix": "Add the {heading!r} heading and fill it in.",
        },
        "de": {
            "what": "Kein Abschnitt {heading!r}.",
            "why": (
                "{shipped} der umgesetzten Tickets haben einen, gegenüber {stalled} "
                "der liegengebliebenen - ohne Merge geschlossen, wieder geöffnet oder "
                "an Rückfragen hängengeblieben."
            ),
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
    if code == "language_mismatch":
        # `found` and `expected` arrive as codes. A reader wants a language.
        params = {
            **params,
            **{
                key: _part(f"lang.{params[key]}", lang)
                for key in ("found", "expected")
                if key in params
            },
        }
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


# The caveats that travel with a profile, in both languages.
#
# These are separate from findings because they are about the *measurement*
# rather than about one ticket: "built from four tickets, so treat every rate
# as a hint". They were English string literals built in `profile.py`, which
# put the most important sentences on a German page in English - the page whose
# whole reason for existing is that a German team reads it.
#
# Same two rules as above: every message names its measurement, and both
# languages carry the same numbers.
_NOTES: dict[str, dict[str, str]] = {
    "thin_sample": {
        "en": (
            "Built from {n} tickets. Every rate below moves by more than {points} "
            "points if one ticket changes, so treat them as a hint rather than a rule."
        ),
        "de": (
            "Aus {n} Tickets gebaut. Jede Rate unten verschiebt sich um mehr als "
            "{points} Punkte, wenn ein Ticket anders ist - also eher ein Hinweis "
            "als eine Regel."
        ),
    },
    "no_template": {
        "en": (
            "No heading appears in two or more of these tickets: this team does not "
            "seem to use a template, so nothing here can check for one."
        ),
        "de": (
            "Keine Überschrift kommt in zwei oder mehr dieser Tickets vor: dieses "
            "Team benutzt offenbar keine Vorlage, also kann hier auch nichts auf "
            "eine geprüft werden."
        ),
    },
    "no_exemplars": {
        "en": "No ticket in the sample could be used as an exemplar.",
        "de": "Kein Ticket aus der Stichprobe war als Beispiel brauchbar.",
    },
    "tracker_withheld": {
        "en": (
            "{host} did not serve {missing} - {why}. Rankings and the "
            "shipped-against-stalled comparison are weaker without them."
        ),
        "de": (
            "{host} hat {missing} nicht geliefert - {why}. Die Bewertung und der "
            "Vergleich zwischen umgesetzten und liegengebliebenen Tickets sind "
            "ohne sie schwächer."
        ),
    },
}

# The pieces the tracker_withheld sentence is assembled from. They are listed
# here rather than in the adapter for the same reason the sentence is: an
# adapter that returns prose can only return it in one language.
_PARTS: dict[str, dict[str, str]] = {
    "notes": {"en": "comments", "de": "Kommentare"},
    "resource_state_events": {"en": "reopen history", "de": "die Wiedereröffnungen"},
    "related_merge_requests": {"en": "linked merge requests", "de": "verknüpfte Merge Requests"},
    "anonymous": {"en": "reading without a token", "de": "Lesen ohne Token"},
    # Language names, because the finding used to interpolate the code: both
    # languages read "Written in de." and "Auf de geschrieben.", which is not
    # a sentence in either of them.
    "lang.en": {"en": "English", "de": "Englisch"},
    "lang.de": {"en": "German", "de": "Deutsch"},
    "scope": {
        "en": "the token's scope, or this GitLab version",
        "de": "der Umfang des Tokens oder diese GitLab-Version",
    },
}


def part(name: str, language: str | None = None) -> str:
    """One noun out of a note, in the reader's language."""
    entry = _PARTS.get(name)
    if not entry:
        return name
    return entry.get(normalise(language)) or entry.get(DEFAULT, name)


# `render` takes a parameter called `part`, which shadows the function above.
# The alias is the smallest fix that keeps both names readable where they are.
_part = part


def render_note(code: str, params: dict[str, Any], language: str | None = None) -> str:
    """One profile caveat, in one language.

    Same forgiving behaviour as `render`: an unknown code or a missing
    parameter produces an obviously wrong sentence rather than taking the
    report down with it.
    """
    if code == "_literal":
        # A sentence that was already finished when it got here: a caveat read
        # back from a profile cached before notes became codes. It cannot be
        # translated - nobody kept the parts - and showing it is better than
        # dropping it.
        return str(params.get("text", ""))
    entry = _NOTES.get(code)
    if not entry:
        return code
    lang = normalise(language)
    if code == "tracker_withheld":
        # The adapter hands over endpoint names and a reason to blame; the
        # nouns and the joining comma are language, so they are made here.
        params = {
            **params,
            "missing": ", ".join(part(name, lang) for name in params.get("parts", ())),
            "why": part(params.get("why", ""), lang),
        }
    template = entry.get(lang) or entry.get(DEFAULT, "")
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def note_codes() -> tuple[str, ...]:
    return tuple(sorted(_NOTES))


# What a ticket got right, and the caveats a review carries. Same treatment as
# findings and for the same reason: these were English prose built in
# `review.py`, so a German page showed German findings, a German heading over
# them, and then "has the Steps to reproduce section" underneath.
#
# The section names inside them stay in whatever language the team writes -
# they are the team's own headings, not words to translate.
_PASSED: dict[str, dict[str, str]] = {
    "has_section": {
        "en": "has the {heading} section",
        "de": "hat den Abschnitt {heading}",
    },
    "has_pair": {
        "en": "has {then} to go with {when}",
        "de": "hat {then} passend zu {when}",
    },
    "length_normal": {
        "en": "description length ({chars} characters) is in the normal range",
        "de": "Beschreibungslänge ({chars} Zeichen) liegt im üblichen Bereich",
    },
    "labelled": {
        "en": "labelled ({labels})",
        "de": "mit Labels versehen ({labels})",
    },
    "has_list": {"en": "has a list", "de": "hat eine Liste"},
    "has_checklist": {"en": "has a checklist", "de": "hat eine Checkliste"},
    "has_code": {"en": "has a code block", "de": "hat einen Code-Block"},
    "has_image": {"en": "has a screenshot", "de": "hat einen Screenshot"},
    "has_cross_ref": {
        "en": "links another ticket",
        "de": "verweist auf ein anderes Ticket",
    },
    "no_profile": {
        "en": (
            "There is no profile to compare against - no exemplar tickets were "
            "found. Nothing below this line would mean anything."
        ),
        "de": (
            "Es gibt kein Profil zum Vergleichen - es wurden keine Beispieltickets "
            "gefunden. Nichts unterhalb dieser Zeile hat eine Bedeutung."
        ),
    },
    "nothing_applied": {
        "en": (
            "No check applied to this ticket: the exemplar tickets have no "
            "convention consistent enough to hold anyone to. That is a finding "
            "about the board, not a pass for this ticket."
        ),
        "de": (
            "Keine Prüfung war auf dieses Ticket anwendbar: die Beispieltickets "
            "haben keine Konvention, die konsequent genug wäre, um jemanden daran "
            "zu messen. Das ist ein Befund über das Board, nicht ein Bestehen "
            "für dieses Ticket."
        ),
    },
}


def render_passed(code: str, params: dict[str, Any], language: str | None = None) -> str:
    """One "already right" line or caveat, in one language."""
    entry = _PASSED.get(code)
    if not entry:
        return code
    template = entry.get(normalise(language)) or entry.get(DEFAULT, "")
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template
