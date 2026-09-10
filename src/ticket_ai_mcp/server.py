# SPDX-License-Identifier: MIT

"""The MCP server.

Seven tools, and the division of labour between them and the model is the point:

**The server measures. The model writes.** Nothing here calls a language model,
scores prose, or has an opinion about whether a ticket is a good idea. It
counts what the team already does and hands back numbers. Drafting the ticket,
judging whether the acceptance criteria are any good, deciding the thing is
worth building at all - that is the model's half, and it does it better with
the numbers than without them.

That split is also why `house_style` and `ticket_template` exist as separate
tools from `review_ticket`. An assistant about to *write* a ticket needs the
template and the targets; one reviewing a draft needs the findings. Handing
either one both is how you get a model that describes the profile back at the
user instead of using it.

`learn_conventions` is slow - minutes, on a real project - and says so in its
description, reports progress while it runs, and caches the result. An
assistant that re-mines a tracker on every question will be turned off.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from .config import cache_dir, settings, tracker_for
from .context import gather
from .corpus import by_mining, from_keys
from .i18n import ticket_language
from .profile import Profile, build
from .report import (
    render_batch,
    render_context,
    render_contrast,
    render_gaps,
    render_profile,
    render_review,
)
from .review import EXPECT_RATE
from .review import review as _review
from .review import review_draft as _review_draft
from .schemas import TicketQuery
from .templates import compare as compare_templates
from .templates import read as read_templates
from .trackers import TrackerError, available

INSTRUCTIONS = f"""Measures tickets against the way this team already writes them.

Nothing here writes to the tracker. Every tool is a read.

**Start with `house_style`.** If it says no profile has been learned, run
`learn_conventions` once - it takes minutes, and everything else depends on it.

Which tool answers which question:

- *What does a good ticket look like here?* `house_style`.
- *Write a ticket for me.* `ticket_template` **and** `ticket_context`, then read
  the files it points at, then write it yourself. See below.
- *Is this ticket ready to work on?* `review_ticket`.
- *What should we clean up before planning?* `review_open_tickets`.

**Writing a ticket is a four-step job and this server does two of them.** When
the user gives you a subject - a sentence, a bug they hit, a feature they want:

1. `ticket_template` tells you the shape: which sections, how long, what
   language, which labels.
2. `ticket_context` tells you what already exists: related past tickets, the
   files whose merge requests fixed them, and the files in the checkout that
   mention it.
3. **You read those files.** The context tool runs a text search, not an
   analysis - it will list files that merely share a word, and the point of it
   is to tell you where to look, not to save you looking.
4. **You write the ticket**, in the team's shape, naming what you found, and
   then run `review_draft` on it and fix what that finds. Check before the
   ticket exists, not after: creating it first notifies the board and turns
   every correction into an edit with a history.

Do not hand the user the raw output of steps 1 and 2. They asked for a ticket.

Two things to keep in mind when you read the output:

- The numbers describe **this team**, not good practice in general. A finding
  that says 78% of shipped tickets have an acceptance-criteria section is worth
  repeating to the user verbatim; "tickets should have acceptance criteria" is
  not, and they did not need a tool for it.
- `alignment` is not quality. It measures distance from the tickets that
  historically got built here. A one-line ticket from someone who knows exactly
  what they mean can score badly and be fine. Say so when it applies.

Trackers this build supports: {", ".join(available())}.
"""

server = MCPServer(name="ticket-ai", instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def _path(slug: str):
    return cache_dir() / f"profile-{slug}.json"


def _load(slug: str) -> Profile | None:
    path = _path(slug)
    if not path.exists():
        return None
    profile = Profile.from_json(path.read_text(encoding="utf-8"))
    return profile.with_language(ticket_language())


def _need(slug: str, project: str) -> Profile:
    profile = _load(slug)
    if profile is None:
        raise TrackerError(
            f"No house style learned for {project} yet. Call learn_conventions first. "
            "It reads a few hundred closed tickets, takes a minute or two, and the "
            "result is cached, so it only has to happen once."
        )
    return profile


def answers(fn):
    """Return a tracker's refusal as the answer instead of raising it.

    The MCP layer treats an exception out of a tool as a crash: the client is
    told "Error executing tool ticket_template" and the exception's own text
    stays on the server. Measured against the real server, five of the eight
    tools did exactly that the first time anyone called them - so the sentence
    naming `learn_conventions`, and every message about a rate limit, a moved
    repository or a board with issues switched off, was written carefully and
    then thrown away before the person who needed it could read it.

    A `TrackerError` is not a crash. It is the tool answering: this cannot be
    done, and here is why. That belongs in the result, which is where
    `house_style` had always put it and why it was the one tool whose message
    ever reached anybody.

    Anything else still raises. A real bug should look like one.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except TrackerError as exc:
            return str(exc)

    return wrapper


