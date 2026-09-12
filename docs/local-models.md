# Composing with a local model

`compose` turns a title into a ticket, and the model that writes it can run on
your own machine: no key, no account, nothing leaving the laptop. This is what
that actually produces, measured rather than described.

Everything below was run against live boards with **llama3.2:3b** — a 2 GB
model, deliberately the small end, because the interesting question is whether
the tool can make a weak model useful rather than whether a strong one needs
help.

## Setup

```bash
ollama pull llama3.2:3b
export TICKET_AI_WRITER=ollama
export TICKET_AI_MODEL=llama3.2:3b
ticket-ai compose --title 'Lager: Etikettendruck bricht bei mehr als zehn Positionen ab'
```

## What came back

Seven titles, three boards, one profile learned per board beforehand.
`alignment` is the share of applicable checks the draft passed, measured
against that board's own tickets.

> **Measured before `promised` existed, and not recomputed.** A draft is now
> held to the skeleton it was handed and those headings are checked
> unconditionally, so both the check counts and the scores on this page would
> come out differently today. See
> [evaluating-compose.md](evaluating-compose.md). The runs
> were real and the figures are what they were. **They are not current, and the
> numbers here should not be compared against a run made now.** Rerunning them
> needs the same models against the same boards, and one of the three boards is
> a private one whose tickets cannot be republished.


| Board | Ticket | Alignment | Checks | Attempts | Time |
|---|---|---|---|---|---|
| a private board (GitLab, German) | filter | 100% | 3 | 2 | 51s |
| a private board | catalogue order | 100% | 3 | 2 | 48s |
| a private board | PDF generation | 100% | 2 | 2 | 43s |
| pydantic (GitHub, English) | field alias | 72% | 8 | 2 | 70s |
| pydantic | discriminated union | 83% | 8 | 2 | 71s |
| astral-sh/uv (GitHub, English) | lock vs sync | 80% | 5 | 2 | 41s |
| astral-sh/uv | editable install | 80% | 5 | 2 | 27s |

A run costs 30–70 seconds on a laptop CPU, and always takes the revision pass —
the first draft has never yet cleared the corpus on the first try with a model
this size.

Note the **Checks** column. pydantic's issue form is strict, so eight checks
apply and 72% means missing two of them; a private board has no board-wide
template, so three checks apply and 100% is a weaker claim. Alignment is only
comparable within a board.

Every remaining finding on the English runs was "no labels" or "no checklist" —
the labels one because the batch did not pass `--label`, which is a gap in how
it was run rather than in what was written.

## A German sample, at 100%

The boards above are real and two of them are public, but printing a generated
ticket from a private one would be publishing someone's product detail. So this
sample comes from a corpus of invented tickets for a fictional warehouse tool -
a real model run, against a board that belongs to nobody.

Title in, nothing else:

    Lager: Etikettendruck bricht bei mehr als zehn Positionen ab

Out, after one revision, 49 seconds, 100% over 7 checks:

```markdown
### Ziel
Das Etikettendruck-System bricht bei mehr als zehn Positionen ab, wodurch die
korrekte Darstellung der Etiketten auf den Produkten nicht möglich ist.

### Abnahme
- [ ] Die korrekte Anzahl von Etiketten wird immer angezeigt.
- [ ] Bei mehr als zehn Positionen sollte das System eine Warnung anzeigen...

### Details
Das System funktioniert nur bis zu zehn Positionen und zeigt danach eine
falsche Anzahl von Etiketten.
```

Correct German, the corpus's own sections, its own length, its own checkbox
habit. It is also repetitive - the second criterion runs to fifty words and
says one thing three ways - which is what a 3B model does, and no amount of
measurement fixes it.

## What the prompt has to spell out

Three things a small model gets wrong unless told, all found by running a batch
rather than a single ticket:

**Labels are metadata, not a section.** Given a list of the project's labels
with no explanation, several drafts wrote a `Labels` heading into the body. The
system prompt now says what they are for.

