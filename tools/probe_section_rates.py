# SPDX-License-Identifier: MIT

"""What decides whether a board has a skeleton? Ask several boards, cheaply.

Written to check a claim on `docs/evaluating-compose.md`, and it has now
refuted two of them, which is why it is a file rather than something somebody
ran once.

The first claim was that the 0.6 threshold is never a close call.
`gitlab-org/gitlab-runner` sits at 68% and 40% on a 60-ticket sample.

The second was worse: that the top board-wide rate is what decides the shape at
all. **`Profile.skeleton` has two paths**, and on most boards here the second
one does the work. A section earns its place by being common board-wide, *or*
by belonging to a pair whose members imply each other. kern-ux has no section
over 39% and still has a skeleton, because `Beschreibung` and
`Akzeptanzkriterien` appear together on 8 of the 9 tickets that have either.
So this prints both.

A smaller sample than `build_eval_dataset.py` uses, deliberately. The output is
one number per board, nothing is written to disk, and no profile survives the
run - so it is worth pointing at a dozen boards to see a distribution rather
than at one board to keep it.

**The answer moves with the sample, and so does the path it came by.**
gitlab-runner, on three sizes of the same board:

    n=15   skeleton 0                top section 53%
    n=20   skeleton 1  (by rate)     top section 65%
    n=32   skeleton 3  (by pairs)    top section 38%

It does not only cross the threshold in both directions. It changes *which*
rule carried it: at twenty exemplars one section is common enough on its own,
at thirty-two none is and three pairs are instead. Both readings are correct
about the tickets they saw.

That is a fact about mining a ranked subset, not a bug, and it is why a figure
from here is not settled. Use it to decide which boards are worth collecting
properly, and nothing more.

    python tools/probe_section_rates.py
    python tools/probe_section_rates.py --sample 120 --keep 40
    python tools/probe_section_rates.py --gitlab myorg/myrepo

Public boards read without credentials, same as `tests/fleet.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ticket_ai_mcp.corpus import by_mining
from ticket_ai_mcp.profile import build
from ticket_ai_mcp.trackers import build as tracker_for

# Anything in this band means the threshold decided the answer rather than
# confirming it, and those are the boards worth knowing about.
NEAR_LOW, NEAR_HIGH = 0.40, 0.75

# Profile.skeleton's default. Both of its paths use it: a board-wide rate, and
# the rate at which one heading implies another.
FLOOR = 0.6

GITLAB = [
    "gitlab-org/gitlab-runner",
    "libeigen/eigen",
]

# "base url|PROJECT", the same shape tests/fleet.py uses.
JIRA = [
    "https://issues.apache.org/jira|LUCENE",
    "https://jira.mongodb.org|SERVER",
    "https://jira.mariadb.org|MDEV",
    "https://issues.redhat.com|JBEAP",
]


def probe(kind: str, board: str, *, sample: int, keep: int) -> None:
    try:
        if kind == "gitlab":
            tracker = tracker_for("gitlab", url="https://gitlab.com", token="")
            project = board
        else:
            url, project = board.split("|", 1)
            tracker = tracker_for("jira", url=url, email="", token="", api="auto")

        gathered = by_mining(tracker, project, sample=sample, want=keep)
        if not gathered.enough:
            print(f"  {kind:6s} {board:42s} only {len(gathered.exemplars)} exemplars", flush=True)
            return

        profile = build(gathered.exemplars, project=project, tracker=kind)
        top = sorted(profile.sections, key=lambda s: -s.rate)[:5]
        rates = ", ".join(f"{s.rate * 100:.0f}%" for s in top) or "no sections at all"

        skeleton = {h for h, _ in profile.skeleton()}
        by_rate = {s.heading for s in profile.sections if s.rate >= FLOOR}
        pairs = sorted((c for c in profile.conditionals if c.rate >= FLOOR), key=lambda c: -c.rate)
        strongest = (
            f"{pairs[0].when_heading} -> {pairs[0].then_heading} {pairs[0].count}/{pairs[0].of}"
            if pairs
            else "-"
        )
        near = any(NEAR_LOW <= s.rate <= NEAR_HIGH for s in profile.sections)
        print(
            f"  {kind:6s} {board:42s} n={len(gathered.exemplars):2d} "
            f"skeleton={len(skeleton)} (rate {len(by_rate)}, pair {len(skeleton - by_rate)})"
            f" | {rates}" + ("  <-- near the threshold" if near else ""),
            flush=True,
        )
        if pairs:
            print(f"  {'':6s} {'':42s} strongest pair: {strongest}", flush=True)
    except Exception as exc:
        # One board that will not answer should not cost the other eleven. The
        # distribution is the output, and a gap in it is better than no output.
        print(f"  {kind:6s} {board:42s} FAILED {type(exc).__name__}: {exc}"[:150], flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--keep", type=int, default=25)
    parser.add_argument("--gitlab", action="append", help="extra gitlab.com project")
    parser.add_argument("--jira", action="append", help="extra board as 'base url|PROJECT'")
    args = parser.parse_args(argv)

    print(f"skeleton threshold is 60%; flagging anything from {NEAR_LOW:.0%} to {NEAR_HIGH:.0%}\n")
    fleets = (
        ("gitlab", GITLAB + (args.gitlab or [])),
        ("jira", JIRA + (args.jira or [])),
    )
    for kind, boards in fleets:
        # Naming a board that is already in the list should not probe it twice;
        # dict keeps the order they were written in.
        for board in dict.fromkeys(boards):
            probe(kind, board, sample=args.sample, keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
