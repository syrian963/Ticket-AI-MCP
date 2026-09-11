# SPDX-License-Identifier: MIT

"""Collect one board into `evals/dataset/`. Run by hand, output committed.

This is the only part of the evaluation that touches a tracker. It runs when a
board is added and then not again, which is the point: the dataset has to sit
still while models and prompts move.

**The split is the reason this is a tool and not a one-liner.** A case whose
ticket also went into the profile is a case the profile was fitted to. The
draft would then be marked against a corpus that already contains the answer,
and the score would come out flattering for a reason that has nothing to do
with the model. So the exemplars are split before the profile is built, and the
held-out ones become the cases.

The split is by a hash of the ticket id rather than by rank. Holding out the
lowest-scoring exemplars would be simpler and would quietly make every case
harder than the corpus that judges it.

    python tools/build_eval_dataset.py --tracker github --project pydantic/pydantic \\
        --slug pydantic --sample 200 --keep 40

Only public boards. A tracker that needs a token to read is not a board this
dataset may contain.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ticket_ai_mcp.config import settings, tracker_for
from ticket_ai_mcp.corpus import by_mining
from ticket_ai_mcp.evals.dataset import DATASET_DIR, Case, write_board
from ticket_ai_mcp.mining import Exemplar
from ticket_ai_mcp.profile import build

HOLDOUT_PERCENT = 30


def _bucket(uid: str) -> int:
    """A stable 0-99 for a ticket, independent of Python's hash seed.

    `hash()` is salted per process, so a dataset rebuilt tomorrow would split
    differently and the two would not be comparable.
    """
    return int(hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8], 16) % 100


def board_url(ticket_url: str) -> str:
    """The board a ticket belongs to, from the ticket's own address.

    Chopping two segments off the end works for GitHub and Jira and produces
    `https://gitlab.com/inkscape/inkscape/-` on GitLab, whose issue addresses
    carry a `/-/` separator before the collection. That trailing dash is a real
    404, so the separator is handled first and the generic rule is the
    fallback.
    """
    head, sep, _ = ticket_url.partition("/-/")
    if sep:
        return head
    return ticket_url.rsplit("/", 2)[0]


def split(exemplars: list[Exemplar], holdout: int) -> tuple[list[Exemplar], list[Exemplar]]:
    """Corpus first, cases second."""
    corpus: list[Exemplar] = []
    cases: list[Exemplar] = []
    for exemplar in exemplars:
        target = cases if _bucket(exemplar.detail.ticket.uid) < holdout else corpus
        target.append(exemplar)
    return corpus, cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracker", required=True, help="github, gitlab or jira")
    parser.add_argument("--project", required=True)
    parser.add_argument("--slug", required=True, help="directory name under evals/dataset")
    parser.add_argument("--sample", type=int, default=200, help="closed tickets to consider")
    parser.add_argument("--keep", type=int, default=45, help="exemplars to keep before the split")
    parser.add_argument("--holdout", type=int, default=HOLDOUT_PERCENT, help="percent held out")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = settings(args.tracker, args.project)
    tracker = tracker_for(cfg.tracker)

    def progress(done: int, total: int, what: str) -> None:
        print(f"  {done}/{total} {what}", file=sys.stderr)

    gathered = by_mining(
        tracker, cfg.project, sample=args.sample, want=args.keep, on_progress=progress
    )
    if not gathered.enough:
        print(
            f"only {len(gathered.exemplars)} exemplars, not enough to build a board",
            file=sys.stderr,
        )
        return 1

    corpus, held = split(gathered.exemplars, args.holdout)
    if len(corpus) < 5 or not held:
        print(
            f"split left {len(corpus)} in the corpus and {len(held)} cases; "
            "raise --keep or --sample",
            file=sys.stderr,
        )
        return 1

    profile = build(
        corpus,
        project=cfg.project,
        tracker=cfg.tracker,
        contrast=gathered.contrast,
        limits=gathered.limits,
    )

    cases = [
        Case(
            key=exemplar.detail.ticket.key,
            title=exemplar.detail.ticket.title,
            url=exemplar.detail.ticket.url,
            reference=exemplar.detail.ticket.description,
            labels=exemplar.detail.ticket.labels,
        )
        for exemplar in held
    ]

    directory = args.out or (DATASET_DIR / args.slug)
    write_board(
        directory,
        tracker=cfg.tracker,
        project=cfg.project,
        url=board_url(held[0].detail.ticket.url),
        fetched_at=datetime.now(UTC).date().isoformat(),
        profile=profile,
        cases=cases,
    )
    print(
        f"{directory}: {len(cases)} cases, profile from {len(corpus)} exemplars, "
        f"language {profile.language or 'unknown'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