@server.tool(annotations=READ_ONLY)
@answers
async def learn_conventions(
    ctx: Context,
    project: str | None = None,
    tracker: str | None = None,
    from_tickets: list[str] | None = None,
    sample: int = 150,
    keep: int = 30,
) -> str:
    """Learn how this team writes tickets, and cache the result.

    Slow: it reads the comments and linked changes of up to `sample` closed
    tickets, which is a few hundred API calls. Call it once per project, not
    once per question.

    Pass `from_tickets` when the user can name good examples - those are taken
    as given and nothing is filtered out. Leave it empty and the tracker is
    mined instead: closed tickets are ranked by whether a merge request shipped
    for them, whether anyone had to reopen them, and how many clarifying
    questions they drew before work started.
    """
    cfg = settings(tracker, project)
    client = tracker_for(cfg.tracker)

    async def report(step: int, total: int, message: str) -> None:
        # Best effort, always. A client that did not send a progress token, or
        # any caller reaching the tool outside a live request, must not be able
        # to kill a run that is otherwise several minutes from finishing.
        try:
            await ctx.report_progress(step, total or None, message)
        except Exception:
            pass

    def on_progress(step: int, total: int, message: str) -> None:
        anyio.from_thread.run(report, step, total, message)

    def work():
        if from_tickets:
            return from_keys(client, cfg.project, list(from_tickets), on_progress=on_progress)
        return by_mining(client, cfg.project, sample=sample, want=keep, on_progress=on_progress)

    gathered = await anyio.to_thread.run_sync(work)
    profile = build(
        gathered.exemplars,
        project=cfg.project,
        tracker=cfg.tracker,
        contrast=gathered.contrast,
        limits=gathered.limits,
    )
    _path(cfg.slug).write_text(profile.to_json(), encoding="utf-8")

    text = render_profile(profile, gathered)
    if gathered.contrast:
        text += "\n" + render_contrast(gathered.contrast)
    if not gathered.enough:
        text += (
            "\n\nToo few exemplars to draw conclusions from. Ask the user to name a "
            "few good tickets and pass them as from_tickets, or raise sample.\n"
        )
    return text


@server.tool(annotations=READ_ONLY)
@answers
async def house_style(project: str | None = None, tracker: str | None = None) -> str:
    """What this team's tickets look like: template, length, labels, habits.

    Reads the cached profile. Says so plainly if none has been learned yet
    rather than returning something empty that looks like an answer.
    """
    cfg = settings(tracker, project)
    profile = _load(cfg.slug)
    if profile is None:
        return (
            f"No house style learned for {cfg.project} yet. Call learn_conventions "
            "to build one - it takes a minute or two and is cached afterwards."
        )
    return render_profile(profile)


@server.tool(annotations=READ_ONLY)
@answers
async def ticket_template(project: str | None = None, tracker: str | None = None) -> str:
    """The skeleton to fill in when writing a new ticket here.

    Use this before drafting, not after. It returns the sections this team
    actually uses and the length they actually write - then write the ticket
    yourself. The server has no opinion about the content.
    """
    cfg = settings(tracker, project)
    profile = _need(cfg.slug, cfg.project)

    skeleton = profile.skeleton(EXPECT_RATE)
    lines = [f"Template from {profile.sample_size} tickets that shipped in {cfg.project}.", ""]
    if skeleton:
        for heading, why in skeleton:
            lines += [f"## {heading}", f"<!-- {why} -->", ""]
    else:
        lines += ["No recurring sections. This team writes prose, not a template.", ""]

    # A section that is only conventional alongside another one belongs in the
    # template as a condition, not as a heading to fill in regardless.
    conditional = [c for c in profile.conditionals if c.rate >= EXPECT_RATE]
    if conditional:
        lines += ["If you write one of these, write the other:", ""]
        for rule in conditional[:6]:
            lines.append(
                f"- **{rule.when_heading}** goes with **{rule.then_heading}** "
                f"({rule.count}/{rule.of}, against {round(rule.baseline * 100)}% overall)"
            )
        lines.append("")

    lines += [
        "---",
        "",
        f"Aim for about {round(profile.chars_median)} characters "
        f"(the middle half run {round(profile.chars_p25)} to {round(profile.chars_p75)}).",
    ]
    if profile.language:
        lines.append(f"Write it in {profile.language}.")
    if profile.common_labels:
        lines.append("Labels in use: " + ", ".join(f"`{c}`" for c, _ in profile.common_labels[:8]))
    if profile.label_groups:
        lines.append("Always set: " + ", ".join(f"{g}::" for g in profile.label_groups))
    if profile.checkbox_rate >= EXPECT_RATE:
        lines.append(f"{round(profile.checkbox_rate * 100)}% use a checklist.")
    if profile.notes:
        lines += ["", *(f"> {n}" for n in profile.notes)]
    return "\n".join(lines)


