# SPDX-License-Identifier: MIT

"""One question a model is allowed to answer about a draft, and nothing more.

Counting gets most of the way. It cannot answer whether the prose is about the
title, and that is the failure worth catching: a draft can carry every section
the board uses, hit the length, take the right labels, and describe something
else entirely. `review_draft` would give it full marks.

So the judge is asked exactly that and nothing else. Not "is this a good
ticket" - that is the question the whole tool exists to replace with counts,
and handing it back to a model would undo the point. Three answers rather than
a score out of ten, because a model asked for a number spreads it over 6, 7 and
8 in a way that survives no calibration.

**An uncalibrated judge is an opinion with decimal places.** Nothing here is
meant to be reported on its own. `calibration.py` is the other half, and the
agreement figure belongs next to any number this produces.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..writers.base import Writer, WriterError

VERDICTS = ("on_topic", "partly", "off_topic")

SYSTEM = """You check one thing about a draft ticket and answer in JSON.

The question: does the body describe the problem named in the title?

- "on_topic": the body is about the title's subject.
- "partly": the body touches the subject but spends most of itself elsewhere,
  or describes something adjacent to it.
- "off_topic": the body is about something else, or says nothing specific
  enough to tell.

You are not judging whether the ticket is well written, long enough, correctly
structured or useful. Those are measured separately by counting, and an opinion
about them here would be noise. Ignore missing sections, formatting, tone and
length entirely.

Answer with JSON only: {"verdict": "on_topic"|"partly"|"off_topic", "why": "one short sentence"}
"""

_JSON = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the judge said about one draft."""

    board: str
    case: str
    repeat: int
    verdict: str
    why: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_json(self) -> str:
        return json.dumps(
            {
                "board": self.board,
                "case": self.case,
                "repeat": self.repeat,
                "verdict": self.verdict,
                "why": self.why,
                "error": self.error,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def parse(text: str) -> tuple[str, str]:
    """Pull a verdict out of whatever the model sent back.

    Models wrap JSON in prose and in code fences however they feel, so the
    first balanced-looking object in the text is taken rather than the whole
    string being parsed. A verdict outside the three allowed words is not
    coerced to the nearest one: "mostly on topic" becomes an error, because
    silently rounding it would put a guess into the agreement figure that the
    calibration is supposed to measure.
    """
    match = _JSON.search(text or "")
    if not match:
        raise ValueError(f"no JSON object in the answer: {(text or '')[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"the answer is not valid JSON: {exc}") from exc
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise ValueError(f"{verdict!r} is not one of {', '.join(VERDICTS)}")
    return verdict, str(data.get("why", "")).strip()


def judge_draft(title: str, body: str, writer: Writer) -> tuple[str, str]:
    """Ask once. No retry, no second opinion."""
    prompt = f"Title:\n{title}\n\nDraft body:\n{body}\n"
    return parse(writer.write(SYSTEM, prompt))


def judge_runs(runs, boards, writer: Writer):
    """Judge every successful run, yielding a verdict each.

    Failed runs are skipped rather than judged as off_topic. There is no draft
    to read, and a verdict invented for a missing one would flow straight into
    the agreement figure.
    """
    cases = {(b.slug, c.key): c for b in boards for c in b.cases}
    for run in runs:
        if not run.ok or not run.body:
            continue
        case = cases.get((run.board, run.case))
        if case is None:
            continue
        try:
            verdict, why = judge_draft(case.title, run.body, writer)
        except (WriterError, ValueError) as exc:
            yield Verdict(run.board, run.case, run.repeat, verdict="", error=str(exc))
            continue
        yield Verdict(run.board, run.case, run.repeat, verdict=verdict, why=why)
