# Measuring the one thing that is not counting

Everything in this tool counts, and counting is tested the ordinary way. Then
there is `compose`, where a language model writes German or English prose into
a shape the counting worked out. **Every test in `tests/` mocks that writer.**
They check that the plumbing holds and say nothing at all about what comes back.

`local-models.md` came closest: seven titles, one model, one pass, alignment
between 72% and 100%. That is a demo. This is what had to change to make it an
evaluation.

## The dataset had to stop moving

134 cases over ten public boards, each stored next to **the board profile as it
stood when the case was collected**.

Freezing the profile is not tidiness. `compose` writes into whatever shape the
profile describes, so relearning a board between two runs changes the score with
nothing about the model having changed, and the comparison then measures the
wrong thing while looking exactly like a comparison.

**And a board's shape is less stable than it looks.** The same board, mined at
three sample sizes:

    gitlab-runner   n=15   skeleton 0                top section 53%
                    n=20   skeleton 1  (by rate)     top section 65%
                    n=32   skeleton 3  (by pairs)    top section 38%

It crosses the threshold in both directions and changes which rule carried it.
Every reading is correct about the tickets it saw, which is precisely why the
profile has to be a file rather than something recomputed per run.

| Board | Cases | Tracker | Language | Skeleton | Sections seen |
|---|---|---|---|---|---|
| inkscape | 19 | GitLab | English | 5 | 7 |
| kern-ux | 17 | GitLab | **German** | 2 | 24 |
| gitlab-cli | 16 | GitLab | English | 6 | 14 |
| kafka | 14 | **Jira** | English | **prose** | 6 |
| fitko-fim | 13 | GitLab | **German** | 3 | 5 |
| fdroid | 10 | GitLab | English | 3 | 4 |
| hibernate | 10 | **Jira** | English | **prose** | 4 |
| veloren | 10 | GitLab | English | **prose** | 5 |
| cassandra | 12 | **Jira** | English | **prose** | 8 |
| gitlab-runner | 13 | GitLab | English | 3 | 22 |

Two columns, because they are two different things. **Skeleton** is what the
prompt asks for: the sections common enough to be worth naming. **Sections
seen** is everything the board ever used, and that is what an invented heading
is measured against. kern-ux is the gap in the flesh - two sections are asked
for and twenty-four have been used, so a draft can produce a heading nobody
requested and still be writing in the house style.

The spread is not variety for its own sake. **`compose` branches on each
column.** A German system prompt, a tracker abstraction, and a `_skeleton_block`
that either lists the sections or tells the model to write no headings at all.
CI asserts all three survive, because a dataset that lost one would stop
measuring that branch **without the average moving**, and the loss would be
invisible in the one number anybody reads.

## The cases were held out of the profile

`tools/build_eval_dataset.py` splits the exemplars **before** building the
profile, by a SHA-256 bucket of the ticket id.

A case whose ticket also went into the profile is a case the profile was fitted
to: the draft gets marked against a corpus that already contains the answer, and
the score comes out flattering for a reason that has nothing to do with the
model.

Splitting by rank instead would be simpler and would quietly make every case
harder than the corpus judging it. Splitting with `hash()` would be simpler
still and would produce a different split every process, because Python salts it
per run.

## The reference is not a right answer

Each case stores what the team actually shipped. It is never scored against.

Two people write a usable ticket for the same title in two different ways, and
marking a draft down for differing from one of them measures imitation. The
reference is there so that a person rating a draft can see what the board treats
as normal.

## Two metrics `review_draft` does not have

**Invented sections.** `review_draft` asks whether the sections a board uses are
present. It never asks what else the draft added, and the reason it never had to
is that people rarely invent a heading their board has never seen. Models do it
constantly.

Measured against every observed section rather than the skeleton: a heading used
in a third of tickets is house style even though the prompt did not ask for it,
and counting that as invented would punish a draft for being right. On a
prose board every heading is invented, which is the correct reading — the
prompt said in plain words to write none.

**Spread.** One figure per case reads as precision a non-deterministic writer
cannot support. `--repeats` runs each case several times; the report prints the
deviation next to the mean, and a single observation reports *no* spread rather
than a spread of zero.

## The gate tolerates noise on purpose

`evals/baseline.json` holds a committed figure, and a drop has to exceed **0.05
absolute** to fail.

Nobody remembers last month's number, so without a file a slow slide across four
commits is invisible. But the writer is not deterministic, `local-models.md`
already shows one board moving eleven points on one model, and **a gate that
fires on every decrease gets switched off inside a week**, which is worse than
having none.

Boards are checked as well as the total. Three points gained on one and eight
lost on another comes out level, and the level number is the one nobody
investigates. A board in the baseline that produced no runs is a complaint, not
a silent improvement.

## The judge is asked one question, and is not believed on its own

