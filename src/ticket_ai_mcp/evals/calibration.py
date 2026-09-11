# SPDX-License-Identifier: MIT

"""How far the judge can be trusted, measured against a person.

Raw agreement is the number everyone quotes and it is close to useless on its
own. If nine drafts in ten are on topic, a judge that answers "on_topic" every
single time agrees ninety percent of the time and has learned nothing. Cohen's
kappa subtracts the agreement you would get from two people guessing with those
same proportions, so the constant judge scores zero, which is the honest answer.

The labels come from a person, and there is no way around that. This module
computes; it never fills a label in. A calibration whose human half was written
by a model measures one model against another and says nothing about whether
either matches what someone reading the ticket would think.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .dataset import jsonl_lines


@dataclass(frozen=True, slots=True)
class Label:
    """One human verdict on one draft."""

    board: str
    case: str
    repeat: int
    verdict: str

    @property
    def ident(self) -> tuple[str, str, int]:
        return (self.board, self.case, self.repeat)


def load_labels(path: Path) -> list[Label]:
    out: list[Label] = []
    for number, line in enumerate(jsonl_lines(path.read_text(encoding="utf-8")), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: not valid JSON: {exc}") from exc
        out.append(
            Label(
                board=str(data["board"]),
                case=str(data["case"]),
                repeat=int(data.get("repeat", 0)),
                verdict=str(data["verdict"]),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Agreement:
    """The judge against the person, on the drafts both of them saw."""

    pairs: int
    agreed: int
    kappa: float | None
    confusion: tuple[tuple[str, str, int], ...]
    labelled_only: int = 0
    judged_only: int = 0

    @property
    def raw(self) -> float:
        return round(self.agreed / self.pairs, 4) if self.pairs else 0.0

    @property
    def verdict(self) -> str:
        """Landis and Koch's bands, named rather than left to the reader.

        The bands are a convention, not a law, and they are printed as words
        so that nobody reads 0.41 as a pass mark. Anything below "moderate"
        means the judge's numbers should not be quoted at all.
        """
        if self.kappa is None:
            return "undefined"
        for floor, name in ((0.81, "almost perfect"), (0.61, "substantial"), (0.41, "moderate"), (0.21, "fair")):
            if self.kappa >= floor:
                return name
        return "poor" if self.kappa >= 0 else "worse than chance"


def cohens_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Agreement above what two people guessing at these rates would reach.

    None when it cannot be defined: no pairs at all, or a set where both sides
    used exactly one category and the same one. There, expected agreement is
    1.0, the denominator is zero, and every possible answer is perfect
    agreement - a fact about the sample, not about the judge.
    """
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n
    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    expected = sum((left[k] / n) * (right[k] / n) for k in set(left) | set(right))
    if expected >= 1.0:
        return None
    return round((observed - expected) / (1 - expected), 4)


def agreement(labels: list[Label], verdicts) -> Agreement:
    """Compare the two, on the drafts they both cover.

    Drafts only one side saw are counted and reported rather than dropped
    quietly. A calibration over eight of fifty labels is not a calibration, and
    the two counts are how that becomes visible.
    """
    judged = {
        (v.board, v.case, v.repeat): v.verdict for v in verdicts if getattr(v, "ok", True) and v.verdict
    }
    by_label = {label.ident: label.verdict for label in labels}

    shared = sorted(set(by_label) & set(judged))
    pairs = [(by_label[k], judged[k]) for k in shared]

    confusion = Counter(pairs)
    return Agreement(
        pairs=len(pairs),
        agreed=sum(1 for a, b in pairs if a == b),
        kappa=cohens_kappa(pairs),
        confusion=tuple(
            (human, machine, count) for (human, machine), count in sorted(confusion.items())
        ),
        labelled_only=len(set(by_label) - set(judged)),
        judged_only=len(set(judged) - set(by_label)),
    )


def render_agreement(result: Agreement) -> str:
    """The agreement, with the caveats attached rather than in a footnote."""
    if not result.pairs:
        return "no draft was both labelled and judged, so there is nothing to compare"
    lines = [
        f"  pairs        {result.pairs}",
        f"  raw          {result.raw * 100:.0f}% agreed",
        f"  kappa        {result.kappa if result.kappa is not None else 'undefined'} "
        f"({result.verdict})",
    ]
    if result.labelled_only or result.judged_only:
        lines.append(
            f"  unmatched    {result.labelled_only} labelled but not judged, "
            f"{result.judged_only} judged but not labelled"
        )
    lines.append("  human -> judge")
    lines.extend(f"    {human:10s} {machine:10s} {count}" for human, machine, count in result.confusion)
    if result.pairs < 30:
        lines.append(f"  note: {result.pairs} pairs is too few to quote this figure anywhere")
    return "\n".join(lines)
