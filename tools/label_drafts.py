# SPDX-License-Identifier: MIT

"""Show drafts one at a time and write down what a person thought of each.

The labels this collects are the only half of the calibration a model cannot
supply. If they were generated, the agreement figure would compare one model
against another and say nothing about whether either matches what someone
reading the ticket would think.

    python tools/label_drafts.py evals/results/latest.jsonl

Resumable: drafts already in the labels file are skipped, so this can be done
in twenty-minute sittings. Answer with a number, `s` to skip one you are unsure
about, or `q` to stop. **An unsure draft should be skipped, not guessed at** -
a coin flip here lands directly in the kappa.

The draft is shown before the title on purpose. Reading the title first primes
you to find the subject in the body, and the question is whether the body finds
it on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ticket_ai_mcp.evals.calibration import load_labels
from ticket_ai_mcp.evals.dataset import load_suite
from ticket_ai_mcp.evals.judge import VERDICTS
from ticket_ai_mcp.evals.runner import read_runs

PROMPT = "  ".join(f"[{i + 1}] {name}" for i, name in enumerate(VERDICTS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, help="a results file from `ticket-ai eval`")
    parser.add_argument("--labels", type=Path, default=Path("evals/labels.jsonl"))
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many")
    args = parser.parse_args(argv)

    boards = load_suite(args.dataset)
    cases = {(b.slug, c.key): c for b in boards for c in b.cases}
    runs = [r for r in read_runs(args.runs) if r.ok and r.body]

    done = set()
    if args.labels.exists():
        done = {label.ident for label in load_labels(args.labels)}

    todo = [r for r in runs if (r.board, r.case, r.repeat) not in done]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print(f"nothing left to label; {len(done)} already in {args.labels}", file=sys.stderr)
        return 0

    print(f"{len(todo)} to go, {len(done)} already done. q quits, s skips.\n", file=sys.stderr)
    args.labels.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with args.labels.open("a", encoding="utf-8") as handle:
        for number, run in enumerate(todo, start=1):
            case = cases.get((run.board, run.case))
            if case is None:
                continue
            print("=" * 72)
            print(run.body.strip())
            print("-" * 72)
            print(f"title: {case.title}")
            print(f"({number}/{len(todo)}  {run.board} {run.case}  {case.url})")
            print(PROMPT)
            try:
                answer = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            if answer in {"q", "quit"}:
                break
            if answer in {"", "s", "skip"}:
                continue
            if not answer.isdigit() or not 1 <= int(answer) <= len(VERDICTS):
                print("  not one of the options, skipping", file=sys.stderr)
                continue
            handle.write(
                json.dumps(
                    {
                        "board": run.board,
                        "case": run.case,
                        "repeat": run.repeat,
                        "verdict": VERDICTS[int(answer) - 1],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            written += 1

    print(f"\nwrote {written} labels to {args.labels}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
