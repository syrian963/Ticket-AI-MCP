# ticket-ai

![release](https://img.shields.io/badge/release-v0.1.1-1f6feb?style=for-the-badge&labelColor=22272e)
![MCP tools](https://img.shields.io/badge/MCP%20tools-8-8957e5?style=for-the-badge&labelColor=22272e)
![CLI commands](https://img.shields.io/badge/CLI%20commands-11-8957e5?style=for-the-badge&labelColor=22272e)
![trackers](https://img.shields.io/badge/trackers-GitLab%20%7C%20Jira%20%7C%20GitHub-8957e5?style=for-the-badge&labelColor=22272e)

![tests](https://img.shields.io/badge/tests-473-238636?style=for-the-badge&labelColor=22272e)
![coverage](https://img.shields.io/badge/coverage-93%25-238636?style=for-the-badge&labelColor=22272e)
![python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-484f58?style=for-the-badge&labelColor=22272e&logo=python&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-484f58?style=for-the-badge&labelColor=22272e)

**An MCP server and CLI that measures how your team actually writes tickets, and
holds new ones to that.** Not *this ticket is bad* — *31 of the 40 tickets that
shipped here have an acceptance-criteria section, and this one does not.*

> **No 'Akzeptanzkriterien' section.**
> 31 of the 40 exemplar tickets (78%) have one.

Most ticket advice is free and therefore ignored. "Add acceptance criteria",
"include steps to reproduce" — everyone has heard it, nobody changed anything.
A count of what already happened in your own project is harder to wave away,
and it is not an opinion about tickets.

**There is no model in the loop for any of that.** Learning the house style,
finding related tickets and measuring a draft are counting, and run with no key
and no network beyond your tracker. Writing a ticket needs a model, so that
part is opt-in and which one is your choice.
**[What it found when it was run against real boards →](docs/thirty-boards.md)**

## Install

```bash
uv tool install ticket-ai-mcp
```

Configuration is environment variables only — a token passed as `--token` ends
up in your shell history and in the process list.

```bash
export TICKET_AI_TRACKER=gitlab
export TICKET_AI_PROJECT=acme/shop        # or the numeric id
export TICKET_AI_GITLAB_URL=https://gitlab.example.com
export TICKET_AI_GITLAB_TOKEN=...         # read_api scope is enough
```

The token is optional on a public project. `TICKET_AI_GITLAB_URL=https://gitlab.com`
with no token reads any public board, which is the quickest way to see what
this does before pointing it at your own instance.

<details>
<summary>Jira and GitHub</summary>

```bash
# Jira — Cloud or self-hosted Server / Data Center. Which one you are on is
# detected from the instance; you do not have to say.
export TICKET_AI_TRACKER=jira
export TICKET_AI_PROJECT=PROJ
export TICKET_AI_JIRA_URL=https://acme.atlassian.net

# Leave the credentials unset for a public board — plenty answer without any.
# Cloud: the token comes from id.atlassian.com, and is not the password.
export TICKET_AI_JIRA_EMAIL=you@example.com
export TICKET_AI_JIRA_TOKEN=...
# Self-hosted: a personal access token on its own, sent as a Bearer.
export TICKET_AI_JIRA_TOKEN=...
# Only if detection gets it wrong: cloud | server | auto (the default)
export TICKET_AI_JIRA_API=server

# GitHub
export TICKET_AI_TRACKER=github
export TICKET_AI_PROJECT=acme/shop
export TICKET_AI_GITHUB_TOKEN=...
```

</details>

mcp-name: io.github.syrian963/ticket-ai-mcp

## One command

```bash
ticket-ai learn      # mine the tracker, cache the profile
ticket-ai style      # what it learned
```

`learn` takes a minute or two: ranking needs each ticket's comments and linked
merge requests, which is an extra request or two per ticket. It caches to
`TICKET_AI_CACHE_DIR` if you set one and to `.ticket-ai/` otherwise, so it
happens once rather than once per review.

Then the rest:

```bash
ticket-ai learn --from '#412,#98'                  # or name the good ones yourself
ticket-ai context 'export is broken on mobile'     # what already exists
ticket-ai gaps                                     # declared template vs what arrives
ticket-ai draft --title '...' --file draft.md      # check one before creating it
ticket-ai review '#42'                             # measure one that exists
ticket-ai open                                     # every open ticket, worst first
```

`draft` is the one worth building a habit around. Checking a ticket after you
create it puts the review past the point of no return: the board has already
been notified and every fix is now an edit with a history.

```
$ ticket-ai draft --title 'Filter kaputt' --file draft.md
Alignment with house style: 33% over 3 checks

### MEDIUM - The description is 116 characters.
The shortest quarter of tickets that shipped here start at 639; the median is 1079.

### MEDIUM - The ticket has no labels.
62% of the exemplars are labelled.
```

`--from` is taken as given: no filtering, no scoring against your choices. Name
a ticket with a three-word description and that is your answer about how this
team writes tickets, and the profile will say so.

## As an MCP server

```json
{
  "mcpServers": {
    "ticket-ai": {
      "command": "uvx",
      "args": ["ticket-ai-mcp"],
      "env": {
        "TICKET_AI_TRACKER": "gitlab",
        "TICKET_AI_PROJECT": "acme/shop",
        "TICKET_AI_GITLAB_URL": "https://gitlab.example.com",
        "TICKET_AI_GITLAB_TOKEN": "..."
      }
    }
  }
}
```

Eight tools, all read-only: `learn_conventions`, `house_style`,
`ticket_template`, `ticket_context`, `template_gaps`, `review_draft`,
`review_ticket`, `review_open_tickets`.

Add `TICKET_AI_REPO` to the `env` block if the checkout you want searched is
not the assistant's working directory.

### Writing a ticket this way

The path to reach for if you already use Claude Code: no key, no `compose`, no
second model call. Ask for a ticket and the assistant does five things, three
of them here:

1. **`ticket_template`** — the shape: which sections, how long, what language,
   which labels.
2. **`ticket_context`** — what exists: related tickets, the files their merge
   requests changed, the files in the checkout that mention it.
3. **It reads those files.** `ticket_context` runs a text search, not an
   analysis; it says where to look, it does not save you looking.
4. **It writes the ticket.**
5. **`review_draft`** — measures what it wrote, and fixes what that finds
   before showing you anything.

## Where the "AI" is

Counting cannot produce a paragraph of German, so writing a ticket needs a
model. Everything else needs nothing.

| | Writes | Needs |
|---|---|---|
| **MCP, in Claude Code** | yes | nothing — the assistant is already a model |
| **`--writer ollama`** | yes | a model on your machine. No key, no account, nothing leaves the laptop |
| **`--writer openai`** | yes | a base URL and a key. OpenRouter, Azure AI Foundry, vLLM, any provider |
| **no writer (default)** | no | nothing. Measures and gathers; you write |

```bash
export TICKET_AI_WRITER=ollama       # or openai, with a base url and key
ticket-ai models                     # what that endpoint can reach
ticket-ai compose --title 'Etikettendruck bricht bei mehr als zehn Positionen ab'
ticket-ai models --workflow          # an Actions workflow that drafts new issues
```

`compose` writes the body, measures it, hands the findings back to the model
once, and prints the review to stderr so the body alone can be redirected.
`--fail-under` makes it refuse to emit a draft that missed the house style.

Whichever model writes, the draft goes through the same measurement as any
other ticket. That loop is why a small local model is usable here: it writes
into a shape worked out by counting, and is marked against your team's own
tickets afterwards.

## A page instead

```bash
ticket-ai ui --lang de     # or en
```

A local page on `127.0.0.1:8760` with three tabs: the house style, a box to
paste a draft into, and the open backlog worst-first. Loopback only, because
this process holds a tracker token, and there is no flag to change that.

No build step and no CDN — one HTML file with its CSS and JavaScript inline.

### Two languages, and they are separate

```bash
export TICKET_AI_UI_LANGUAGE=de       # buttons and headings
export TICKET_AI_TICKET_LANGUAGE=de   # what it says to write tickets in
```

The distinction is easy to collapse and worth keeping. A German team may want
the tool's own buttons in English; someone joining a German board still has to
write the ticket in German.

`TICKET_AI_TICKET_LANGUAGE` overrides what the corpus measured and takes effect
without re-learning. Leave it unset unless the board is mid-switch — a
measurement beats a setting, and forcing a language the board does not use
makes every existing ticket fail the language check.

Findings, the caveats under them and the list of what a ticket already got
right are all rendered in the reader's language at the moment they are shown.
The section names inside them are not: those are the team's own headings, and
translating one turns it into a section the team does not have.

### In CI

```bash
ticket-ai review "$CI_ISSUE" --fail-under 0.5
```

## What it measures

**The house style.** Sections, length, labels, title markers, language — over a
corpus it either took from you or found itself. Every finding cites a count
over that corpus.

**The tickets that failed, not only the ones that worked.** A rate can be a
comparison: not "78% of tickets have acceptance criteria" but *"78% of the ones
that shipped, and 30% of the ones that stalled"*. The second is evidence; the
first invites a shrug.

The split is on **outcome alone** — a merged change, a reopen, a run of
clarifying questions — and never on what the ticket contains, because splitting
on content and then comparing content would be circular. Tickets a staleness
bot closed are left out of both groups: those say something about attention
rather than about writing. On a board where nothing separates the two, it says
so, which is worth knowing before anyone is asked to write differently.
**[docs/shipped-against-stalled.md](docs/shipped-against-stalled.md)** has the
guards and the real board that forced the bot exclusion.

**Sections that only travel together.** A section carried by 41% of tickets is
under any threshold worth having — but if it is on 76% of the tickets that also
have a *Ziel* section and 20% of the ones that do not, the team has a template
and applies it to one kind of work. A board-wide rate hides that entirely.

**The form you declared, against the tickets you got.** With a
`.github/ISSUE_TEMPLATE` or `.gitlab/issue_templates` in the checkout, `gaps`
lines each field up against how often tickets actually carry it — and the gap
runs both ways. A required field that turns up in 10% of tickets is a form
asking for something people cannot easily give. A section most tickets carry
that no form mentions is a convention the project grew and never wrote down.

**What you need to write one.** Give it a subject and it returns the related
past tickets, **the files the merge requests for those tickets actually
changed**, and the files in your checkout that mention it. That middle one
lives only in the tracker's history — no amount of reading the code produces
it, and it is usually the fastest way to find where the work will land.

## How the mining works

Only closed tickets are sampled: an open ticket may be beautifully written, but
nothing about it yet shows anyone could act on it. Each is scored on

| Signal | Weight | Why |
|---|---|---|
| A merged MR is attached | 0.30 | Strongest evidence someone could build it as written |
| Substantial description | 0.20 | A stub teaches nothing about a template |
| Has sections | 0.15 | The template is the thing being learned |
| No clarifying questions | 0.15 | Eleven "what do you mean?" comments means it was not clear |
| Never reopened | 0.10 | Reopened means closed before it was understood |
| Labelled | 0.10 | |

Tickets opened and closed inside an hour are halved — usually duplicates or
typo fixes, whose shape is not the shape of real work. Bot authors are dropped
outright; learning a house style from Renovate is a real failure mode. No one
author may supply more than 40% of the corpus, or the profile describes your
most prolific ticket-writer instead of your team.

Every score carries the reasons that produced it. A corpus you cannot argue
with is one you will not trust.

## What it will not tell you

**Whether your tickets are any good.** Nothing here reads for meaning; it
counts. It cannot tell you whether your acceptance criteria make sense. It can
tell you that the 40 tickets that shipped in this project all had some and this
one does not.

**Alignment is not quality.** The score is distance from the tickets that
historically got built here. A one-line ticket from someone who knows exactly
what they mean can score badly and be completely fine. The tool says this about
itself, and so should you when you quote it at a colleague.

**Nothing about your tracker's contents changes.** Every operation is a read.

The ranking signals are also circumstantial. A well-written ticket closed as
out-of-scope with no MR scores badly, and that is an acceptable error: the goal
is thirty *representative* tickets, not the thirty best ones. A thin sample
says so, loudly, in the report and in every review built on it.

Jira has no public API for linked branches and merge requests, so the "shipped"
signal there falls back to remote links, and a board that does not post them
cannot be split into shipped and stalled at all. It says that rather than
reporting every ticket as stalled. That is a limit of the API, not of the
corpus.

## Measuring the part a model writes

Every test in this repository mocks the writer, so none of them says anything
about the prose that comes back. `evals/` is the answer to that: a frozen set of
titles from public boards, each stored next to the board profile as it stood
when the case was collected.

```bash
ticket-ai eval --baseline evals/baseline.json          # run it and mark the result
ticket-ai eval --runs evals/results/latest.jsonl       # re-score records, no model
ticket-ai eval --repeats 5 --markdown                  # spread, as a table for an MR
```

**The cases were held out of the profile before it was built.** A case the
profile was fitted to flatters the model for a reason that has nothing to do
with the model.

**The runner records and does not score.** A case costs thirty to seventy
seconds on a local model, so scoring separately is what makes a changed metric
free to try. `--runs` is that path.

Two of the figures are not in `review_draft`, deliberately. It asks whether the
sections a board uses are present; it never asks **what else the draft
invented**, because people rarely add a heading their board has never seen and
models do it constantly. The other is spread: one number per case reads as
precision a non-deterministic writer cannot support, so `--repeats` exists and
the report prints the deviation next to the mean.

### The judge, and why its number never travels alone

Counting cannot say whether the prose is about the title, and that is the
failure worth catching: a draft can carry every section, hit the length, take
the right labels and describe something else entirely. `review_draft` gives it
full marks.

`--judge` asks a model that one question and nothing else. Not "is this a good
ticket" - that is the question this tool exists to replace with counts.

```bash
ticket-ai eval --judge --labels evals/labels.jsonl
python tools/label_drafts.py evals/results/latest.jsonl   # collect the labels
```

**Raw agreement is close to useless on its own.** If nine drafts in ten are on
topic, a judge that answers "on topic" every time agrees ninety percent of the
time and has learned nothing. Cohen's kappa subtracts the agreement two people
guessing at those rates would reach, so that judge scores zero.

The labels come from a person and there is no way around it. A calibration
whose human half was generated compares one model to another. Without
`--labels`, the judge line prints `uncalibrated` every time rather than
documenting the caveat once and letting the number travel without it.

The dataset is 85 cases over six public boards. **Thirty of them are German**,
from `kern-ux/pattern-library` and `fitko/fim/portal` on gitlab.opencode.de, a
public German GitLab that reads without a token. That matters because `compose`
has a whole German branch in its system prompt and nothing measured it before.
kern-ux is why `jsonl_lines` exists: one of its
tickets carries a literal U+2028, `str.splitlines` treats that as a line break
and `json.dumps` does not escape it, so a valid file read as a truncated record
and the error blamed the file.

[**evals/README.md**](evals/README.md) has the rules about what may become a
case. Public boards only.

### Where it runs

The cheap half runs on every pull request that touches the harness: every board
loads, no case is also an exemplar, and a committed results file is re-scored.
No model, so it costs seconds.

The suite itself is `workflow_dispatch`. Composing 55 cases is minutes and
money, and paying for it on a branch about a README typo buys nothing.

`terraform/` is the report's address: an S3 bucket behind CloudFront, and an
IAM role GitHub Actions assumes with a short-lived OIDC token. **There is no
AWS key in the repository secrets** to leak or rotate. The trust policy is
scoped to one repository; `repo:owner/*` would hand the role to every
repository that owner ever creates. Everything works without any of it, and the
publish step skips itself when the role variable is unset.

## Documentation

| | |
|---|---|
| [**thirty-boards.md**](docs/thirty-boards.md) | What running it against forty-three real boards, an MCP client, a browser and a clean install found. Almost all of it had full line coverage at the time |
| [**what-it-produces.md**](docs/what-it-produces.md) | Five tickets it wrote, unedited, with the score each one got |
| [**shipped-against-stalled.md**](docs/shipped-against-stalled.md) | How the two groups are split, and the guards that keep the comparison honest |
| [**local-models.md**](docs/local-models.md) | Running the writer on a 2 GB local model: measured timings, and what it trades away |

### Verified against

Run end to end, read-only, against `home-assistant/core`, `pydantic/pydantic`,
`astral-sh/uv`, `fastapi/fastapi` (GitHub), `inkscape/inkscape` and
`gitlab-org/gitlab-runner` (GitLab), `hibernate.atlassian.net` HHH (Jira Cloud),
`issues.apache.org/jira` KAFKA (Jira Data Center), and one private GitLab board.

Home Assistant produces no conditional rules at all, which is the right answer:
their issue form is mandatory, so every section already clears the board-wide
threshold and a conditional has nothing to add.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
bash install_check.sh          # build the wheel and drive it from a clean venv
uv run python tests/fleet.py   # the whole tool across 43 public boards
```

## License

MIT.
