# SPDX-License-Identifier: MIT

"""Measuring the shape of a ticket description, without reading it.

Everything here is counting. Nothing here judges - judging needs the corpus to
compare against, and that lives in `profile.py` and `review.py`.

Two decisions shape this file.

**Headings are found four ways, not one.** Teams write the same template in
whatever their editor made easy: `## Steps`, `**Steps**`, `Steps:`, or the
`h2.` that Jira's old wiki markup left behind. A profile built only from `##`
headings would tell a team using bold labels that they have no template at all,
which is both wrong and insulting.

**Heading text is normalised, never translated.** `Akzeptanzkriterien`,
`akzeptanz-kriterien` and `AKZEPTANZKRITERIEN` are one heading;
`Akzeptanzkriterien` and `Acceptance criteria` are two. Deciding those are the
same thing is a judgement about a team's language, and this tool reports what
it counted instead of guessing.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# A fenced code block, so its contents can be removed before counting anything
# else. A stack trace pasted into a ticket is not six bullet points.
_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
# A whole line that is only bold text is a heading in every team's hands, even
# though no spec says so.
_BOLD_HEADING = re.compile(r"^\s{0,3}(?:\*\*|__)(.{2,80}?)(?:\*\*|__)\s*:?\s*$", re.MULTILINE)
# `Steps to reproduce:` on its own line.
#
# Capped by word count, not character count. A character cap lets a short
# sentence through - a real project had "Hier ist das Mockup der neuen
# Unterseite:" counted as a section heading in three tickets, which is 43
# characters and obviously prose. Headings are short *phrases*: "Steps to
# reproduce" is three words, "Akzeptanzkriterien" is one.
_COLON_HEADING = re.compile(r"^\s{0,3}((?:[^\s:]+[ \t]+){0,5}[^\s:]+):[ \t]*$", re.MULTILINE)
# Jira wiki markup, still everywhere in older projects.
_JIRA_HEADING = re.compile(r"^\s{0,3}h[1-6]\.\s*(.+?)\s*$", re.MULTILINE)

_LIST_ITEM = re.compile(r"^\s{0,8}(?:[-*+]|\d+[.)])\s+\S", re.MULTILINE)
_CHECKBOX = re.compile(r"^\s{0,8}[-*+]\s+\[[ xX]\]\s+\S", re.MULTILINE)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)|!\S+\.(?:png|jpe?g|gif)!|/uploads/", re.IGNORECASE)
_LINK = re.compile(r"https?://\S+")
_TICKET_REF = re.compile(r"(?<![\w/])(?:#\d+|![\d]+|[A-Z][A-Z0-9]+-\d+)\b")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Shape:
    """What a description is made of.

    `chars` and `words` exclude code blocks on purpose. A 4000-character ticket
    that is 3900 characters of pasted log is a short ticket with an attachment,
    and treating it as long would let one stack trace drag a team's median up.
    """

    chars: int
    words: int
    headings: tuple[str, ...]
    list_items: int
    checkboxes: int
    code_blocks: int
    images: int
    links: int
    ticket_refs: tuple[str, ...]
    question_marks: int

    @property
    def is_empty(self) -> bool:
        return self.chars == 0


def normalise_heading(text: str) -> str:
    """Fold a heading to the key two spellings of it share.

    Accents are stripped, case and punctuation go, and whitespace collapses -
    so `Schritte zur Reproduktion:` and `schritte  zur reproduktion` land on
    the same key. Umlauts fold the German way (`ä` to `a`, not to `ae`),
    which is enough for matching even though it is not how anyone spells.
    """
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.replace("*", " ").replace("_", " ").replace("#", " ")
    folded = re.sub(r"[^\w\s]+", " ", folded, flags=re.UNICODE)
    return re.sub(r"\s+", " ", folded).strip().lower()


def headings(text: str) -> tuple[str, ...]:
    """Every line that acts as a heading, in the order they appear.

    A line can only be one heading: markdown wins over bold, bold over colon.
    Without that precedence `## Steps:` would be counted twice and inflate
    every rate built on top of it.
    """
    body = _FENCE.sub("\n", text)
    found: dict[int, str] = {}
    for pattern, group in (
        (_MD_HEADING, 2),
        (_JIRA_HEADING, 1),
        (_BOLD_HEADING, 1),
        (_COLON_HEADING, 1),
    ):
        for match in pattern.finditer(body):
            line_start = body.rfind("\n", 0, match.start()) + 1
            # The trailing colon is punctuation, not part of the name. It is
            # dropped here rather than in `normalise_heading` because the
            # display form ends up quoted back to people in a report.
            found.setdefault(line_start, match.group(group).strip().rstrip(":").strip())
    return tuple(found[k] for k in sorted(found) if found[k])


def shape(text: str) -> Shape:
    """Measure one description."""
    raw = text or ""
    code_blocks = len(_FENCE.findall(raw))
    body = _FENCE.sub("\n", raw)
    prose = _INLINE_CODE.sub(" ", body)
    return Shape(
        chars=len(prose.strip()),
        words=len(_WORD.findall(prose)),
        headings=headings(raw),
        list_items=len(_LIST_ITEM.findall(body)),
        checkboxes=len(_CHECKBOX.findall(body)),
        code_blocks=code_blocks,
        images=len(_IMAGE.findall(body)),
        links=len(_LINK.findall(body)),
        ticket_refs=tuple(_TICKET_REF.findall(body)),
        question_marks=prose.count("?"),
    )


# Function words, which are the ones that survive any subject matter. A ticket
# about `QuerySet.annotate` is still recognisably German if it says "wenn der
# Nutzer auf den Button klickt".
_STOPWORDS = {
    "de": {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "nicht",
        "eine",
        "einen",
        "einem",
        "wird",
        "werden",
        "wenn",
        "soll",
        "sollte",
        "beim",
        "auf",
        "für",
        "mit",
        "von",
        "dass",
        "kann",
        "aber",
        "auch",
        "noch",
        "man",
        "sich",
        "wie",
        "nach",
        "über",
        "bei",
    },
    "en": {
        "the",
        "and",
        "is",
        "not",
        "should",
        "when",
        "with",
        "from",
        "that",
        "this",
        "for",
        "are",
        "was",
        "were",
        "have",
        "has",
        "but",
        "also",
        "can",
        "will",
        "there",
        "then",
        "than",
        "into",
        "about",
        "after",
        "over",
    },
}


def language(text: str) -> str | None:
    """Guess the language of a ticket from function words alone.

    Two languages, because those are the two this was built for and a wrong
    third guess is worse than none. Returns None when the evidence is thin -
    a three-word title cannot tell you anything, and saying so beats a coin
    flip that later shows up in a report as fact.
    """
    words = [w.lower() for w in _WORD.findall(text or "")]
    if len(words) < 12:
        return None
    scores = {lang: sum(w in bag for w in words) for lang, bag in _STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    runner = max((k for k in scores if k != best), key=lambda k: scores[k])
    if scores[best] < 3 or scores[best] < scores[runner] * 1.5:
        return None
    return best


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile.

    Deliberately not interpolating. These numbers are quoted back to people as
    "the shortest quarter of your tickets start at 340 characters", and a real
    ticket's length is a better thing to point at than an average of two.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return float(ordered[rank])
