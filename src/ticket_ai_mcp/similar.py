# SPDX-License-Identifier: MIT

"""Finding the tickets that already covered this ground.

No model, no embeddings, no service. Word overlap weighted by how rare each
word is in the project's own tickets - which for this job beats a semantic
model in the ways that matter here: it is instant, it costs nothing, it gives
the same answer twice, and it can name the words that caused the match so a
reader can disagree with it.

The weighting is the part that does the work. On a travel platform every
second ticket says "Seite" and "Kunde", so a plain overlap count ranks by how
generic a ticket is. Weighting each word by the inverse of how many tickets
contain it means the match is driven by "Etikettendruck" and "Wareneingang",
which is what someone actually meant when they typed the subject.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .schemas import Ticket

# Words, in any script, plus the identifier shapes that show up in a ticket
# about code: snake_case, dotted.paths, CamelCase are split by the caller.
_TOKEN = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

# Function words carry no signal and would otherwise dominate a short subject.
# German and English together, because a German board still says "Backend".
_STOP = {
    # de
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einen",
    "einem",
    "und",
    "oder",
    "aber",
    "ist",
    "sind",
    "war",
    "wird",
    "werden",
    "wurde",
    "nicht",
    "kein",
    "keine",
    "auf",
    "für",
    "mit",
    "von",
    "vom",
    "bei",
    "beim",
    "nach",
    "über",
    "unter",
    "aus",
    "durch",
    "wenn",
    "dann",
    "soll",
    "sollte",
    "kann",
    "können",
    "muss",
    "müssen",
    "man",
    "sich",
    "wie",
    "was",
    "wer",
    "hier",
    "dort",
    "auch",
    "noch",
    "nur",
    "schon",
    "sehr",
    "mehr",
    "alle",
    "wir",
    "ich",
    "sie",
    "dass",
    "als",
    "hat",
    "haben",
    # en
    "the",
    "and",
    "or",
    "but",
    "is",
    "are",
    "were",
    "be",
    "been",
    "being",
    "not",
    "no",
    "for",
    "with",
    "from",
    "by",
    "at",
    "in",
    "on",
    "of",
    "to",
    "when",
    "then",
    "should",
    "can",
    "could",
    "must",
    "may",
    "this",
    "that",
    "these",
    "those",
    "there",
    "here",
    "also",
    "only",
    "just",
    "more",
    "all",
    "we",
    "it",
    "its",
    "as",
    "has",
    "have",
    "had",
    "will",
    "would",
    "does",
}


def tokens(text: str) -> list[str]:
    """Lowercase content words, with identifiers split into their parts.

    `get_destination_list` and `getDestinationList` both become
    `get destination list`, so a subject written in prose can match a ticket
    that quotes the function name, and the other way round.
    """
    split = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", text or "")
    split = re.sub(r"[_\-./]+", " ", split)
    return [t for t in (w.lower() for w in _TOKEN.findall(split)) if t not in _STOP]


@dataclass(frozen=True, slots=True)
class Match:
    """One ticket that looks related, and the words that made it look that way.

    `shared` exists so nobody has to trust the number. A match on
    "wareneingang" is obviously right; a match on "seite" obviously is
    not, and showing which one happened costs nothing.
    """

    ticket: Ticket
    score: float
    shared: tuple[str, ...]


def _weights(documents: list[list[str]]) -> dict[str, float]:
    """Inverse document frequency over the tickets being searched.

    Smoothed, so a word appearing in every ticket gets a weight near zero
    rather than exactly zero - a subject made entirely of common words should
    still return its best guess rather than nothing at all.
    """
    seen: Counter[str] = Counter()
    for doc in documents:
        seen.update(set(doc))
    n = max(len(documents), 1)
    return {word: math.log(1 + n / (1 + count)) for word, count in seen.items()}


# A word in a title is a claim about what a ticket is; the same word in the
# eleventh paragraph is usually background. Worth something, but not double -
# at 2.0 a common word in a title outscored a rare word in a description, and
# on a German bug board that meant three tickets matching only on "Fehler"
# ranked above the one ticket that actually said "Wareneingang".
TITLE_BOOST = 1.4

# Matching one word out of three is weak evidence however rare that word is,
# and matching all three is strong evidence even if none of them is unusual.
# Agreement across terms is a signal of its own, so it is scored as one:
# a full match keeps its score, a one-in-three match keeps two thirds of it.
MIN_COVERAGE_FACTOR = 0.5

# Results below this fraction of the best one are noise sitting under a
# heading that says "related". Five results of which four are wrong is worse
# than one result, because it teaches people to skim the section.
RELATIVE_FLOOR = 0.45


def rank(subject: str, tickets: list[Ticket], *, limit: int = 5) -> list[Match]:
    """Rank tickets by weighted word overlap with the subject."""
    wanted = set(tokens(subject))
    if not wanted or not tickets:
        return []

    documents = [tokens(f"{t.title} {t.description}") for t in tickets]
    weight = _weights(documents)
    subject_weight = sum(weight.get(word, 0.0) for word in wanted) or 1.0

    matches: list[Match] = []
    for ticket, doc in zip(tickets, documents, strict=True):
        body = set(doc)
        title = set(tokens(ticket.title))
        overlap = wanted & body
        if not overlap:
            continue

        score = sum(
            weight.get(word, 0.0) * (TITLE_BOOST if word in title else 1.0) for word in overlap
        )
        # Normalised by the subject, not by the ticket: a long ticket that
        # happens to contain every word is a better match, not a worse one,
        # and dividing by its length would punish it for being thorough.
        score /= subject_weight
        coverage = len(overlap) / len(wanted)
        score *= MIN_COVERAGE_FACTOR + (1 - MIN_COVERAGE_FACTOR) * coverage

        matches.append(
            Match(
                ticket=ticket,
                score=round(score, 3),
                shared=tuple(sorted(overlap, key=lambda w: -weight.get(w, 0.0))[:6]),
            )
        )

    matches.sort(key=lambda m: (-m.score, m.ticket.key))
    if matches:
        floor = matches[0].score * RELATIVE_FLOOR
        matches = [m for m in matches if m.score >= floor]
    return matches[:limit]
