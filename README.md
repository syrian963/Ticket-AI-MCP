# ticket-ai

Most ticket advice is free and therefore ignored. "Add acceptance criteria",
"include steps to reproduce" — everyone has heard it, nobody changed anything.

This measures your own tracker instead, and says things like:

> **No 'Akzeptanzkriterien' section.**
> 31 of the 40 exemplar tickets (78%) have one.

That is harder to wave away, because it is not an opinion about tickets. It is
a count of what already happened in your project.

Works with GitLab, Jira and GitHub. Runs as an MCP server or a CLI.

## What it does

**Learns your house style.** Give it a handful of tickets you think are good
and it measures those. Give it nothing and it goes looking: it reads your
closed tickets and ranks them by whether a merge request shipped for them,
whether anyone had to reopen them, and how many clarifying questions they drew
before work started. The tickets that scored well become the corpus.

**Measures new tickets against it.** Sections, length, labels, title markers,
language. Every finding cites a count over that corpus.

**Learns from the tickets that failed, not only the ones that worked.** Every
rate it reports can be a comparison: not "78% of tickets have acceptance
criteria" but *"78% of the ones that shipped, and 30% of the ones that
stalled"*. The second is evidence; the first invites a shrug.

The split is on **outcome alone** — a merged change, a reopen, a run of
clarifying questions — and never on what the ticket contains. Splitting on
content and then comparing content would be circular. It also ignores tickets a
staleness bot closed, because those say something about attention rather than
about writing.

On a board where nothing separates the two groups, it says so. That is worth
knowing before anyone is asked to write differently.
[**docs/shipped-against-stalled.md**](docs/shipped-against-stalled.md) has the
guards, and the real board that forced the staleness-bot exclusion.

**Reads the form you declared, not just the tickets you got.** If the repo has
`.github/ISSUE_TEMPLATE` or `.gitlab/issue_templates`, `gaps` lines each field
up against how often tickets actually carry it — and the gap runs both ways. On
`astral-sh/uv` a required field turns up in 10% of tickets, which is a form
asking for something people cannot easily give rather than a discipline
problem. A section most tickets carry that no form mentions is the opposite: a
convention the project grew and never wrote down.

**Gathers what you need to write one.** Give it a subject and it returns the
related past tickets, **the files the merge requests for those tickets actually
changed**, and the files in your checkout that mention it. That middle one
lives only in the tracker's history — no amount of reading the code produces
it, and it is usually the fastest way to find where the work will land.

## Where the "AI" is

Counting cannot produce a paragraph of German, so writing a ticket needs a
model. Everything else — learning the house style, finding related tickets,
measuring a draft — needs nothing, and runs with no key and no network beyond
your tracker.

So the model is opt-in, and which one is your choice:

| | Writes | Needs |
|---|---|---|
| **MCP, in Claude Code** | yes | nothing — the assistant is already a model |
| **`--writer ollama`** | yes | a model on your machine. No key, no account, nothing leaves the laptop |
| **`--writer openai`** | yes | a base URL and a key. OpenRouter, Azure AI Foundry, vLLM, any provider |
| **no writer (default)** | no | nothing. Measures and gathers; you write |

The MCP path is the one to reach for first if you already use Claude Code: ask
for a ticket, and the assistant calls `ticket_template` and `ticket_context`,
reads the files those point at, writes it, and checks it with `review_draft`
before showing you anything.

Whichever model writes, the draft goes through the same measurement as any
other ticket, and the findings go back to it once. That loop is why a small
local model is usable here: it is writing into a shape the tool worked out by
counting, and being marked against your team's own tickets afterwards.

## What it does not do

It does not judge your tickets. Nothing that measures here reads for meaning —
it counts. So it cannot tell you whether your acceptance criteria make sense.
It can tell you that the 40 tickets that shipped in this project all had some
and this one does not.

That is also why a model is optional rather than required, and why the parts
that need no model are the parts that make the tool worth having.

It also does not write to your tracker. Every operation is a read.

## Alignment is not quality

The score is distance from the tickets that historically got built here.
A one-line ticket from someone who knows exactly what they mean can score
badly and be completely fine. The tool says this about itself, and so should
you when you quote it at a colleague.

## Setup

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

The token is optional on a public project — `TICKET_AI_GITLAB_URL=https://gitlab.com`
with no token reads any public board, which is the quickest way to see what the
tool does before pointing it at your own instance.

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

## Using it

```bash
ticket-ai learn                      # mine the tracker, cache the profile
ticket-ai learn --from '#412,#98'    # or name the good ones yourself
ticket-ai style                      # what it learned
ticket-ai context 'export is broken on mobile'   # what already exists
ticket-ai gaps                       # the declared template vs what arrives
ticket-ai draft --title '...' --file draft.md    # check one before creating it
ticket-ai review '#42'             # measure one ticket
ticket-ai open                       # every open ticket, worst first
```

With a model configured, a title is enough:

