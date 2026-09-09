# SPDX-License-Identifier: MIT

"""Everything worth knowing before writing a ticket, gathered without a model.

This is the half of ticket-writing a language model cannot do for itself, and
the division is worth being precise about, because it is the reason this tool
needs no API key of its own.

A model reading a repository can work out what the code does. It cannot know:

- which of the team's past tickets covered this ground, and what they were
  called;
- **which files the merge requests for those tickets actually changed** - that
  is in the tracker's history and nowhere else, and it is usually the single
  most useful sentence you can hand someone;
- what shape a ticket has to be to get picked up here.

So this collects those, deterministically, and stops. The prose is written by
whoever asked - which, when this runs as an MCP server, is a model that is
already in the room and already has the file tools it needs. No second model,
no key, no per-call cost.

The cost that does exist is API calls: reading the changed files of five merge
requests is five more requests, so it is capped and it degrades to nothing
rather than failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .codebase import FileHit
from .codebase import search as _search_files
from .profile import Profile
from .schemas import TicketQuery
from .similar import Match, rank
from .trackers import Tracker, TrackerError

# How many past tickets to open the merge requests of. Each one is an extra
# request or two, and the fourth-best match is rarely worth the wait.
CHANGES_FOR_TOP = 3


@dataclass(frozen=True, slots=True)
class PriorArt:
    """A past ticket that covered similar ground, and what shipped for it."""

    match: Match
    files: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return self.match.ticket.key


@dataclass(frozen=True, slots=True)
class Context:
    subject: str
    prior: tuple[PriorArt, ...] = ()
    files: tuple[FileHit, ...] = ()
    searched: int = 0
    repo: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def touched(self) -> tuple[str, ...]:
        """Files the prior art changed, most frequently changed first.

        Ordered by how many of the related tickets touched each file, because
        a file three past tickets on this subject all changed is where this
        one is going too.
        """
        counts: dict[str, int] = {}
        for art in self.prior:
            for path in art.files:
                counts[path] = counts.get(path, 0) + 1
        return tuple(sorted(counts, key=lambda p: (-counts[p], p)))


def gather(
    subject: str,
    tracker: Tracker,
    project: str,
    *,
    profile: Profile | None = None,
    repo: Path | None = None,
    search_limit: int = 120,
) -> Context:
    """Collect the evidence for one subject.

    Both halves degrade independently: a tracker that will not answer still
    leaves the codebase search, and a missing checkout still leaves the prior
    art. Neither failure is worth aborting on, because half an answer here is
    most of the value.
    """
    notes: list[str] = []

    tickets = []
    try:
        # Closed tickets first - a closed ticket has a merged change attached,
        # which is what makes the file list possible.
        tickets = tracker.search(TicketQuery(project=project, state="closed", limit=search_limit))
    except TrackerError as exc:
        notes.append(f"Could not search the tracker: {exc}")

    prior: list[PriorArt] = []
    for i, match in enumerate(rank(subject, tickets, limit=5)):
        files: tuple[str, ...] = ()
        if i < CHANGES_FOR_TOP:
            try:
                detail = tracker.detail(match.ticket)
                for change in detail.linked_changes:
                    if change.merged:
                        files += tracker.changed_files(project, change)
            except TrackerError:
                # One unreadable merge request costs that one, not the run.
                pass
        prior.append(PriorArt(match=match, files=tuple(dict.fromkeys(files))))

    files: tuple[FileHit, ...] = ()
    if repo is not None:
        if repo.is_dir():
            files = tuple(_search_files(repo, subject))
            if not files:
                notes.append(f"Nothing in {repo} matched the words in the subject.")
        else:
            notes.append(f"{repo} is not a directory, so no code was searched.")

    if profile is not None and profile.sample_size == 0:
        notes.append("No house style has been learned, so there is no template to follow.")

    return Context(
        subject=subject,
        prior=tuple(prior),
        files=files,
        searched=len(tickets),
        repo=str(repo) if repo else None,
        notes=tuple(notes),
    )
