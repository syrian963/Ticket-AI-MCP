# Changelog

Notable changes, newest first. Versions follow [semantic versioning](https://semver.org),
and the release workflow refuses a tag with no section here.

## Unreleased

### Measuring what the model writes

Every test in this repository mocks the writer, so none of them said anything
about the prose that comes back. `docs/local-models.md` came closest: seven
titles, one model, one pass. `ticket-ai eval` is the difference between that
and an evaluation.

- **A dataset that does not move.** 121 cases over nine public boards, each
  stored next to the board profile as it stood when the case was collected.
  `compose` writes into whatever shape the profile describes, so relearning a
  board between two runs moves the score with nothing about the model having
  changed.
- **Cases held out of the profile**, split by a SHA-256 bucket of the ticket id
  before the profile is built. A case the profile was fitted to flatters the
  model for a reason that has nothing to do with the model, and splitting by
  rank instead would quietly make every case harder than the corpus marking it.
- **Two figures `review_draft` does not have.** It asks whether the sections a
  board uses are present; it never asks what else the draft invented, because
  people rarely add a heading their board has never seen and models do it
  constantly. And spread, because one number per case reads as precision a
  non-deterministic writer cannot support.
- **A gate that tolerates noise.** A drop has to exceed 0.05 absolute, and
  every board is checked as well as the total — three points gained on one and
  eight lost on another comes out level, and the level number is the one nobody
  investigates.
- **A judge with one question**, and Cohen's kappa to say whether to believe
  it. Raw agreement is close to useless: nine on-topic drafts in ten and a
  judge that always answers on_topic agree ninety percent of the time and have
  learned nothing. Without human labels the output prints `uncalibrated` every
  time rather than travelling as a number.

The dataset covers all three branches `compose` takes — German and English, two
trackers, and boards with a section skeleton as well as boards that write pure
prose. CI asserts all three survive, because losing one would stop measuring it
without the average moving.

### Fixed

- **`str.splitlines` is wrong for JSONL.** It breaks on U+2028, U+2029, U+0085
  and three ASCII separators, and `json.dumps` escapes none of them. A German
  ticket carries a literal U+2028, so a valid file read as a truncated record
  and the error blamed the file. Three readers had it.
- `ticket-ai eval` from an installed wheel now says the dataset belongs to the
  repository, instead of reporting that a path inside site-packages is missing.

## [0.1.1] - 2026-09-11

The package can be found without being named. The MCP registry proves that
whoever publishes a manifest owns the package it points at, by looking for the
server's name in the package's own description - so the line is in the README
now, and a release writes the manifest to the registry as well as the wheel to
PyPI.

Nothing about the tool itself changed.

## [0.1.0] - 2026-09-10

First cut. Learns what a team's tickets look like by measuring the ones that
shipped, then holds new tickets to that.

### Reading the board

- `template_gaps` compares the declared issue template with the tickets that arrive.
  It reads a GitHub issue form's keys in whichever order the author wrote them —
  `validations` before `attributes` is common and made every required field on
  home-assistant/core's form read as optional. A template that declares no
  fields is reported as a template that declares nothing, not as no template at
  all. And a gap names the form file it came from, so a field required by a
  form these tickets never used reads as that rather than as a field nobody
  fills in.
  A required field nobody fills in is a form to change, not a team to nag.

### Answering

- A tracker's refusal reaches the caller. The MCP layer keeps a crash's text on
  the server, and five of the eight tools were raising `TrackerError` — so
  every message about a rate limit, a moved repository, a board with issues
  switched off, or a house style that has not been learned yet arrived as
  `Error executing tool ticket_template` and nothing more. A refusal is the
  tool answering, so it is returned; a real bug still raises.
- `ticket_context` works with a tracker that implements only the three required
  methods. It called the optional fourth one, which the protocol says callers
  must treat as a bonus.
- `ticket_context` says when there is no house style behind it. It works
  without one, and used to say so only in the rarer of the two ways of not
  having one.

### Trackers

- GitLab, Jira and GitHub behind one adapter with three methods and an
  optional fourth for the files a linked change touched.
- Jira works against Cloud and against self-hosted Server and Data Center;
  which one is detected from the instance. Public boards are readable with no
  credentials at all.
- GitLab reads a public project without a token, which is the quickest way to
  see what the tool does. gitlab.com serves the issues and their merge requests
  to anyone and withholds the comments, so a profile built that way says at the
  bottom which parts of the board it was not allowed to read, and which of its
  own numbers are weaker for it.
- A Jira behind a company hostname works. Atlassian answers 301 from a vanity
  domain to the real site; detection read that as an answer, guessed the wrong
  product from the hostname and then asked a Cloud instance for a Server
  endpoint. Anonymously it now follows the redirect; with a credential it
  refuses and names the real URL, because a token belongs to the host it was
  issued for.
- A corpus with no merged change anywhere no longer files every ticket under
  "stalled". Jira has no supported API for the development panel, so seven
  public boards came back 0 shipped and 349 stalled — a false thing to tell a
  team about its own board. It says the comparison cannot be made instead.
- A German page is German all the way down. What a ticket got right, the
  caveats a review carries, and the habits list were still English prose under
  translated headings; they are data now, rendered in the reader's language at
  display. A caveat also stopped being styled as a chip, which had been
  shouting whole paragraphs in capitals.
- The language finding names a language rather than a code. It read "Written
  in de." in English and "Auf de geschrieben." in German.
- The caveats on a profile are translated too. They are the sentences that say
  how much of the rest of the page to believe, and they were English literals
  built where the profile is built - so a German page explained itself in
  English under German headings. They are codes and measurements now, like
  findings, rendered at the moment of display. A profile cached before this
  keeps its sentences verbatim rather than losing them.
- Reports end with what was left out of the corpus and why. A board of
  dependency bumps used to produce "built from 2 tickets" with no hint that the
  other 48 carried no description at all.
- A corpus where every ticket listed and none could be read is refused with the
  first reason, instead of profiled into a page of zeroes that reads like an
  answer about a team.

### Measuring

- A profile of the corpus: recurring sections, length, labels, title markers,
  language, and habits like screenshots and checklists.
- **Conditional conventions.** A section that is only conventional on one kind
  of ticket is invisible in a board-wide rate; a pair is kept when the
  conditional rate beats the overall rate by a clear margin.
- A corpus with thousands of recurring headings cannot stall the call that
  learns from it. Every ordered pair of headings was examined with nothing
  bounding how many there could be: 2000 of them cost 3.3 seconds, 3000 cost
  ten. The widest real board produces eleven, so the ceiling is well clear of
  anything a team writes.
- A team whose sections always travel together is reported as one block, not as
  every pair of them. The rules are kept whole for that reason: clustering
  needs both directions of a pair, and truncating the list first turned one
  block into a dozen loose rules and cut through ties while doing it.
- A section missing from a ticket is one finding, however many other sections
  imply it.
- `alignment` is the share of applicable checks a ticket passed. A ticket that
  no check applied to is reported as unmeasurable rather than as 100%.

### Writing

- The Actions workflow `models --workflow` prints is checked against the CLI it
  invokes. It is output that gets pasted rather than run from here, so a flag
  renamed in the CLI would otherwise break a file already sitting in somebody's
  repository with nothing here failing.

- `ticket_context` gathers related past tickets, the files their merge requests
  changed, and files in the checkout that mention the subject. No model needed.
  Related tickets are ranked with a length term, because without one the
  ranking was being done by length: measured over six subjects on 120 real
  tickets, the top match was longer than 83% of the pool on average, and 56%
  after. Words like "after" and "still" no longer count as evidence.
  A subject that says "session" finds `sessions.py`, which it did not before —
  the file named after the subject was scoring the same as a tutorial that
  mentions it once. Changelogs count for half: one records every change the
  project ever made, so it was ranking on five of six unrelated subjects.
- An issue template's own HTML comments are no longer measured as if the
  reporter had written them. On one GitLab board 24 of 30 tickets carried the
  form's instructions, which produced a section called `<!-- Example file` and
  added 437 characters to the median ticket. Comments inside a fence are left
  alone, because there they are content.
- A ticket written in German on a board whose headings are English keeps those
  headings. The two rules were contradicting each other, and a model that
  resolved it the other way would translate a heading into a section the team
  does not have, failing every section check on a draft that was fine.
- A draft that opens by repeating the ticket title has it removed. The body
  goes into the description field, directly under the same words.
- A board with no template gets a prompt that says so. It used to be handed a
  heading promising a list of sections, no list under it, and a rule telling it
  to use exactly the sections it was given; a small model resolves that by
  inventing sections.
- `compose` turns a title into a draft with a model of your choosing: a local
  Ollama with no key, or any OpenAI-compatible endpoint. The draft is measured
  against the corpus and the findings go back to the model once.
- `review_draft` checks a ticket before it exists, which is the only point at
  which fixing it is free.

### Interfaces

- An MCP server with seven read-only tools, and a CLI with the same reach.
- A local page for the parts that want a text box, in English or German.

### Known limits

- Jira has no supported API for linked branches and merge requests, so the
  "shipped" signal there falls back to remote links and the ranking is weaker
  than on GitLab or GitHub.
- A small local model writes short and will invent a cause if it does not know
  one. See [docs/local-models.md](docs/local-models.md) for measured runs.