```bash
export TICKET_AI_WRITER=ollama       # or openai, with a base url and key
ticket-ai models                     # what that endpoint can reach
ticket-ai compose --title 'Etikettendruck bricht bei mehr als zehn Positionen ab'
ticket-ai models --workflow          # an Actions workflow that drafts new issues
```

`compose` writes the body, measures it, hands the findings back to the model
once, and prints the review to stderr so the body alone can be redirected.
`--fail-under` makes it refuse to emit a draft that missed the house style.

[**docs/what-it-produces.md**](docs/what-it-produces.md) prints five tickets it
produced, unedited, with the score each one got.
[**docs/local-models.md**](docs/local-models.md) covers running the writer on a
2 GB local model instead: measured timings, and what that trades away.

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

`learn` takes a minute or two — ranking needs each ticket's comments and linked
merge requests, which is an extra request or two per ticket. It caches to
`TICKET_AI_CACHE_DIR` if you set one, otherwise to
`.ticket-ai/`, so you do it once, not once per review.

`--from` is taken as given: no filtering, no scoring against your choices. If
you name a ticket with a three-word description, that is your answer about how
this team writes tickets and the profile will reflect it.

### A page instead

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

`TICKET_AI_TICKET_LANGUAGE` overrides what the corpus measured, and takes
effect without re-learning. Leave it unset unless the board is mid-switch — a
measurement beats a setting, and forcing a language the board does not use
makes every existing ticket fail the language check.

**Findings are never translated.** They carry counts over a named sample and
get pasted verbatim into tickets; a half-translated sentence with a number in
it is worse than an English one.

### In CI

```bash
ticket-ai review "$CI_ISSUE" --fail-under 0.5
```

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

### Writing a ticket

This is the path to reach for if you already use Claude Code: no key, no
`compose`, no second model call. Ask for a ticket and the assistant does five
things, three of them here:

1. **`ticket_template`** — the shape: which sections, how long, what language,
   which labels.
2. **`ticket_context`** — what exists: related tickets, the files their merge
   requests changed, the files in the checkout that mention it.
3. **It reads those files.** `ticket_context` runs a text search, not an
   analysis; it says where to look, it does not save you looking.
4. **It writes the ticket.**
5. **`review_draft`** — measures what it wrote, and it fixes what that finds
   before showing you anything. Checking after creating puts the review past
   the point of no return.

## How the mining works

Only closed tickets are sampled — an open ticket may be beautifully written,
but nothing about it yet shows anyone could act on it. Each one gets scored on:

| Signal | Weight | Why |
|---|---|---|
| A merged MR is attached | 0.30 | Strongest evidence someone could build it as written |
| Substantial description | 0.20 | A stub teaches nothing about a template |
| Has sections | 0.15 | The template is the thing being learned |
| No clarifying questions | 0.15 | Eleven "what do you mean?" comments means it was not clear |
| Never reopened | 0.10 | Reopened means closed before it was understood |
| Labelled | 0.10 | |

Tickets opened and closed inside an hour are halved — usually duplicates or
typo fixes, and their shape is not the shape of real work. Bot authors are
dropped outright; learning a house style from Renovate is a real failure mode.

No one author can supply more than 40% of the corpus, or the profile ends up
describing your most prolific ticket-writer instead of your team.

Every score carries the reasons that produced it. A corpus you cannot argue
with is one you will not trust.

### Where it is weak

The signals are circumstantial. A well-written ticket closed as out-of-scope
with no MR scores badly here, and that is an acceptable error — the goal is
thirty *representative* tickets, not the thirty best ones.

A thin sample says so, loudly, in the report and in every review built on it.

Jira has no public API for linked branches and merge requests, so the "shipped"
signal there falls back to remote links. Teams relying on smart commits will see
weaker rankings, and `detail` reports nothing rather than guessing — which makes
the ranking on Jira weaker than on GitLab or GitHub. That is a limit of the API,
not of the corpus.

### What running it against real things found

[**docs/thirty-boards.md**](docs/thirty-boards.md) is what happened when this
was driven across every board its three adapters can reach — thirty GitHub
repositories, six GitLab projects and seven Jira projects on five instances —
and then driven the way a person drives it: through the MCP tools over the
protocol, the CLI, a browser, and a clean install of the built wheel.

A repository that had moved, one with issues switched off, a staleness bot
inverting a comparison, a display cap that was doing the selecting, a Jira
behind a company hostname, 349 tickets reported as never having shipped, every
error message being swallowed before it reached the caller, and a release gate
that could only pass on the machine it was written on.

Almost all of it had full line coverage at the time. That is the point of the
page.

### Verified against

Run end to end, read-only, against: `home-assistant/core`, `pydantic/pydantic`,
`astral-sh/uv`, `fastapi/fastapi` (GitHub), `hibernate.atlassian.net/HHH` (Jira
Cloud), `issues.apache.org/jira` KAFKA (Jira Data Center), and one private
GitLab board.

Home Assistant produces no conditional rules at all, which is the right answer:
their issue form is mandatory, so every section already clears the board-wide
threshold and a conditional has nothing to add.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

## License

MIT.
