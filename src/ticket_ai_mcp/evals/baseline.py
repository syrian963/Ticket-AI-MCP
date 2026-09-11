# SPDX-License-Identifier: MIT

"""A committed number to fall short of, and the rules for when that matters.

Nobody remembers last month's figure. Without a file in the repository holding
it, a slow slide across four commits is invisible and only the person who ran
both ends of it could ever notice.

**Not every drop is a regression.** The writer is not deterministic, so two
runs of an unchanged model differ. A gate that fires on any decrease would fire
constantly and be switched off within a week, which is worse than not having
one. The tolerance here is absolute and deliberately blunt: a drop has to
exceed it to count, and the baseline records the spread it was measured with so
that a later reader can see whether the tolerance was reasonable.

The board figures are checked as well as the total. A suite that gains three
points on one board and loses eight on another can come out level overall,
and the level number is the one nobody investigates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .metrics import Report

# A tenth of the scale. Chosen against the only spread there is so far:
# docs/local-models.md shows one board moving between 72% and 83% on the same
# model, so anything tighter would be noise and anything looser would let a
# real regression through.
TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class Baseline:
    """What a good run looked like, and what produced it."""

    model: str
    runs: int
    alignment: float
    stdev: float
    boards: dict[str, float]

    @classmethod
    def of(cls, report: Report) -> Baseline:
        if report.alignment is None:
            raise ValueError("a report with no successful runs cannot be a baseline")
        return cls(
            model=report.model,
            runs=report.runs,
            alignment=report.alignment.mean,
            stdev=report.alignment.stdev,
            boards={b.board: b.alignment.mean for b in report.boards if b.alignment},
        )

    @classmethod
    def load(cls, path: Path) -> Baseline:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            model=str(data["model"]),
            runs=int(data["runs"]),
            alignment=float(data["alignment"]),
            stdev=float(data.get("stdev", 0.0)),
            boards={str(k): float(v) for k, v in (data.get("boards") or {}).items()},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "model": self.model,
                    "runs": self.runs,
                    "alignment": self.alignment,
                    "stdev": self.stdev,
                    "boards": dict(sorted(self.boards.items())),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether the run may pass, and every reason it may not."""

    ok: bool
    complaints: tuple[str, ...]
    notes: tuple[str, ...] = ()


def compare(report: Report, baseline: Baseline, *, tolerance: float = TOLERANCE) -> Verdict:
    """Judge a report against a baseline.

    A model that differs from the baseline's is a note rather than a failure.
    Comparing two models is a legitimate thing to do and the gate has no
    business refusing it; reporting the figure as a regression would be wrong,
    so the mismatch is said out loud and the numbers are left alone.

    A board in the baseline that produced no runs is a complaint. It usually
    means a board directory went missing, and a suite that quietly got smaller
    reports a higher average for a reason that has nothing to do with quality.
    """
    complaints: list[str] = []
    notes: list[str] = []

    if report.model != baseline.model:
        notes.append(f"baseline is {baseline.model}, this run is {report.model}")

    if report.alignment is None:
        return Verdict(False, ("no run succeeded",), tuple(notes))

    drop = baseline.alignment - report.alignment.mean
    if drop > tolerance:
        complaints.append(
            f"alignment {report.alignment.mean:.3f} is {drop:.3f} below the "
            f"baseline {baseline.alignment:.3f} (tolerance {tolerance:.3f})"
        )

    seen = {b.board: b for b in report.boards}
    for slug, was in sorted(baseline.boards.items()):
        board = seen.get(slug)
        if board is None or board.alignment is None:
            complaints.append(f"board {slug} is in the baseline but produced no runs")
            continue
        board_drop = was - board.alignment.mean
        if board_drop > tolerance:
            complaints.append(
                f"board {slug} {board.alignment.mean:.3f} is {board_drop:.3f} "
                f"below its baseline {was:.3f}"
            )

    for slug in sorted(seen):
        if slug not in baseline.boards and seen[slug].alignment is not None:
            notes.append(f"board {slug} is new and not in the baseline")

    if report.failed:
        notes.append(f"{report.failed} of {report.runs} runs failed outright")

    return Verdict(not complaints, tuple(complaints), tuple(notes))