**Feedback is not text to reuse.** Handed a draft and a list of corrections in
one flat block, a 3B model copied a correction's wording into the ticket. The
draft is fenced now and the instructions say so outright.

Between them those two took the German runs from 67% to 100%.

**A board with no template needs saying so.** Three of the forty-three boards in
the fleet use no recurring heading at all, and one more has a heading that is
not common enough to ask for. Their prompt used to read "Sections to use, and
why each one:" followed by "(no recurring sections)" — a heading promising a
list and then no list — while the system prompt separately said to use exactly
the sections it was given. The prompt now says *this team has no section every
ticket uses, write prose paragraphs with no headings at all*, and rule 2 names
that case rather than leaving "exactly none" to interpretation.

Checked against llama3.2:3b on two real boards, same run:

| board | template | headings written |
|---|---|---|
| Apache KAFKA | none measured | 0 |
| home-assistant/core | 10 sections | exactly those 10, in order |


## Writing German on an English board

The case the two language settings exist for: a team that writes its tickets in
German on a board whose template headings are English. Driven end to end
against Inkscape's board with the ticket language forced to German, through
llama3.2:3b.

**Rule 1 and rule 2 were telling the model opposite things.** Rule 1 said
*write in German — every heading and every sentence*; rule 2 said *use exactly
the sections you are given, with those headings*. This model kept the English
headings, which is right. A model that obeyed rule 1 instead would translate
`What happened?` into a section the team does not have, and then every section
check would fail on a draft that was actually fine. Rule 1 now exempts the
given headings and says why.

**The title came back inside the body.** Rule 5 says to write the description
only; the first run opened with the ticket title underlined in `=`, the second
with it in bold. The body goes into the description field, directly under that
same title, so a copy of it would sit in every ticket this tool writes. A first
line that *is* the title is now dropped — matched on the same normalisation the
headings use, so bold, hashes, case and punctuation do not save it. A first
line that says anything else is the model writing, and it stays.

After both: prose in German, headings in the team's English, findings rendered
in German, alignment 73% with two real findings.

## A pattern that was measured and not built

The same run produced a heading style the tool does not recognise:

    Version info
    ------------

That is a heading in every markdown renderer and in none of this tool's four
patterns, and the obvious move is to add a fifth. Counted first, across seven
boards and 210 tickets: **23 apparent matches, 22 of them a code fence followed
by a line of dashes, and one more of the same.** Zero real ones.

So it is not built. `---` is also a horizontal rule, a table border and a YAML
fence, and a pattern that fires only on those is a pattern that only ever
produces false findings. The four that exist are there because teams were
measured using them.

## What to expect of a small model

- **It writes short.** The most common remaining finding, and the reason the
  prompt names a character target.
- **It will describe software it has imagined** if it does not know the real
  thing — a plausible reproduction for a UI that does not exist. Read a draft
  before posting it; that is what `--fail-under` and the printed review are
  for.
- **The revision pass is one round.** A second fixes mechanical misses; a third
  mostly rewords.

The structure is handled by measurement either way. What a bigger model buys is
content you have to check less. See
[what-it-produces.md](what-it-produces.md) for the other end of that scale.

## Where a bigger model helps

Quality of prose, not compliance with the template - the template is handled by
the measurements either way. If the tickets are read by people outside the
team, a 7B or larger model is worth the disk. `qwen2.5:7b` is noticeably better
at German.

The same command works against any OpenAI-compatible endpoint:

```bash
export TICKET_AI_WRITER=openai
export TICKET_AI_BASE_URL=https://openrouter.ai/api/v1
export TICKET_AI_MODEL=...
export TICKET_AI_API_KEY=...
```

## If the tickets should not leave the building

That is the case `--writer ollama` is for. The model runs locally, and the only
network traffic is the tracker API this tool already needs.

## A note for WSL users

Ollama installed as a Windows application binds to `127.0.0.1` on Windows,
where a WSL shell cannot reach it. Either run `ticket-ai` from Windows, or set
`OLLAMA_HOST=0.0.0.0` in the Ollama app's settings - which also exposes it to
your local network, so decide whether you want that.
