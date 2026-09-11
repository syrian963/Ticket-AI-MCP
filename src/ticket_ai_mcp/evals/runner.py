# SPDX-License-Identifier: MIT

"""Put a case through `compose` and write down what came back.

Nothing here decides whether a draft is good. The runner produces records; the
scoring is a separate pass over those records, so that a metric can be added or
corrected later without paying for the model runs again. A harness that mixes
the two forces a rerun every time someone changes their mind about a threshold,
and at thirty to seventy seconds a case that is the difference between trying
an idea and not bothering.

Every record carries the repeat index. A language model is not deterministic
even at temperature zero, and a single run per case produces a number with no
spread, which reads as precision it does not have.

A writer that fails takes its own case down and nothing else. One unreachable
model mid-suite used to mean starting over; the failure is a record like any
other now, with the message kept, and the rest of the suite continues.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from ..compose import compose
from ..writers.base import Writer, WriterError
from .dataset import Board, Case, jsonl_lines


@dataclass(frozen=True, slots=True)
class CaseRun:
    """One case, one model, one attempt at it.

    `alignment` is `review_draft`'s own figure and is `None` when the run
    failed, deliberately rather than 0.0: a model that could not be reached
    scored nothing, and averaging a zero into the result would report an
    outage as a quality problem.
    """

    board: str
    case: str
    model: str
    repeat: int
    seconds: float
    attempts: int = 0
    alignment: float | None = None
    checks_run: int = 0
    findings: tuple[str, ...] = ()
    body: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(slots=True)
class Progress:
    """What the caller is told while a suite runs.

    A suite is minutes long on a laptop. Printing nothing for that stretch is
    indistinguishable from a hang.
    """

    board: str
    case: str
    repeat: int
    done: int
    total: int
    run: CaseRun | None = None


def run_case(
    board: Board,
    case: Case,
    writer: Writer,
    *,
    repeat: int = 0,
) -> CaseRun:
    """Compose one draft for `case` and measure how long it took."""
    model = getattr(writer, "model", writer.name)
    started = time.perf_counter()
    try:
        composed = compose(case.title, board.profile, writer, labels=case.labels)
    except WriterError as exc:
        return CaseRun(
            board=board.slug,
            case=case.key,
            model=model,
            repeat=repeat,
            seconds=round(time.perf_counter() - started, 3),
            error=str(exc),
        )
    return CaseRun(
        board=board.slug,
        case=case.key,
        # `compose` reports which model actually answered. Prefer it over the
        # name asked for: a backend that silently substitutes a model would
        # otherwise be recorded under the name that was requested.
        model=composed.model or model,
        repeat=repeat,
        seconds=round(time.perf_counter() - started, 3),
        attempts=composed.attempts,
        alignment=composed.review.alignment,
        checks_run=composed.review.checks_run,
        findings=tuple(finding.code for finding in composed.review.findings),
        body=composed.body,
    )


def run_suite(
    boards: Iterable[Board],
    writer: Writer,
    *,
    repeats: int = 1,
    on_progress: Callable[[Progress], None] | None = None,
) -> Iterator[CaseRun]:
    """Every case of every board, `repeats` times, yielded as they finish.

    Repeats are the outer loop rather than the inner one. Running a case three
    times in a row and only then moving on gives a model's cache the best
    possible view of it, which is not the view a real user gets.
    """
    ordered = list(boards)
    total = sum(len(board.cases) for board in ordered) * max(repeats, 1)
    done = 0
    for repeat in range(max(repeats, 1)):
        for board in ordered:
            for case in board.cases:
                if on_progress:
                    on_progress(Progress(board.slug, case.key, repeat, done, total))
                run = run_case(board, case, writer, repeat=repeat)
                done += 1
                if on_progress:
                    on_progress(Progress(board.slug, case.key, repeat, done, total, run))
                yield run


def write_runs(path: Path, runs: Iterable[CaseRun]) -> int:
    """Append records as they arrive, and return how many were written.

    Line by line rather than one document at the end, so that a suite
    interrupted after forty minutes still leaves forty minutes of results.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for run in runs:
            handle.write(run.to_json() + "\n")
            handle.flush()
            written += 1
    return written


def read_runs(path: Path) -> list[CaseRun]:
    """Read a results file back, skipping nothing quietly."""
    out: list[CaseRun] = []
    for number, line in enumerate(jsonl_lines(path.read_text(encoding="utf-8")), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: not valid JSON: {exc}") from exc
        payload["findings"] = tuple(payload.get("findings") or ())
        out.append(CaseRun(**payload))
    return out
