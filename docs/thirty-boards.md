# What running it against real things found

Every serious defect in this project was found by pointing it at something
real. None of them were found by reasoning about it, and most of them had full
line coverage at the time.

Two kinds of run. `tests/fleet.py` drives the whole tool across **forty-three
public boards** — thirty GitHub repositories, six GitLab projects and seven
Jira projects across five instances — recording what crashed, what the tool
declined to do and why, what the tracker would not serve, what ran but looks
wrong, and the shape of every answer. It is not part of the suite: it needs a
token and a network, and a test that needs either fails for whoever forks this.
The rest is this tool being driven the way a person drives it — through the MCP
tools, the CLI, a browser, a clean install — and read.

Where it stands after all of it: **41 boards profiled, 2 refused with a reason,
0 crashed** — the same on each of the last five sweeps. The two refusals are
the repository with issues switched off and the one that moved, which are the
right answers.

| what it was pointed at | what came back |
|---|---|
| 43 boards, repeatedly | a repository that had moved, one with issues switched off, a staleness bot inverting a comparison, a display cap doing the selecting |
| the MCP tools, over the protocol | every error message being swallowed before it reached the caller |
| six public GitLab projects | an adapter that would not start without a token, and a corpus of forty tickets read as zero |
| seven public Jira projects | 349 tickets reported as never having shipped, and a Jira behind a company hostname |
| five real issue templates | every required field on the biggest form read as optional |
| three real checkouts | the file named after the subject ranked fourteenth |
| 120 real tickets, read | the "related tickets" ranked by length |
| the page, in a browser | four English strings on a German page |
| a clean install | a release gate that could only pass on one machine |

The sections below are in the order they were found, which is also roughly the
order from "does it work on somebody else's board" to "does it work for
somebody else at all".

## The first run

One crash, five anomalies, twenty-nine boards profiled.

### A repository that had moved

`tiangolo/sqlmodel` answered **301 Moved Permanently**, with a body naming its
new location. The code saw an object where it wanted a list and said `expected
a list, got dict` — which points at parsing when the fix is one environment
variable. It now reads the redirect and says so.

### A repository with issues turned off

`encode/httpx` has `has_issues: false`, so its issues endpoint serves nothing
but pull requests. Two hundred rows came back, every one filtered out, and the
tool returned an empty corpus in silence. Silence was the bug: it sent the
reader off to wonder why every rate was zero. It now refuses and names the
likely cause.

### A staleness bot inverting a comparison

`home-assistant/core` reported that **every field of its mandatory issue form
was more common among the tickets that stalled**, which reads as "filling in
the form makes tickets fail". Their bot closes anything quiet for long enough,
so the stalled group had filled with well-written tickets whose only fault was
age. Bot closures now land in neither group — see
[shipped-against-stalled.md](shipped-against-stalled.md).

### A rate limit reported as a permission error

Thirty boards is a few thousand API calls, and the run ended on a 403 that
looked exactly like a bad token. A limit is a wait, not a fault; the message
now says which it is, and how long the window has left.

## The finding that needed arithmetic, not a stack trace

Sixteen of the thirty boards came back with **exactly twelve** conditional
rules. Twelve was the display cap. A cap that binds on half the sample is not
trimming a long tail — it is doing the selecting.

The cause is a multiple-comparisons problem hiding in plain sight. Every
ordered pair of sections is a hypothesis, so a board with fourteen sections
tests around 180 of them, and keeping whichever clear a fixed threshold is the
oldest mistake in statistics.

Simulated against random data, a pair with **no association at all** cleared a
flat 0.25 lift 8% of the time. On a wide board that is fourteen invented rules.

Two changes:

- The minimum trigger went from five tickets to eight. At five, one ticket
  moves a conditional rate by twenty points, and that is where the invented
  rules came from — sections carried by half the board never produced them.
- The required gap now rises with the number of pairs examined. On a board with
  two sections a 0.25 gap is interesting; on a board with twenty it is noise.

Measured on random data with sixteen low-prevalence sections: **3.5 invented
rules per board before, none after.**

The regression test took three attempts to write honestly. The first two passed
with the old thresholds as well, which meant they were testing nothing — the
fixture used sections common enough to have low variance, and the phenomenon
only appears in small trigger groups.

## What that first run said about the tool