@server.tool(annotations=READ_ONLY)
@answers
async def template_gaps(
    repo: str | None = None,
    project: str | None = None,
    tracker: str | None = None,
) -> str:
    """Compare the issue template this repository declares with the tickets it gets.

    Reads `.github/ISSUE_TEMPLATE` or `.gitlab/issue_templates` from the
    checkout and lines each field up against how often tickets actually carry
    it. The gap is the finding, and it runs both ways:

    - A **required field almost nobody fills in** is a form asking for
      something people cannot easily supply. Say so: the cheap fix is to
      change the form, not to nag the team.
    - A **section most tickets carry that no form mentions** is a convention
      the project grew and never wrote down. Adding it to the template is how
      it survives the next person who joins.

    Use this when asked how to improve a board rather than one ticket. It is
    the only tool here that reads what the project said it wanted, instead of
    only what it does.
    """
    cfg = settings(tracker, project)
    profile = _need(cfg.slug, cfg.project)
    root = Path(repo or os.environ.get("TICKET_AI_REPO") or Path.cwd())
    declared = read_templates(root)
    gaps, undeclared = compare_templates(declared, profile)
    return render_gaps(declared, gaps, undeclared, profile.sample_size)


@server.tool(annotations=READ_ONLY)
@answers
async def ticket_context(
    subject: str,
    repo: str | None = None,
    project: str | None = None,
    tracker: str | None = None,
) -> str:
    """Gather what is known about a subject before you write the ticket for it.

    **Call this together with `ticket_template`, then write the ticket
    yourself.** This returns evidence, not prose - and specifically the
    evidence you cannot get by reading the repository:

    - which past tickets covered this ground, so you can say whether this is a
      duplicate before anyone spends a week on it;
    - **which files the merge requests for those tickets actually changed.**
      That exists only in the tracker's history. It is usually the fastest way
      to find where the work will land, and it is worth naming in the ticket.
    - which files in the checkout mention the subject, as a starting point for
      your own reading.

    Read the files it points at before drafting. The list is a search result,
    not an understanding of the code, and it will include things that merely
    share a word.
    """
    cfg = settings(tracker, project)
    client = tracker_for(cfg.tracker)
    profile = _load(cfg.slug)
    root = Path(repo or os.environ.get("TICKET_AI_REPO") or Path.cwd())

    def work():
        return gather(subject, client, cfg.project, profile=profile, repo=root)

    return render_context(await anyio.to_thread.run_sync(work))


@server.tool(annotations=READ_ONLY)
@answers
async def review_draft(
    title: str,
    description: str,
    labels: list[str] | None = None,
    project: str | None = None,
    tracker: str | None = None,
) -> str:
    """Check a ticket you have written but not created yet.

    **Call this on your own draft before showing it to the user**, and fix
    what it finds rather than reporting it. It is the same measurement
    `review_ticket` runs, so a draft that passes here passes there.

    This exists so the check happens before the point of no return. Creating
    the ticket first notifies whoever watches the board and turns every fix
    into an edit with a history.
    """
    cfg = settings(tracker, project)
    profile = _need(cfg.slug, cfg.project)
    result = _review_draft(title, description, profile, labels=tuple(labels or ()))
    return render_review(result)


@server.tool(annotations=READ_ONLY)
@answers
async def review_ticket(ticket: str, project: str | None = None, tracker: str | None = None) -> str:
    """Measure one ticket against the learned house style.

    Every finding cites a count over the exemplar sample. Repeat those numbers
    to the user - they are the difference between this and generic advice.
    """
    cfg = settings(tracker, project)
    profile = _need(cfg.slug, cfg.project)
    client = tracker_for(cfg.tracker)
    found = await anyio.to_thread.run_sync(lambda: client.fetch(cfg.project, ticket))
    return render_review(_review(found, profile))


@server.tool(annotations=READ_ONLY)
@answers
async def review_open_tickets(
    limit: int = 50,
    label: str | None = None,
    project: str | None = None,
    tracker: str | None = None,
) -> str:
    """Review every open ticket and list them least-aligned first.

    A planning tool: the answer to "what needs tidying before we can estimate
    any of this". One line per ticket, so call `review_ticket` for the detail
    on the ones that matter.
    """
    cfg = settings(tracker, project)
    profile = _need(cfg.slug, cfg.project)
    client = tracker_for(cfg.tracker)
    query = TicketQuery(
        project=cfg.project,
        state="open",
        labels=(label,) if label else (),
        limit=limit,
    )
    tickets = await anyio.to_thread.run_sync(lambda: client.search(query))
    return render_batch([_review(t, profile) for t in tickets])


def main() -> None:
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
