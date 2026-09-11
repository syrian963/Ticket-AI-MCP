# The evaluation dataset

Everything in this tool except one thing is counting, and counting is tested in
`tests/`. The exception is `compose`, where a language model writes the prose.
Every existing test mocks that writer, so nothing here has ever measured how
good the real output is.

`docs/local-models.md` came closest: seven titles, one model, one pass. This
directory is the difference between that and an evaluation.

## What a case is

A title a real team actually wrote a ticket for, taken from a public board,
stored next to the board profile as it stood when the case was collected.

```
evals/dataset/<slug>/
  board.json     tracker, project, source url, the date it was collected
  profile.json   Profile.to_json(), frozen
  cases.jsonl    one case per line
```

**The profile is frozen on purpose.** `compose` writes into whatever shape the
profile describes. Relearning it between two runs changes the score without
anything about the model changing, and the comparison then measures the wrong
thing.

**The cases were held out of the profile.** `tools/build_eval_dataset.py` splits
the exemplars before building it, by a SHA-256 bucket of the ticket id, so a
case is never a ticket the profile was fitted to. Holding out the lowest-scoring
exemplars instead would be simpler and would quietly make every case harder than
the corpus that marks it.

**The reference body is not a right answer.** It is what the team shipped. Two
people write a usable ticket for the same title in two different ways, and
scoring a draft on its distance from one of them would measure imitation. It is
stored so that a person rating a draft later can see what this board treats as
normal.

## Only public boards

Every case carries the URL it came from and has to be readable without
credentials, so that anyone can check a number in this repository against the
source.

This is not only about reproducibility. **A dataset assembled from an
employer's tracker is their confidential information**, and anonymising it does
not change that. If a board cannot be linked, it does not belong here.

## Adding a board

```bash
export TICKET_AI_GITLAB_URL=https://gitlab.com     # public, no token needed
uv run python tools/build_eval_dataset.py \
    --tracker gitlab --project inkscape/inkscape --slug inkscape \
    --sample 200 --keep 45
```

GitHub needs a token even for public repositories, because the unauthenticated
rate limit does not survive a sample of this size. A read-only token is enough.

Run it once per board and commit the output. Nothing in the evaluation path
writes to this directory.

## Running the suite

The runner produces records and stops there. Scoring is a separate pass over
those records, so a metric can be added or corrected without paying for the
model runs a second time; at thirty to seventy seconds a case, that is the
difference between trying an idea and not bothering.

Records are appended line by line, so a suite interrupted after forty minutes
still leaves forty minutes of results.
