# SPDX-License-Identifier: MIT

"""Getting the exemplar tickets, whichever way the caller has them.

Two paths in, one path out.

**The caller names them.** Someone who already knows which five tickets are the
good ones should not have to argue with a heuristic. Their list is taken as
given: no scoring, no exclusions, no bot filter. If they name a ticket with an
empty description, that is their answer about house style and the profile will
say so.

**Nobody names any.** Then the tracker is mined - the case this tool was
actually built for, since the honest answer to "give me some examples" is
usually "I do not have any to hand".

The mining path is the expensive one and it is worth being blunt about why:
ranking needs comments, linked changes and reopens, and those are one or two
extra requests **per ticket**. Sampling 200 tickets is therefore a few hundred
API calls and a minute or two. `on_progress` exists so a caller can say that
out loud instead of appearing to hang.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .contrast import Contrast
from .contrast import build as build_contrast
from .mining import Exemplar, Rejected, pick
from .schemas import TicketDetail, TicketQuery
from .trackers import Tracker, TrackerError

Progress = Callable[[int, int, str], None]

# Two years back. Far enough to find enough closed tickets on a quiet project,
# recent enough that the corpus describes how the team writes now rather than
# how it wrote under a process it has since abandoned.
DEFAULT_WINDOW_DAYS = 730


@dataclass(frozen=True, slots=True)
class Gathered:
    exemplars: list[Exemplar]
    rejected: list[Rejected]
    considered: int
    source: str
    errors: tuple[str, ...] = ()
    # Not the same thing as an error, and counting them together made a board
    # with one unreadable ticket and a board the instance half-serves look
    # alike. An error is about one ticket; a limit is about the whole run.
    limits: tuple[tuple[str, dict], ...] = ()
    # What separated the tickets that shipped from the ones that stalled.
    # Only mining produces one: a caller who named their own exemplars gave no
    # negative class to compare against, and inventing one would be a guess.
    contrast: Contrast | None = None

    @property
    def enough(self) -> bool:
        return len(self.exemplars) >= 5


def _detail(tracker: Tracker, ticket, errors: list[str]) -> TicketDetail | None:
    """Fetch a ticket's history, downgrading a failure to a note.

    One issue whose comments 403 should cost that issue, not the run. The
    reason is collected and reported rather than swallowed, because a sample of
    200 that quietly became 40 is the kind of thing that makes every number
    afterwards wrong in a way nobody notices.
    """
    try:
        return tracker.detail(ticket)
    except TrackerError as exc:
        errors.append(f"{ticket.key}: {exc}")
        return None


def _degradations(tracker: Tracker) -> tuple[tuple[str, dict], ...]:
    """What the tracker could not read, if it keeps track.

    Optional, like changed_files: an adapter that always gets everything has
    nothing to say here. The one that does is GitLab read anonymously, which
    serves an issue and its merge requests but not its comments - and a corpus
    with no comments cannot tell a ticket that stalled from one a bot closed.
    Degrading is the right behaviour; degrading quietly is not.
    """
    report = getattr(tracker, "degradations", None)
    return tuple(report()) if callable(report) else ()


def from_keys(
    tracker: Tracker,
    project: str,
    keys: list[str],
    *,
    on_progress: Progress | None = None,
) -> Gathered:
    """Build a corpus from tickets the caller named.

    Scored for the report, but never filtered: the caller's judgement outranks
    the heuristic, and silently dropping one of five hand-picked examples would
    be the most confusing thing this tool could do.
    """
    from .mining import score

    errors: list[str] = []
    exemplars: list[Exemplar] = []
    for i, key in enumerate(keys, 1):
        if on_progress:
            on_progress(i, len(keys), f"reading {key}")
        try:
            ticket = tracker.fetch(project, key)
        except TrackerError as exc:
            errors.append(f"{key}: {exc}")
            continue
        detail = _detail(tracker, ticket, errors) or TicketDetail(ticket=ticket)
        exemplars.append(score(detail))
    return Gathered(
        exemplars=exemplars,
        rejected=[],
        considered=len(keys),
        source="tickets you named",
        errors=tuple(errors),
        limits=_degradations(tracker),
    )


def by_mining(
    tracker: Tracker,
    project: str,
    *,
    sample: int = 150,
    want: int = 30,
    window_days: int = DEFAULT_WINDOW_DAYS,
    labels: tuple[str, ...] = (),
    on_progress: Progress | None = None,
) -> Gathered:
    """Find exemplars in the tracker's own history.

    Only closed tickets are sampled. An open ticket may be beautifully written,
    but nothing about it yet shows that anyone could act on it, and every
    ranking signal in `mining.py` needs that.
    """
    since = datetime.now(UTC) - timedelta(days=window_days)
    query = TicketQuery(
        project=project,
        state="closed",
        labels=labels,
        updated_after=since,
        limit=sample,
    )
    if on_progress:
        on_progress(0, sample, "listing closed tickets")
    tickets = tracker.search(query)

    errors: list[str] = []
    details: list[TicketDetail] = []
    for i, ticket in enumerate(tickets, 1):
        if on_progress:
            on_progress(i, len(tickets), f"reading history of {ticket.key}")
        detail = _detail(tracker, ticket, errors)
        if detail:
            details.append(detail)

    if tickets and not details:
        # Every ticket listed, none readable. This is not a quiet project and
        # it is not a strict filter - it is one failure repeated, and the
        # profile built on top of it is a page of zeroes that reads like an
        # answer. Found on a public GitLab board read anonymously: the issues
        # listed fine and every history behind them came back 401.
        raise TrackerError(
            f"{len(tickets)} closed tickets were listed in {project} and none of "
            f"them could be read. The first reason was - {errors[0]}"
        )

    exemplars, rejected = pick(details, want=want)
    # Every ticket read is compared, not only the ones kept as exemplars: the
    # rejected half is exactly the negative class this needs.
    return Gathered(
        exemplars=exemplars,
        rejected=rejected,
        considered=len(tickets),
        source=f"the last {len(tickets)} closed tickets",
        errors=tuple(errors),
        limits=_degradations(tracker),
        contrast=build_contrast(details),
    )