Across the twenty-nine boards that profiled cleanly, all of them GitHub. The
current numbers, over all three trackers, are further down under
[All three fleets, one run](#all-three-fleets-one-run):

| | |
|---|---|
| found a template (at least one recurring section) | 97% |
| guessed the language | 97% |
| kept a full corpus of thirty | 97% |
| could compare shipped against stalled | 86% |
| found something that separated the two groups | 72% |

The last row is the honest one. On roughly a quarter of boards nothing about
how a ticket was written predicted whether it got built, and the tool says so
rather than manufacturing advice.

## The later runs

Two more rounds, after the thresholds changed. No crashes on either, and the
harness learned to stop calling correct answers failures.

### A refusal is not a crash

`encode/httpx` has issues switched off and `tiangolo/sqlmodel` has moved. Both
answers are right, and both were being counted in the crash column - which
meant the one number worth watching was never zero and nobody could tell when
it should have been. Refusals now have their own column, and the run reports
"15 profiled, 3 refused with a reason, 0 crashed".

### Twelve, again

Five of fifteen boards reported exactly twelve conditional rules, which is the
same shape as the finding that started the threshold work: twelve was a hard
truncation. Measuring what the truncation was cutting turned up two separate
mistakes, and neither was "the cap is too small".

On `rollup/rollup` thirty rules cleared the threshold and the cut landed in the
middle of a tie — eighteen dropped, the best of them with **exactly the lift of
the weakest kept**. There is no ranking inside a tie, so which twelve survived
was arbitrary.

And those thirty rules were every ordered pair of six sections: a board whose
template is one rigid block. A block is found by pairs that hold in *both*
directions, so truncating the list first handed the cluster finder a graph with
half its edges missing, and one block came out as a dozen loose rules.

Truncation is a display concern, and it now lives in the report. Live, after
the change:

| board | rules | blocks found | loose rules printed |
|---|---|---|---|
| rollup/rollup | 30 | one block of 6 | 0 |
| sqlalchemy/alembic | 21 | blocks of 3 and 4 | 0 |

The same fix made a reviewer stop stuttering. A ticket missing one section on a
board where five others imply it collected five findings asking for the same
paragraph; it now gets one, from the rule with the best evidence behind it.

## What GitLab found in a minute

Until this point the harness drove one adapter of three. The GitLab one had
unit tests against recorded response shapes, which is a different thing from
having met a live instance, and pointing it at six public projects on
gitlab.com found two defects immediately.

### It would not start without a token

A public GitLab project answers the issues API to anyone, exactly as a public
Jira does - and the adapter refused to be built without a personal access
token, which shut the tool out of the open-source boards it is most useful to
learn from. The token is now optional. What anonymity costs is a worse 404:
GitLab hides a project it will not show you rather than refusing it, so the
message now offers both readings.

### Forty tickets listed, none read

gitlab.com serves a public project's issues to anyone and answers **401 on that
same issue's comments**. Treating that as fatal meant every ticket failed, and
the tool then profiled the empty result: a label rate of 0.00, no sections, no
language. That is not an error message, it is a description of a team - and a
wrong one.

Two changes, because there were two bugs stacked on each other:

- A sub-resource that will not be served is one the ranking does without, the
  way a missing `resource_state_events` already was.
- A corpus where every ticket listed and none could be read is now **refused**
  with the first reason. One failure repeated is not a quiet project.

### Doing without, out loud

With those fixed, all six boards profiled - on the merge requests and
descriptions GitLab does serve anonymously, and with no comments at all. A
corpus with no comments cannot tell a ticket that stalled from one a bot
closed, which is the exact mistake `home-assistant/core` taught this project
the first time.

Degrading is right. Degrading quietly is not, so a profile now ends with what
it was not allowed to read and which of its own numbers are weaker for it:

> https://gitlab.com did not serve comments, reopen history - reading without a
> token. Rankings and the shipped-against-stalled comparison are weaker without
> them.

| board | kept | sections | shipped/stalled | signals | median chars |
|---|---|---|---|---|---|
| inkscape/inkscape | 30 of 50 | 8 | 36/14 | 2 | 1061 |
| gitlab-org/gitlab-runner | 30 of 50 | 22 | 33/17 | 5 | 1744 |
| gitlab-org/cli | 30 of 50 | 17 | 34/15 | 1 | 1558 |
| fdroid/fdroidclient | 30 of 50 | 4 | 10/40 | 1 | 711 |
| veloren/veloren | 30 of 50 | 5 | 29/21 | 1 | 446 |
| libeigen/eigen | 30 of 50 | 12 | 47/3 | 0 | 1566 |

## What Jira found

The third adapter, and the last one the harness had never driven. Seven public
projects across five instances, read without credentials: Hibernate on Cloud
(REST v3, descriptions as an ADF tree), Apache, MongoDB and MariaDB on
self-hosted Server or Data Center (REST v2, wiki markup), and Red Hat's, which
is a Cloud site behind a company hostname. Nothing crashed. Three things were
wrong anyway.

### A Jira that lives somewhere other than where it answers

`issues.redhat.com` is a vanity hostname; Atlassian answers **301** to
`redhat.atlassian.net`. Every browser follows that, so it is the URL people
paste. The client did not: detection read the 301 as a successful answer,
failed to find JSON in it, fell back to guessing the product from the hostname,
guessed Server because the vanity name is not `atlassian.net`, and then asked a
Cloud instance for a Server endpoint. What the user saw was *"answered with
something that is not JSON"* — a sentence about their Jira version.

Anonymously it now follows the redirect and reads the right dialect. With a
credential it **refuses** and names the real URL instead: a token belongs to
the host it was issued for, and forwarding it wherever a redirect points is how
a credential ends up somewhere its owner did not choose.

### Three hundred and forty-nine tickets, none of which shipped

Every board came back **0 shipped, 50 stalled**. Seven independent teams do not
all have that record; the classifier does. It reads "a merged change exists" as
the evidence a ticket got built, and Jira has no supported API for the
development panel — only remote links, which do not say what happened to what
they point at.

The group floor stopped any advice being derived from it, so nothing false was
recommended. But the counts were printed, and telling a team that none of its
fifty closed tickets got built is a false statement about their board.

A corpus with no merged change anywhere now says so and compares nothing. The
second measurement is what made the guard right: Apache's KAFKA board has a
remote link on **32 of 33** tickets and Jira reports the merge state of none of
them, so a guard that asked whether links exist would have passed and left the
split as wrong as it was.

### Two tickets out of fifty, and no reason given

Hibernate's HSEARCH board returned a corpus of two. The other forty-eight are
dependency bumps with no description at all — the exclusion is right, and from
the outside it is indistinguishable from a filter that is too strict. The
reasons were being collected and thrown away.

Reports now end with what was left out and why, and the report that needed it
most — an empty corpus, which returned before the reasons were reached — says
*"30 of 30: description is under 80 characters"* instead of *"nothing could be
measured"*.

### What was checked and turned out to be true

Three boards reported **no recurring heading at all**, which is the shape a
broken markup converter takes: this is the project whose ADF renderer once
reported a code-block rate of 0% on a board full of stack traces. Checked
against the raw API on KAFKA — one of twenty-five descriptions carries a
`h2.`-style heading, and the converter finds four, because it also recognises
bold-line and colon-line headings. The converter is fine. Those teams write
prose, and the tool says so.

## All three fleets, one run

Forty-three boards through one harness, after everything above: **41 profiled,
2 refused with a reason, 0 crashed.** The two refusals are the repository with
issues switched off and the one that moved — both correct answers.

| | all 41 | GitHub only |
|---|---|---|
| guessed the language | 100% | 100% |
| kept a full corpus of thirty | 98% | 100% |
| found a template (at least one recurring section) | 93% | 96% |
| could compare shipped against stalled | 83% | 100% |
| found something that separated the two groups | 61% | 75% |

The two lower rows are the honest ones, and the gap between the columns is the
whole reason to read them separately: every board that *cannot* be compared is
a Jira board, because Jira does not report whether a linked change was merged.
That is a limit of the API, and it is now stated instead of being rendered as
a team that never ships anything.

The three anomalies left are all true. Two Apache boards and one MariaDB board
have no recurring heading — checked against the raw API, they write prose — and
Hibernate's HSEARCH keeps two tickets out of sixty because the other 58 are
dependency bumps with no description, which the report now says out loud.

### The widest board

`BurntSushi/ripgrep` produces **fifty-six** conditional rules, which would have
been alarming under the old cap of twelve and is not. Eight of its sections
imply each other in every direction; the report prints that as one line — *these
appear as a block* — and no loose rules at all, and a ticket reviewed against it
collects at most one finding. Keeping the graph whole made the output smaller.

## What driving the MCP tools found

The fleet drives the library. It does not touch the eight tool functions an
assistant actually calls, and those have only ever run against a fake tracker
in the unit tests. Calling all eight against a live GitLab board, in the order
a conversation would reach for them, found two defects in the first run.

### A section nobody can see

`house_style` reported that 30% of Inkscape's tickets carry a section called
**`<!-- Example file`**. Their issue template ends a comment with a colon on
its own line, and a colon on its own line is one of the four heading shapes
this tool recognises — so the form's own instructions were being counted as the
team's convention.

An HTML comment is invisible in the rendered ticket. It is what the form said,
not what the reporter wrote, and it was being measured twice over: as a
section, and as length. Comments now come out before anything is counted —
except inside a fence, where a ticket showing HTML is showing HTML.

Measured across six boards, thirty tickets each:

| board | tickets carrying a comment | invented sections | median comment characters |
|---|---|---|---|
| inkscape/inkscape | 24/30 | 8 | 437 |
| gitlab-org/gitlab-runner | 3/30 | 7 | 0 |
| home-assistant/core | 0/30 | 0 | 0 |
| pydantic/pydantic | 1/30 | 0 | 0 |
| vitejs/vite | 0/30 | 0 | 0 |
| neovim/neovim | 0/30 | 0 | 0 |

The split is mostly not luck: GitHub's issue *forms* collect answers into
fields, so nothing of the form survives into the body, while GitLab's markdown
templates leave their instructions in the ticket. Mostly, though, and not
always — `jqlang/jq` is a GitHub repository with a markdown template and
carries comments on 9 of 30 tickets. What decides it is the template format,
not the host.

It was distorting the length target handed to a model and the p25 line below
which a ticket is called too short — on Inkscape, by 437 characters the
reporter never typed.

The numbers above come from stripping comments out of *the same* corpus, not
from comparing two runs. A sweep a day apart samples different tickets, and
the differences that shows are mostly the board moving on: `libeigen/eigen`
came back 413 characters shorter between two runs and carries a comment on 3
tickets in 30, so almost none of that was this fix.

### A message pointing at two directories that cannot exist

`template_gaps` on a project with no template said it had looked in
`github/ISSUE_TEMPLATE` and `gitlab/issue_templates`. Both are missing their
leading dot. The paths were written out by hand in the report next to the real
list the search uses, and the copy was wrong — in the one message whose entire
job is to say where to put the file. It reads from the search list now.

## What five real issue templates found

`templates.py` parses GitHub issue forms without a YAML library, on the
argument that the subset it needs is four keys wide. That argument had only
ever been checked against fixtures written by the same person who wrote the
parser, which is not a check. Pointed at five projects' live templates, it got
two of them wrong.

### Every required field on the biggest form read as optional

GitHub issue forms allow the keys of a list item in any order, and this scan
assumed one:

    - type: textarea
      validations:
        required: true
      attributes:
        label: The problem

That is home-assistant/core, verbatim — `validations` before `attributes`. The
parser only counted a `required` that came *after* its label, so every
mandatory field on the form this project's own documentation cites as
mandatory came back optional. `pypa/pip` and `vitejs/vite` write the other
order, which is why nothing looked wrong. The scan now tracks the list item
rather than the order of the keys inside it.

### A repository with two templates reported as having none

`denoland/deno`'s markdown templates are front matter and a single line, with
no headings in them. A file that yielded no fields was not recorded, so the
report said *no issue template found in the checkout* about a repository
carrying two. A template that declares nothing is a finding of its own — and a
different one, with a different fix.

### A field required by a form those tickets never used

Working correctly, and worth a sentence anyway. `home-assistant/core` has a bug
form and a task form; the task form requires a `Description` that no bug ticket
carries, and the report said *required by the form and appears in 0% of
tickets*. True, and unreadable. Gaps now name the file they came from — *required
by `task.yml`* — which is the whole explanation in one word.

## What three real checkouts found

`ticket_context` points at the files a subject is probably about, and the
search behind it had only ever run against this repository and a fixture tree.
Pointed at clones of Flask, fd and Vite it was fast — 0.16s across a 43MB
monorepo — and wrong in two ways that only show up at that size.

### The one file named after the subject came fourteenth

Asked about a **session** cookie on Flask, `src/flask/sessions.py` scored 4.0 —
tied with `docs/templating.rst`, `docs/quickstart.rst` and eleven others, and
losing the tie alphabetically. The name bonus is worth four times a body match
and is the whole idea of the module, and it never fired: the subject says
`session`, the file says `sessions`.

One trailing `s`, and only with four characters of stem left, so `bus` does not
match `bu`. Not a stemmer — every rule added to that trade costs real hits for
wrong ones. Live, on the same checkouts:

| subject | before | after |
|---|---|---|
| session cookie is not set | `sessions.py` 4.0, not in the top ten | `sessions.py` 8.0, first |
| blueprint url_prefix ignored | `blueprints.py` 6.0, sixth | `blueprints.py` 10.0, first |

### A changelog answers every question

`CHANGES.rst` came back for **five of six unrelated subjects** on Flask, and
`CHANGELOG.md` for five of six on fd. Not because it was relevant five times: a
file that records every change mentions every word the project has ever used,
so it scores on anything.

Frequency alone is not the test — `src/cli.rs` also came back five times out of
six on fd, and for a command-line tool that is correct. What makes a changelog
different is that it is a record rather than a place work lands. Files named
like one are halved, not excluded: *the changelog is missing an entry* is a
real ticket, and a name match still carries it. After the change, Flask's
dropped out of the top eight entirely and fd's fell to three of six.

That regression test took two attempts. The first gave the source file a
matching *name*, which is worth four times a body match — so it won with the
weighting and without it, and proved nothing. The case that needed testing is
the one where both files match the same words in their contents.

## What "related tickets" were actually related to

`similar.py` has had full line coverage from the start, and had never been
asked whether its answers are any good. Coverage says the code runs; it says
nothing about whether the five tickets it hands an assistant are the five a
person would have picked.

The check: 120 closed tickets from `home-assistant/core`, six unrelated
subjects, read the results.

### The ranking was being done by length

The top match was **longer than 83% of the pool on average**, and longer than
88% of it for four of the six subjects. One ticket came back first for two
subjects that share no vocabulary.

The code had a comment defending exactly this: *normalised by the subject, not
by the ticket — a long ticket that happens to contain every word is a better
match, not a worse one, and dividing by its length would punish it for being
thorough*. It reads well and the measurement disagrees with it. A ticket long
enough to contain every word contains them by accident, which is the same
pathology as a changelog in the file search and has the same standard answer.

With a BM25-style length term the average drops to **56%** — about what you
would expect if length were not deciding — and the solar query starts
returning the two solar integrations instead of a thermostat.

### The connective tissue of every bug report

`after`, `before`, `again`, `still`, `always`, `during`, `while` were being
scored as evidence. They are in every bug report ever written, which is the
same reason `when`, `then` and `should` were already dropped. Adding them
lowered the spurious scores enough for the relative floor to cut the weakest
results rather than print them under a heading that says *related*.

### And one suspicion that did not survive being checked

After both changes, one ticket was still coming back first for a subject it
looked unrelated to: *todo.item_added trigger stops firing after the list's
config entry is reloaded*, returned for *MQTT sensor keeps its old state after
a restart*. Reading it settles it — it is about an entity whose state survives
a reload while its trigger does not, which is adjacent to what was asked. The
tool's answer was defensible and the objection was not, so nothing was changed
for it.

## What the install check was not checking

`install_check.sh` builds the wheel, installs it into a throwaway environment
and drives it — the only thing in the repository that runs the artefact rather
than the source tree. Two things were wrong with it, and both are the same
shape: it had only ever been run by the person who wrote it, on the machine
they wrote it on.

### It proved the server imports, not that it answers

For a thing whose entire job is to be spoken to, the check that matters is
whether a client gets a tool list. The script verified `from
ticket_ai_mcp.server import main` — which a server that imports cleanly and
then says nothing over stdio passes.

There is now a probe that speaks plain JSON-RPC down a pipe: `initialize`,
`notifications/initialized`, `tools/list`, and count what comes back. No client
library, because the point is to arrive as a stranger. Unregistering one tool
and rebuilding gets *the server did not offer: ['house_style']* rather than a
green run.

### It could only pass on one machine

The first line was `cd "$HOME/projects/Ticket-AI-MCP"`, and `uv` was looked for
under `$HOME/.local/bin`. The CI job that runs this script checks out somewhere
else and has `uv` on the PATH, so its first run on a runner would have failed
on the `cd`, before testing anything at all. Nothing had ever been pushed, so
nothing had ever noticed.

It now uses its own location and whatever `uv` is on the PATH, verified by
copying the repository to `/tmp` and running it from there.

Two tests keep both true: one fails if a `$HOME` path comes back, and one
compares the probe's expected tool names against the tools the server module
actually defines — so a ninth tool cannot ship unchecked.

## What opening the page in a browser found

Every test in this suite asked whether the parts were translated. None asked
whether the *page* was. Serving the UI against a real GitLab profile with
`TICKET_AI_UI_LANGUAGE=de` and reading it found four things, all the same
defect wearing four hats: a string built as English prose somewhere the
reader's language was not known yet.

- **The habits list.** A German heading — *GEWOHNHEITEN* — over
  `labelled, assigned, list, checklist, code, screenshot, cross ref`. Those
  were the dictionary's own keys, printed straight into the page.
- **The caveat, shouted.** Notes had been given the chip style, which is
  `text-transform: uppercase`. That is fine for a three-word severity tag and
  unreadable for the paragraph explaining which of the numbers above to
  distrust. Caveats have their own style now.
- **The review's caveat, in English.** The house-style tab had been fixed to
  render notes in the page's language; the review tab passed the English
  rendering straight through, under a German heading.
- **What the ticket got right.** *PASST SCHON* over "has the What happened?
  section". `passed` was English prose built in `review.py`, exactly as
  findings had been before they became data. It is data now, with the same
  treatment: the sentence is built at display, and the section names inside it
  stay in whatever language the team writes.

And one that was wrong in **both** languages: the language finding read
*Written in de.* and *Auf de geschrieben.* The template was fine; the
parameter was a language code where a language name belongs.

Fixing that one produced a small lesson of its own. `render(code, part,
params, language)` takes a parameter called `part`, and the module has a
function called `part` — so calling it inside `render` raised *'str' object is
not callable*. Caught immediately because the check was to read the sentence,
not to see the test go green.

## The messages that never reached anybody

Every careful sentence in this project — the rate limit that is a wait and not
a bad token, the repository that moved and where to, the board with issues
switched off, the "call learn_conventions first, it takes a minute and is
cached" — is carried by a `TrackerError`.

The MCP layer treats an exception out of a tool as a crash. The client is told
`Error executing tool ticket_template`, and, in the library's own words, *the
exception's own text stays on the server*.

So five of the eight tools answered a first-time caller with nothing at all.
`house_style` was the exception because it **returns** its message instead of
raising, which is why it was the only one anyone ever saw — and why it had the
only test.

This had been invisible because every test called the tool functions directly.
Going through `call_tool`, the way a client does, showed it in one run:

| tool | before | after |
|---|---|---|
| `house_style` | message reaches the caller | unchanged |
| `ticket_template` | `Error executing tool ticket_template` | message reaches the caller |
| `template_gaps`, `review_draft`, `review_ticket`, `review_open_tickets` | same | same |

A `TrackerError` is not a crash — it is the tool answering *this cannot be
done, and here is why* — so it is returned as the result. Anything else still
raises, because a real bug should look like one, and a test holds that line.

### The one underneath it

Chasing the same failure in the test workspace turned up a second defect.
`ticket_context` called `changed_files`, which the tracker protocol documents
as the **optional** fourth method — *an empty tuple is a valid answer, and
callers treat this as a bonus, never as a fact they can rely on*. It was being
relied on, so any adapter implementing only the three required methods brought
the tool down with an `AttributeError` — which the MCP layer then reported as
`Error executing tool ticket_context`, cause discarded, for the same reason.

### And a third, from reading the same code

`ticket_context` works without a house style: the prior art comes from the
tracker. It said nothing about it. The note existed but its condition covered
only a profile that exists and is empty — never a profile that is absent,
which is the state every first-time caller is in.

## And the same question asked of the CLI

The MCP surface was swallowing its messages, so the obvious next question is
what the other front end does with the same failures. Driving seven of them:

    style, nothing configured        exit=2  No tracker chosen. Set TICKET_AI_TRACKER to ...
    style, no profile learned        exit=2  No house style has been learned ... Run `ticket-ai learn`
    learn, project does not exist    exit=2  no project acme/... on https://gitlab.com. Check the path ...
    review / compose / gaps          exit=2  the same, naming the same fix

Which is the behaviour that was wanted, with the right exit codes — the
workflow this tool prints pipes `compose --fail-under` into a step that posts
the result, and a command that says "failed" while exiting 0 posts it anyway.

One was wrong. `models`, with nothing configured at all, said *the key may be
wrong* about an endpoint it did not name — and there was no key, because the
user had set nothing. Defaulting to a local Ollama is right for a question
like "what can I use?"; not saying so is not. It now names the endpoint it
tried and, when nothing was configured, says that is what it assumed.

### The helper that could not have caught it

Those three tests failed on their first run for a reason worth writing down:
the CLI test helper returned `capsys.readouterr().out` — stdout only — and the
CLI puts every error message on stderr, where they belong. So no test in this
repository had ever been able to assert on a CLI error message. It returns both
streams now.

And they were slow: `https://api.example.com` in a fixture is a real DNS
lookup, and three of them added ten seconds to a seven-second suite. Port 9 on
localhost refuses instantly and proves the same thing.

## Boards that are not in English

Every board in the fleet writes English tickets, and German is the language
this tool was built for. Nine German-speaking projects were checked for a
German-language board — public-sector repositories, community projects, a
German university library's catalogue software. There is not one: open source
is written in English, including by teams who speak German to each other. The
most mixed board found, `hbz/lobid-resources`, is 23 English tickets to 4
German out of 30, and the tool calls it English, which is the right call.

That is a fact about the landscape rather than a gap in the tool, and it is why
the German path is exercised the other way — with the ticket language forced on
an English board, through a live model, in
[local-models.md](local-models.md).

### Two suspicions that did not survive being checked

**"It calls a Chinese board English."** `ant-design/ant-design` has CJK
characters in 14 of 25 recent tickets and the guesser answered `en` for 23 of
them, which looks like a confident wrong answer. Counting letters instead:
exactly **one** ticket in 40 is more than 30% Chinese, and for that one the
guesser already returns *nothing* rather than guessing. The other thirteen
carry a Chinese phrase inside an English ticket. The tool was right.

**"It says issues are disabled when they might not be."** The refusal for a
board whose rows are all pull requests read *most likely has issues disabled*.
Both repositories in the fleet that reach it report `has_issues: false`, so the
inference had been correct — and since GitHub answers that outright, the hedge
was unnecessary. One request, on a path that has already spent ten pages:

> `encode/httpx` has issues disabled - GitHub says so, and all 1000 rows on its
> issues endpoint are pull requests. Whatever this project uses for tickets, it
> is not here.

It still hedges when the repository cannot be read, when the answer is not the
object GitHub documents, and when issues really are enabled — a busy board
whose recent closed rows all happen to be pull requests is a different thing
and keeps the softer sentence.

## What hostile tickets found

Real boards produce shapes nobody designs for: a description that is one
enormous stack trace, a ticket that is only a screenshot, Windows line endings,
a title with a control character in it. Nineteen of them through the functions
that measure a ticket.

Nothing crashed, and most of the answers were right for the right reason — a
ticket that is 20,000 lines of code measures as **zero characters of prose**,
because code is excluded on purpose, and the review calls it an empty
description rather than a thorough one. Arabic headings are found, combining
accents fold, an unclosed code fence swallows the rest exactly as it does on
the board.

One thing was wrong, and it was not a wrong answer — it was the time taken.

### An unbounded quadratic in the middle of an MCP call

Every ordered pair of recurring headings is examined, and nothing limited how
many there could be. Measured over a corpus of twelve tickets:

| recurring headings | before | after |
|---|---|---|
| 1000 | 0.94s | 0.09s |
| 2000 | 3.32s | 0.17s |
| 3000 | ~10s | — |
| 5000 | — | 0.68s |

`learn_conventions` is a call somebody is waiting on, and this is reachable
without anybody trying: a template that generates headings, or a board whose
tickets each paste in a table of contents.

The number of headings usable as the *when* half of a rule is now capped at
sixty, keeping the ones the team uses most. The widest board in the fleet
produced eleven, and the one with the most sections had twenty-five, so the
ceiling is five times the worst case ever observed — and `BurntSushi/ripgrep`
still returns exactly the same 56 rules and the same block of eight after it.

The regression test took two attempts, in the way that is becoming familiar: a
thousand headings with a two-second budget costs 0.94 seconds uncapped, so it
passed with the fix and without it. Two thousand and one second is the pair
that separates them.

## Running it

```bash
uv run python tests/fleet.py [sample] [how-many-boards] [github|gitlab|both]
```

Results land in `fleet.json`. Budget roughly a minute and a hundred API calls
per board; the whole list will reach a rate limit on a fresh hourly window, and
the harness waits once and retries rather than filling the report with the same
message thirty times.