Counting cannot tell whether the prose is about the title. That is the failure
worth catching: a draft can carry every section, hit the length, take the right
labels and describe something else entirely, and `review_draft` gives it full
marks.

So `--judge` asks that and nothing else. Not "is this a good ticket" — that is
the question this whole tool exists to replace with counts. Three answers, not a
score out of ten, because a model asked for a number spreads it over 6, 7 and 8
in a way that survives no calibration. A verdict outside the three words is an
error rather than being rounded to the nearest one; rounding puts an invention
into the figure the calibration exists to measure.

**Raw agreement is close to useless on its own.** If nine drafts in ten are on
topic, a judge that answers "on topic" every time agrees ninety percent of the
time and has learned nothing. Cohen's kappa subtracts the agreement two people
guessing at those rates would reach, and scores that judge zero.

The human half comes from a person, through `tools/label_drafts.py`. Nothing
generates a label. Without `--labels` the judge line prints `uncalibrated`
**every time** rather than documenting the caveat once and letting the number
travel without it.

## What the dataset found before a single model ran

Loading the first German board failed:

```
cases.jsonl:9: not valid JSON: Unterminated string starting at line 1 column 57
```

The quoted record really was cut in half. **The file was correct.** A real
ticket carries a literal `U+2028`, `str.splitlines` breaks on it — along with
`U+2029`, `U+0085` and three ASCII separators — and `json.dumps` escapes none of
them. Three readers had the same bug, and every one of them blamed the data.

Fifty-five English cases had run clean through it for two commits.

## What is still missing

**No real number yet.** The harness is complete and tested; it has never been
pointed at a model. That needs `ollama pull llama3.2:3b` or an endpoint, and
until then every figure on this page is a description of the machinery rather
than a result.

**No labels yet**, so the judge cannot be calibrated and its output is exactly
the opinion the `uncalibrated` line says it is.

**One assertion cannot fire on its own.** The guard wants a board without a
skeleton and a board with one. It cannot fire independently because every Jira
board here writes prose, so removing all the prose boards removes both trackers
and the tracker assertion goes first.

### Two claims this page made and had to withdraw

Chasing that, an earlier version said the 0.6 skeleton threshold is never a
close call, and that the two shapes are two shapes rather than two ends of a
slider. The evidence was the boards already in the dataset, which is a sample
chosen by whoever made the claim.

Probing boards that are **not** in it refuted the first in the first row:
`gitlab-org/gitlab-runner` reads 68% on a 60-ticket sample and 53% on a
40-ticket one, which is the same board on both sides of the threshold depending
on how much history was looked at.

The second was worse, and collecting gitlab-runner properly is what exposed it.
**The top board-wide rate is not what decides the shape.** `Profile.skeleton`
has two paths, and on most boards here the second one does all the work:

| Board | Skeleton | via rate ≥ 60% | via a pair | Strongest pair |
|---|---|---|---|---|
| gitlab-cli | 6 | 0 | **6** | current bug behavior → expected correct behavior, 12/12 |
| inkscape | 5 | **5** | 0 | — |
| fdroid | 3 | 0 | **3** | what did you expect → what did you see instead, 18/19 |
| fitko-fim | 3 | 1 | **2** | Relevante Informationen → Warum machen wir das?, 18/18 |
| gitlab-runner | 3 | 0 | **3** | Steps to reproduce → Summary, 10/10 |
| kern-ux | 2 | 0 | **2** | Beschreibung → Akzeptanzkriterien, 8/9 |
| veloren, kafka, hibernate, cassandra | 0 | 0 | 0 | — |

**Only inkscape earns its skeleton the way this page assumed every board did.**
kern-ux has no section above 39% and still has a skeleton, because two headings
appear together on eight of the nine tickets that have either.

The mechanism is obvious in hindsight and the docstring of `skeleton` says it
outright, having been written after a real board proved the point. A board's
tickets are not all bug reports. Features and chores dilute a bug form's
sections board-wide, so the rate understates the template — but among the
tickets that use one half of a pair, the other half is nearly always there.
12/12, 18/19, 18/18, 10/10, 8/9.

So the earlier table of "top section rates" was measuring one input and
reporting it as the decision. What it does still show is the Jira half, and
there the two paths agree: seven Jira boards measured — kafka, hibernate,
cassandra, LUCENE, SERVER, MDEV, JBEAP — **not one has a section above 13% and
not one has a pair that clears the floor either**. CASSANDRA carries
`Environment`, `Reproduction`, `Result`, `Tests`, `Summary`, `Root cause` and
`Steps to reproduce` in its vocabulary; none of them reaches seven percent and
none of them implies another.

That is also the case for measuring invented headings against every observed
section rather than the skeleton. On CASSANDRA a draft writing `Root cause` is
using the board's own vocabulary, and the prompt could not have named it.

The guard stays: it costs nothing and becomes meaningful the day a Jira board
with an enforced template joins. But this page should not claim it is being
exercised.
