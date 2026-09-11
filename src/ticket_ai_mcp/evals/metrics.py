# SPDX-License-Identifier: MIT

"""What the records mean, computed after the fact and without a model.

Everything here is arithmetic over `CaseRun` records plus the dataset they came
from. No model is called, so a metric can be added, corrected or argued with
for free, which is the reason the runner records instead of scoring.

Two of these are not in `review_draft`, and that is deliberate rather than an
oversight there. `review_draft` marks a ticket against a board; it asks whether
the sections the team uses are present. **It never asks what else the draft
invented**, because a human writing a ticket does not usually add a heading the
board has never seen, and a model does it constantly. The other is spread:
a single figure per case reads as precision that a non-deterministic writer
cannot support.

A failed run is excluded from every quality figure and counted separately.
Folding an unreachable model into the mean would report an outage as a drop in
quality, which is the one reading that would send someone to the wrong place.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..textstats import headings, language, normalise_heading
from .dataset import Board
from .runner import CaseRun


@dataclass(frozen=True, slots=True)
class Spread:
    """A number, and how much it moves.

    `n` is carried because a mean over three runs and a mean over sixty read
    identically in a table and mean very different things.
    """

    n: int
    mean: float
    median: float
    stdev: float
    low: float
    high: float

    @classmethod
    def of(cls, values: Iterable[float]) -> Spread | None:
        data = sorted(float(v) for v in values)
        if not data:
            return None
        return cls(
            n=len(data),
            mean=round(statistics.fmean(data), 4),
            median=round(statistics.median(data), 4),
            # One observation has no spread. `stdev` raises on a single value
            # and 0.0 would claim the writer is deterministic.
            stdev=round(statistics.stdev(data), 4) if len(data) > 1 else 0.0,
            low=data[0],
            high=data[-1],
        )


def invented_sections(body: str, board: Board) -> tuple[str, ...]:
    """Headings in the draft that this board has never used.

    Measured against every section the profile observed, not against the
    skeleton. The skeleton is the subset common enough to ask for; a heading
    the board uses in a third of its tickets is part of the house style even
    though the prompt did not name it, and counting it as invented would
    punish a draft for being right.

    A board that writes pure prose has no observed sections, so every heading
    in a draft for it is invented - which is the correct reading. The prompt
    told the model in plain words to write no headings at all.
    """
    known = {normalise_heading(section.heading) for section in board.profile.sections}
    seen: list[str] = []
    for heading in headings(body):
        if normalise_heading(heading) not in known and heading not in seen:
            seen.append(heading)
    return tuple(seen)


def wrong_language(body: str, board: Board) -> bool:
    """Whether the draft is in a language this board does not write in.

    `textstats.language` returns None when the evidence is thin, and that is
    passed through as "not wrong" rather than as a failure. A short draft is
    its own finding; guessing at its language and reporting the guess as a
    defect would stack one complaint on another.
    """
    guessed = language(body)
    return bool(guessed and board.language and guessed != board.language)


@dataclass(frozen=True, slots=True)
class BoardReport:
    """One board, summarised over however many runs it got."""

    board: str
    cases: int
    runs: int
    failed: int
    alignment: Spread | None
    per_case_stdev: Spread | None
    seconds: Spread | None
    revised: float
    invented_rate: float
    invented_headings: tuple[tuple[str, int], ...]
    wrong_language_rate: float
    findings: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Report:
    """The whole suite, and the boards inside it."""

    model: str
    boards: tuple[BoardReport, ...]
    runs: int
    failed: int
    alignment: Spread | None
    findings: tuple[tuple[str, int], ...] = field(default=())

    @property
    def failure_rate(self) -> float:
        return round(self.failed / self.runs, 4) if self.runs else 0.0


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def score_board(board: Board, runs: list[CaseRun]) -> BoardReport:
    """Summarise one board's runs. `runs` may be empty."""
    good = [r for r in runs if r.ok and r.alignment is not None]
    failed = sum(1 for r in runs if not r.ok)
    cases = {c.key: c for c in board.cases}

    by_case: dict[str, list[float]] = {}
    invented_hits = 0
    invented_counter: Counter[str] = Counter()
    language_hits = 0
    findings: Counter[str] = Counter()

    for run in good:
        by_case.setdefault(run.case, []).append(float(run.alignment or 0.0))
        found = invented_sections(run.body, board)
        if found:
            invented_hits += 1
            invented_counter.update(found)
        if wrong_language(run.body, board):
            language_hits += 1
        findings.update(run.findings)

    # Only cases that were run more than once say anything about spread.
    spreads = [statistics.stdev(v) for v in by_case.values() if len(v) > 1]

    return BoardReport(
        board=board.slug,
        cases=len(cases),
        runs=len(runs),
        failed=failed,
        alignment=Spread.of(r.alignment or 0.0 for r in good),
        per_case_stdev=Spread.of(spreads),
        seconds=Spread.of(r.seconds for r in good),
        revised=_rate(sum(1 for r in good if r.attempts > 1), len(good)),
        invented_rate=_rate(invented_hits, len(good)),
        invented_headings=tuple(invented_counter.most_common(10)),
        wrong_language_rate=_rate(language_hits, len(good)),
        findings=tuple(findings.most_common()),
    )


def score(boards: Iterable[Board], runs: Iterable[CaseRun]) -> Report:
    """Summarise a whole suite, one model at a time.

    Runs for a board that is not in `boards` are dropped rather than counted
    under an unknown slug: the metrics need the profile to say what counts as
    an invented heading, and without it the figure would be quietly wrong
    rather than missing.
    """
    by_slug = {b.slug: b for b in boards}
    collected = list(runs)
    grouped: dict[str, list[CaseRun]] = {slug: [] for slug in by_slug}
    for run in collected:
        if run.board in grouped:
            grouped[run.board].append(run)

    reports = tuple(score_board(by_slug[slug], grouped[slug]) for slug in sorted(by_slug))
    used = [r for slug in grouped for r in grouped[slug]]
    good = [r for r in used if r.ok and r.alignment is not None]
    findings: Counter[str] = Counter()
    for run in good:
        findings.update(run.findings)

    models = {r.model for r in used if r.model}
    return Report(
        # A report over two models mixed together is a number nobody can act
        # on, so say so in the field rather than picking one of them.
        model=next(iter(models)) if len(models) == 1 else ", ".join(sorted(models)),
        boards=reports,
        runs=len(used),
        failed=sum(1 for r in used if not r.ok),
        alignment=Spread.of(r.alignment or 0.0 for r in good),
        findings=tuple(findings.most_common()),
    )
