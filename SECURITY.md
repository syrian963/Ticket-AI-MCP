# Security

## Reporting

Open a [private advisory](https://github.com/syrian963/Ticket-AI-MCP/security/advisories/new)
rather than a public issue. A first response should take a few days.

## What this handles

A tracker token, and the contents of your tickets. Both deserve a sentence.

**The token** is read from the environment and used for one thing: HTTP
requests to the tracker you configured. It is never written to disk, never
logged, and never included in a report. There is deliberately no `--token`
option, because an argument ends up in shell history and in the process list
where any other user on the machine can read it.

**Every tracker call is a GET.** Nothing in this tool creates, edits, closes or
comments on anything. A token scoped to read-only — `read_api` on GitLab — is
enough, and using one is the safest way to be sure of that claim rather than
taking it on trust.

**Ticket contents stay local** unless you configure a model that is not.
Learning, measuring and gathering context happen in this process. `compose` is
the exception: it sends the title, the section names, and the titles of related
tickets to whichever endpoint you pointed it at. With `--writer ollama` that
endpoint is your own machine. With `--writer openai` it is whoever you chose,
so choose with the sensitivity of the board in mind.

**The local page binds to `127.0.0.1` and there is no option to change it.**
The process holds a token, and no version of putting that behind a laptop's
network is a good idea. If you need it reachable from elsewhere, put your own
proxy in front and take responsibility for the authentication.

**The cached profile contains ticket keys and counts** — no descriptions, no
comments — and lands in `.ticket-ai/` under the working directory. It is in
`.gitignore` for a reason; if you move it, keep it out of version control.

## What it does not defend against

A tracker you do not control is a tracker whose contents you do not control.
Ticket text is read as data here — it is counted, never executed and never
followed as an instruction — but if you pipe a report into something that does
act on text, that is your boundary to think about, not this tool's.
