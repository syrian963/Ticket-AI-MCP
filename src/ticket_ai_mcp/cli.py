# SPDX-License-Identifier: MIT

"""The command line, which is also how the MCP server gets debugged.

The verbs, roughly in the order a ticket passes through them:

- `learn` builds the profile and caches it. Once per project.
- `style` prints what it learned.
- `context` gathers what already exists about a subject.
- `compose` writes a ticket from a title. The one verb that needs a model.
- `gaps` compares the declared issue template with the tickets that arrive.
- `draft` checks a ticket you have written but not created.
- `review` measures one that exists.
- `open` reviews every open ticket, worst first.
- `models` lists what the configured endpoint can reach.
- `ui` serves a local page for the parts that want a text box.

`learn` is separate from the rest on purpose. Mining is minutes of API calls
and the answer barely moves week to week, so it is a thing you do once and
keep - not something to repeat on every review and make the tool feel broken.

Everything except `compose` runs with no model at all, and that is the default.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .compose import compose
from .config import cache_dir, settings, tracker_for, writer_for
from .context import gather
from .corpus import Gathered, by_mining, from_keys
from .i18n import SUPPORTED, ticket_language
from .profile import Profile, build
from .report import (
    render_batch,
    render_context,
    render_contrast,
    render_gaps,
    render_profile,
    render_review,
)
from .review import review as review_one
from .review import review_draft
from .schemas import TicketQuery
from .templates import compare as compare_templates
from .templates import read as read_templates
from .trackers import TrackerError, available
from .writers import WriterError


def _profile_path(slug: str):
    return cache_dir() / f"profile-{slug}.json"


def _load(slug: str) -> Profile:
    path = _profile_path(slug)
    if not path.exists():
        raise TrackerError(
            f"No house style has been learned for this project yet. "
            f"Run `ticket-ai learn` first - it writes {path}."
        )
    profile = Profile.from_json(path.read_text(encoding="utf-8"))
    return profile.with_language(ticket_language())


def _load_optional(slug: str) -> Profile | None:
    """The profile if one has been learned. Context is useful without it."""
    path = _profile_path(slug)
    if not path.exists():
        return None
    profile = Profile.from_json(path.read_text(encoding="utf-8"))
    return profile.with_language(ticket_language())


def _progress(step: int, total: int, message: str) -> None:
    # Stderr, so `ticket-ai style > file.md` stays clean.
    print(f"  [{step}/{total}] {message}", file=sys.stderr)


def cmd_learn(args: argparse.Namespace) -> int:
    cfg = settings(args.tracker, args.project)
    tracker = tracker_for(cfg.tracker)

    gathered: Gathered
    if args.from_tickets:
        keys = [k.strip() for k in args.from_tickets.split(",") if k.strip()]
        gathered = from_keys(tracker, cfg.project, keys, on_progress=_progress)
    else:
        gathered = by_mining(
            tracker,
            cfg.project,
            sample=args.sample,
            want=args.keep,
            labels=tuple(args.label or ()),
            on_progress=_progress,
        )

    profile = build(
        gathered.exemplars,
        project=cfg.project,
        tracker=cfg.tracker,
        contrast=gathered.contrast,
        limits=gathered.limits,
    )
    path = _profile_path(cfg.slug)
    path.write_text(profile.to_json(), encoding="utf-8")

    print(render_profile(profile, gathered))
    if gathered.contrast:
        print(render_contrast(gathered.contrast))
    print(f"\nSaved to {path}", file=sys.stderr)
    if not gathered.enough:
        print(
            "\nToo few exemplars to say much. Try a wider --sample, or name the "
            "good tickets yourself with --from.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_style(args: argparse.Namespace) -> int:
    cfg = settings(args.tracker, args.project)
    print(render_profile(_load(cfg.slug)))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    cfg = settings(args.tracker, args.project)
    tracker = tracker_for(cfg.tracker)
    root = Path(args.repo or os.environ.get("TICKET_AI_REPO") or Path.cwd())
    context = gather(
        " ".join(args.subject),
        tracker,
        cfg.project,
        profile=_load_optional(cfg.slug),
        repo=root,
    )
    print(render_context(context))
    return 0


def cmd_gaps(args: argparse.Namespace) -> int:
    """The declared issue template against the tickets that actually arrive."""
    cfg = settings(args.tracker, args.project)
    profile = _load(cfg.slug)
    root = Path(args.repo or os.environ.get("TICKET_AI_REPO") or Path.cwd())
    declared = read_templates(root)
    gaps, undeclared = compare_templates(declared, profile)
    print(render_gaps(declared, gaps, undeclared, profile.sample_size))
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    """Check a draft before it becomes a ticket.

    The body comes from a file or from stdin, because a ticket description is
    a multi-paragraph thing and putting one on a command line is a fight with
    the shell that nobody wins.
    """
    cfg = settings(args.tracker, args.project)
    profile = _load(cfg.slug)
    body = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    result = review_draft(
        args.title,
        body,
        profile,
        labels=tuple(args.label or ()),
    )
    print(render_review(result))
    if result.alignment is not None and result.alignment < args.fail_under:
        return 1
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    """A title in, a drafted ticket out. Needs a model; says so if there is none."""
    cfg = settings(args.tracker, args.project)
    profile = _load(cfg.slug)

    writer = writer_for(args.writer, args.model)
    if writer is None:
        print(
            "No model configured, so there is nothing here that can write prose.\n\n"
            "  TICKET_AI_WRITER=ollama    a model on this machine. No key, nothing\n"
            "                             leaves the laptop. Needs `ollama serve`.\n"
            "  TICKET_AI_WRITER=openai    any OpenAI-compatible endpoint. Also set\n"
            "                             TICKET_AI_BASE_URL, TICKET_AI_MODEL and\n"
            "                             TICKET_AI_API_KEY.\n\n"
            "Then `ticket-ai models` lists what that endpoint can reach.\n\n"
            "Everything else works without a model: `context` gathers the evidence "
            "and `style` gives you the shape.",
            file=sys.stderr,
        )
        return 2

    context = None
    if not args.no_context:
        root = Path(args.repo or os.environ.get("TICKET_AI_REPO") or Path.cwd())
        context = gather(args.title, tracker_for(cfg.tracker), cfg.project, repo=root)

    result = compose(
        args.title,
        profile,
        writer,
        context=context,
        labels=tuple(args.label or ()),
    )

    if args.out:
        Path(args.out).write_text(result.body, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(result.body)

    # The measurement goes to stderr so the body can be piped or redirected on
    # its own - `--out draft.md` in a workflow, `> draft.md` by hand.
    print(
        f"\n--- {result.model}, {result.attempts} attempt(s) ---\n" + render_review(result.review),
        file=sys.stderr,
    )
    if result.review.alignment is not None and result.review.alignment < args.fail_under:
        return 1
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """List the models the configured backend can reach."""
    from .workflow import snippet

    if args.workflow:
        print(snippet())
        return 0

    # Defaulting to ollama is right for a question like "what can I use?" -
    # it is the backend that needs no key. Saying which one was tried is the
    # part that was missing: with nothing configured, the old message talked
    # about a key that had never been set, for an endpoint it did not name.
    chosen = args.writer or os.environ.get("TICKET_AI_WRITER") or "ollama"
    writer = writer_for(chosen)
    found = writer.models() if writer else []
    if not found:
        where = getattr(writer, "base_url", "") or "the configured endpoint"
        print(
            f"No models came back from {where} ({chosen}).",
            file=sys.stderr,
        )
        if not os.environ.get("TICKET_AI_WRITER") and not args.writer:
            print(
                "Nothing is configured, so this tried a local Ollama. Start it, or "
                "set TICKET_AI_WRITER to openai with TICKET_AI_BASE_URL and "
                "TICKET_AI_API_KEY for a hosted one.",
                file=sys.stderr,
            )
        else:
            print(
                "It may not serve /models, it may not be running, or the key may be "
                "wrong. Set TICKET_AI_MODEL by hand if you already know the name.",
                file=sys.stderr,
            )
        return 1
    current = getattr(writer, "model", "")
    for name in found:
        print(f"{'*' if name == current else ' '} {name}")
    print(
        f"\n{len(found)} models. Pick one with TICKET_AI_MODEL or --model; "
        "* is the current default.",
        file=sys.stderr,
    )
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui import serve

    cfg = settings(args.tracker, args.project)
    serve(cfg, _load(cfg.slug), port=args.port, language=args.lang)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    cfg = settings(args.tracker, args.project)
    profile = _load(cfg.slug)
    tracker = tracker_for(cfg.tracker)
    worst = 1.0
    for key in args.tickets:
        ticket = tracker.fetch(cfg.project, key)
        result = review_one(ticket, profile)
        # An unmeasurable ticket cannot fail a gate. Blocking a merge on the
        # absence of evidence is how a tool like this gets switched off.
        if result.alignment is not None:
            worst = min(worst, result.alignment)
        print(render_review(result))
    # A non-zero exit below the threshold is what makes this usable in CI.
    return 1 if worst < args.fail_under else 0


def cmd_open(args: argparse.Namespace) -> int:
    cfg = settings(args.tracker, args.project)
    profile = _load(cfg.slug)
    tracker = tracker_for(cfg.tracker)
    tickets = tracker.search(TicketQuery(project=cfg.project, state="open", limit=args.limit))
    print(render_batch([review_one(t, profile) for t in tickets]))
    return 0


def _force_utf8() -> None:
    """Make the streams carry the text the tickets are actually written in.

    Windows still hands a console a legacy code page, and Python honours it: a
    German ticket composed on this machine printed `L?ndern`, and a redirect to
    a file wrote the same mojibake. Since the whole point here is producing
    text in the team's own language, the encoding is not something to leave to
    the environment.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A stream that will not be reconfigured is not worth failing over.
            pass


