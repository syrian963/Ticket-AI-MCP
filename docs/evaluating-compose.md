# Measuring the one thing that is not counting

Everything in this tool counts, and counting is tested the ordinary way. Then
there is `compose`, where a language model writes German or English prose into
a shape the counting worked out. **Every test in `tests/` mocks that writer.**
They check that the plumbing holds and say nothing at all about what comes back.

`local-models.md` came closest: seven titles, one model, one pass, alignment
between 72% and 100%. That is a demo. This is what had to change to make it an
evaluation.

## The dataset had to stop moving

109 cases over eight public boards, each stored next to **the board profile as it
stood when the case was collected**.

Freezing the profile is not tidiness. `compose` writes into whatever shape the
profile describes, so relearning a board between two runs changes the score with
nothing about the model having changed, and the comparison then measures the
wrong thing while looking exactly like a comparison.

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

**One assertion cannot fire on its own, and it took a count to say why
correctly.** The first guess was that shape and tracker line up exactly. They do
not:

| | prose | skeleton |
|---|---|---|
| **GitLab** | veloren | fdroid, fitko-fim, gitlab-cli, inkscape, kern-ux |
| **Jira** | hibernate, kafka | — |

There is a GitLab board writing prose. What is true is the weaker statement, and
it is enough to explain the guard: **both Jira boards are prose**, so the prose
set contains the Jira set, and removing every prose board removes every Jira
board. The tracker assertion fires first and the prose assertion never gets a
turn.

The Jira half is probably not sampling luck. GitLab issue templates are files
committed in the repository, so a board that wants a shape gets one by default;
a Jira description is a free text field, and both Jira boards here read like
mailing-list posts. One prose GitLab board against five with templates points
the same way.

The guard stays: it costs nothing and becomes meaningful the day a Jira board
with a template joins. But this page should not claim it is being exercised.
