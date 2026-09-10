# SPDX-License-Identifier: MIT

"""Drive the whole tool across many public boards and record what breaks.

Not a test - a harness. The unit suite proves the code does what it was
written to do; this finds out what real trackers do that nobody wrote code
for. Every serious defect in this project so far came from pointing it at a
board nobody had tried: an endpoint that had moved to cursor paging, a Jira
renderer that dropped every code block, a staleness bot that filled the
"stalled" group with perfectly good tickets.

It reports five kinds of thing, and the anomalies are the point:

- **crashes** - an exception reached the top. Always a defect.
- **refusals** - the tool declined and said why. "This repository moved", "this
  one has issues switched off": correct answers about a board that cannot be
  profiled. Counting these as crashes buried the runs where something really
  did go wrong, so they are their own column.
- **limits** - it ran on part of a board. gitlab.com serves a public project's
  issues to anyone and withholds the comments, which weakens the ranking and
  the shipped-against-stalled comparison without breaking either. Correct
  behaviour, still worth printing beside the numbers it weakens.
- **anomalies** - it ran, and the answer looks wrong. A corpus of three from a
  sample of eighty, or a language guess that flips. These are where the next
  bug is.
- **shape** - the numbers, so a human can scan a table and see the outlier.

Run it directly; it is excluded from the suite because it needs a token and a
network, and a test that needs either is a test that fails for whoever forks
this.

    uv run python tests/fleet.py [sample] [how-many-boards] [github|gitlab|jira|both]

All three fleets by default, one per adapter. GitLab and Jira run without
credentials - that is how a stranger will first try this tool, and it exercises
paths a token hides.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from ticket_ai_mcp.corpus import by_mining
from ticket_ai_mcp.messages import render_note
from ticket_ai_mcp.profile import build
from ticket_ai_mcp.review import review
from ticket_ai_mcp.schemas import TicketQuery
from ticket_ai_mcp.trackers import TrackerError
from ticket_ai_mcp.trackers import build as tracker_for

# Chosen for variety rather than popularity: different languages, different
# issue cultures, different template discipline, and several that are known to
# behave oddly - monorepos, bot-heavy boards, boards with almost no prose.
GITHUB_BOARDS = [
    "pydantic/pydantic",
    "astral-sh/uv",
    "astral-sh/ruff",
    "home-assistant/core",
    "fastapi/fastapi",
    "encode/httpx",
    "psf/requests",
    "pallets/flask",
    "tiangolo/sqlmodel",
    "python-poetry/poetry",
    "pypa/pip",
    "sqlalchemy/alembic",
    "prettier/prettier",
    "vitejs/vite",
    "rollup/rollup",
    "denoland/deno",
    "biomejs/biome",
    "nodejs/undici",
    "rust-lang/mdBook",
    "clap-rs/clap",
    "serde-rs/serde",
    "tokio-rs/axum",
    "gohugoio/hugo",
    "spf13/cobra",
    "stretchr/testify",
    "jqlang/jq",
    "sharkdp/fd",
    "BurntSushi/ripgrep",
    "junegunn/fzf",
    "neovim/neovim",
]

# Public projects on gitlab.com, read without a token. For a long time this
# harness drove one adapter of three, and the GitLab one had never met a live
# instance - it had unit tests against recorded shapes, which is a different
# thing and did not catch either of the two defects the first anonymous run
# found in a minute.
GITLAB_BOARDS = [
    "inkscape/inkscape",
    "gitlab-org/gitlab-runner",
    "gitlab-org/cli",
    "fdroid/fdroidclient",
    "veloren/veloren",
    "libeigen/eigen",
]

# Public Jira instances, as "base url|PROJECT KEY", read without credentials.
# Both dialects on purpose: Hibernate is Cloud (REST v3, descriptions as an ADF
# tree), the other three are self-hosted Server or Data Center (REST v2,
# descriptions as wiki markup). They are not the same product and the code
# paths they exercise barely overlap.
#
# issues.redhat.com is here for the third case: a Cloud site behind a company
# hostname, answering 301 to redhat.atlassian.net. It is what broke detection.
JIRA_BOARDS = [
    "https://hibernate.atlassian.net|HHH",
    "https://hibernate.atlassian.net|HSEARCH",
    "https://issues.apache.org/jira|KAFKA",
    "https://issues.apache.org/jira|LUCENE",
    "https://jira.mongodb.org|SERVER",
    "https://jira.mariadb.org|MDEV",
    "https://issues.redhat.com|JBEAP",
]


@dataclass
class Result:
    tracker: str
    board: str
    ok: bool = False
    seconds: float = 0.0
    considered: int = 0
    exemplars: int = 0
    sections: int = 0
    language: str | None = None
    shipped: int = 0
    stalled: int = 0
    signals: int = 0
    conditionals: int = 0
    median_chars: int = 0
    crash: str = ""
    # A board the tool declined to profile, with its reason. Distinct from a
    # crash: "this repository moved" is the right answer, not a failure.
    refused: str = ""
    # Parts of the board the instance would not serve. Its own column for the
    # same reason refusals got one: routed through `anomalies` at first, and
    # six GitLab boards that all behaved correctly came back reported as six
    # boards with something wrong.
    limits: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)


def token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[9:]
    raise SystemExit("no github credential")


def sniff(result: Result, profile, gathered, tickets) -> list[str]:
    """Answers that ran but look wrong.

    Each of these was chosen because it is cheap to check and because a wrong
    answer of that shape has already happened at least once.
    """
    bad: list[str] = []
    if gathered.considered and result.exemplars < gathered.considered * 0.1:
        # Name the reason rather than guessing at one. "The exclusions may be
        # too harsh" sent a reader looking at the filter on a Hibernate board
        # whose recent closed tickets are dependency bumps with no description
        # - the filter was right and the sentence was not.
        why = ""
        if gathered.rejected:
            reasons = Counter(r.reason.split(" - ")[0] for r in gathered.rejected)
            reason, count = reasons.most_common(1)[0]
            why = f", {count} of them because the {reason}"
        bad.append(f"kept {result.exemplars} of {gathered.considered}{why}")
    if gathered.considered < 20:
        bad.append(f"only {gathered.considered} closed tickets came back - paging or filter")
    if profile.sample_size and not profile.sections:
        bad.append("no recurring heading at all across the whole corpus")
    if profile.language is None and profile.sample_size >= 10:
        bad.append("could not guess a language from a full corpus")
    # A rate of exactly 0 or 1 was flagged here at first, on the theory that it
    # is the shape a parsing bug takes. Checked against the raw API on three
    # boards it was true every time: pallets/flask labels nothing, gohugoio/hugo
    # labels everything. The check cost more attention than it was worth and is
    # gone; the adapters have their own tests for dropped fields.
    if profile.sample_size >= 10 and profile.label_rate == 0.0 and profile.assignee_rate == 0.0:
        # Both at zero together is different: it suggests the fields are not
        # being read rather than not being used.
        bad.append("neither labels nor assignees on any ticket - fields may not be parsing")
    if profile.chars_median and profile.chars_median < 120:
        bad.append(f"median description is {round(profile.chars_median)} characters")
    if gathered.errors:
        bad.append(f"{len(gathered.errors)} tickets could not be read")
    # The reviewer has to survive its own corpus.
    for ticket in tickets[:5]:
        try:
            review(ticket, profile)
        except Exception as exc:
            bad.append(f"review({ticket.key}) raised {type(exc).__name__}: {exc}")
            break
    return bad


def make(name: str, board: str, tracker_token: str):
    """The adapter for a fleet, configured the way a stranger would have it.

    GitLab and Jira are built without credentials on purpose. That is how
    someone trying this tool on a public board will run it, and it exercises
    the degradation paths a token hides.
    """
    if name == "gitlab":
        return tracker_for("gitlab", url="https://gitlab.com", token="")
    if name == "jira":
        return tracker_for("jira", url=board.split("|", 1)[0], email="", token="", api="auto")
    return tracker_for("github", token=tracker_token)


def run(name: str, board: str, tracker_token: str, sample: int) -> Result:
    result = Result(tracker=name, board=board)
    started = time.time()
    # A Jira board carries its instance in front of the project key; every
    # other tracker has one host and the board is the whole address.
    project = board.split("|", 1)[-1]
    try:
        tracker = make(name, board, tracker_token)
        gathered = by_mining(tracker, project, sample=sample, want=30)
        profile = build(
            gathered.exemplars,
            project=board,
            tracker=name,
            contrast=gathered.contrast,
            limits=gathered.limits,
        )
        openish = tracker.search(TicketQuery(project=project, state="open", limit=10))

        result.ok = True
        result.considered = gathered.considered
        result.exemplars = profile.sample_size
        result.sections = len(profile.sections)
        result.conditionals = len(profile.conditionals)
        result.language = profile.language
        result.median_chars = round(profile.chars_median)
        if gathered.contrast:
            result.shipped = gathered.contrast.shipped
            result.stalled = gathered.contrast.stalled
            result.signals = len(gathered.contrast.signals)
        # Rendered, not stored raw. These became a code and its measurements
        # so they could be shown in either language, and the harness went on
        # printing them - which turned its own report into a column of Python
        # tuples nobody reads.
        result.limits = [render_note(code, params) for code, params in gathered.limits]
        result.anomalies = sniff(result, profile, gathered, openish)
    except TrackerError as exc:
        # A TrackerError is the tool answering, not failing. "This repository
        # moved" and "this one has issues switched off" are both correct
        # answers about a board that cannot be profiled, and counting them as
        # crashes buried the run where something really did go wrong.
        result.refused = str(exc)
    except Exception:
        result.crash = traceback.format_exc(limit=4)
    result.seconds = round(time.time() - started, 1)
    return result


def main() -> int:
    sample = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    which = sys.argv[3].lower() if len(sys.argv) > 3 else "both"
    fleet: list[tuple[str, str]] = []
    if which in ("both", "github"):
        fleet += [("github", b) for b in GITHUB_BOARDS]
    if which in ("both", "gitlab"):
        fleet += [("gitlab", b) for b in GITLAB_BOARDS]
    if which in ("both", "jira"):
        fleet += [("jira", b) for b in JIRA_BOARDS]
    if len(sys.argv) > 2:
        fleet = fleet[: int(sys.argv[2])]
    tracker_token = token()
    out: list[Result] = []

    for i, (name, board) in enumerate(fleet, 1):
        print(f"[{i}/{len(fleet)}] {name}:{board}", file=sys.stderr, flush=True)
        result = run(name, board, tracker_token, sample)

        # A rate limit is not a finding about the board, and carrying on past
        # one turns every remaining row into the same non-result. Wait it out
        # once, retry, and only then give up on the run.
        if "rate limiting" in result.refused:
            print("    rate limited - waiting 60s and retrying once", file=sys.stderr)
            time.sleep(60)
            result = run(name, board, tracker_token, sample)
            if "rate limiting" in result.refused:
                print(
                    "    still limited; stopping here rather than filling the "
                    "report with the same message",
                    file=sys.stderr,
                )
                out.append(result)
                break

        out.append(result)
        if result.crash:
            print(f"    CRASH {result.crash.splitlines()[-1][:110]}", file=sys.stderr)
        elif result.refused:
            print(f"    refused: {result.refused.splitlines()[0][:110]}", file=sys.stderr)
        for limit in result.limits:
            print(f"    limited: {limit[:110]}", file=sys.stderr)
        for a in result.anomalies:
            print(f"    ? {a}", file=sys.stderr)

    Path("fleet.json").write_text(json.dumps([asdict(r) for r in out], indent=2), encoding="utf-8")
    crashed = [r for r in out if r.crash]
    refused = [r for r in out if r.refused]
    odd = [r for r in out if r.anomalies]
    print(
        f"\n{len(out)} boards: {len(out) - len(crashed) - len(refused)} profiled, "
        f"{len(refused)} refused with a reason, {len(crashed)} crashed, "
        f"{len(odd)} with anomalies",
        file=sys.stderr,
    )
    return 1 if crashed else 0


if __name__ == "__main__":
    raise SystemExit(main())