def build_parser() -> argparse.ArgumentParser:
    """Every command, in one place something other than `main` can read.

    Split out so the documentation checks can ask what the commands are. They
    scraped `--help` at first, and reported the choices of `--tracker` as
    undocumented commands - a parser knows the difference and prose does not.
    """
    parser = argparse.ArgumentParser(
        prog="ticket-ai",
        description=(
            "Learn what a good ticket looks like in your tracker, then measure new ones against it."
        ),
    )
    parser.add_argument("--tracker", choices=available(), help="overrides TICKET_AI_TRACKER")
    parser.add_argument("--project", help="overrides TICKET_AI_PROJECT")
    sub = parser.add_subparsers(dest="command", required=True)

    learn = sub.add_parser("learn", help="build the house style profile")
    learn.add_argument(
        "--from",
        dest="from_tickets",
        help="comma-separated tickets to learn from, e.g. #12,#40,#71. "
        "Taken as given: no filtering, no scoring against them.",
    )
    learn.add_argument("--sample", type=int, default=150, help="closed tickets to consider")
    # `--keep` and not `--want`, to match the MCP tool's `keep`. One knob with
    # two names across two front ends of the same tool is a papercut nobody
    # reports and everybody hits; the old spelling still works.
    learn.add_argument(
        "--keep",
        "--want",
        type=int,
        default=30,
        dest="keep",
        help="exemplars to keep",
    )
    learn.add_argument("--label", action="append", help="only mine tickets with this label")
    learn.set_defaults(func=cmd_learn)

    style = sub.add_parser("style", help="print the learned profile")
    style.set_defaults(func=cmd_style)

    ctx = sub.add_parser(
        "context",
        help="what is already known about a subject, before you write the ticket",
    )
    ctx.add_argument("subject", nargs="+")
    ctx.add_argument(
        "--repo",
        help="checkout to search. Defaults to TICKET_AI_REPO, then the working directory.",
    )
    ctx.set_defaults(func=cmd_context)

    gap = sub.add_parser(
        "gaps",
        help="the issue template this repo declares, against the tickets it gets",
    )
    gap.add_argument("--repo", help="checkout holding the templates. Defaults to TICKET_AI_REPO.")
    gap.set_defaults(func=cmd_gaps)

    dft = sub.add_parser(
        "draft",
        help="check a ticket you have written but not created yet",
    )
    dft.add_argument("--title", required=True)
    dft.add_argument("--file", help="the description. Reads stdin when omitted.")
    dft.add_argument("--label", action="append", help="labels the ticket will carry")
    dft.add_argument("--fail-under", type=float, default=0.0)
    dft.set_defaults(func=cmd_draft)

    comp = sub.add_parser(
        "compose",
        help="write a ticket from a title. Needs a model - see `ticket-ai models`.",
    )
    comp.add_argument("--title", required=True)
    comp.add_argument("--writer", help="overrides TICKET_AI_WRITER")
    comp.add_argument("--model", help="overrides TICKET_AI_MODEL. Any the backend can reach.")
    comp.add_argument("--label", action="append", help="labels the ticket will carry")
    comp.add_argument("--repo", help="checkout to search for related files")
    comp.add_argument(
        "--no-context",
        action="store_true",
        help="skip the tracker and codebase search. Faster, and worse.",
    )
    comp.add_argument("--out", help="write the body here instead of to stdout")
    comp.add_argument("--fail-under", type=float, default=0.0)
    comp.set_defaults(func=cmd_compose)

    mods = sub.add_parser("models", help="which models the backend can reach")
    mods.add_argument("--writer", help="overrides TICKET_AI_WRITER")
    mods.add_argument(
        "--workflow",
        action="store_true",
        help="print a GitHub Actions workflow that drafts issues opened with only a title",
    )
    mods.set_defaults(func=cmd_models)

    web = sub.add_parser("ui", help="a local page for checking drafts")
    web.add_argument("--port", type=int, default=8760, help="0 asks for a free one")
    web.add_argument(
        "--lang",
        choices=SUPPORTED,
        help="interface language. Defaults to TICKET_AI_UI_LANGUAGE, then English. "
        "This is the chrome, not the language tickets are written in - that is "
        "TICKET_AI_TICKET_LANGUAGE.",
    )
    web.set_defaults(func=cmd_ui)

    rev = sub.add_parser("review", help="measure tickets against the profile")
    rev.add_argument("tickets", nargs="+")
    rev.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="exit non-zero if any ticket scores below this (0-1). For CI.",
    )
    rev.set_defaults(func=cmd_review)

    op = sub.add_parser("open", help="review every open ticket, worst first")
    op.add_argument("--limit", type=int, default=100)
    op.set_defaults(func=cmd_open)

    return parser


def subcommands() -> tuple[str, ...]:
    """The registered subcommand names."""
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return tuple(sorted(action.choices))
    return ()


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TrackerError, WriterError) as exc:
        # These messages are written to be the whole answer, so they are
        # printed as themselves rather than wrapped in a traceback. A writer
        # that is not set up is the most likely thing a new user hits, and a
        # stack trace would bury the one sentence that fixes it.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
