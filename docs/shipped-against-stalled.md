# Shipped against stalled

Most of this tool reports rates over the tickets that worked. This reports the
same rates over the ones that did not, and hands you the difference.

    78% of tickets have acceptance criteria
    78% of the ones that shipped, and 30% of the ones that stalled

The first is a number. The second is an argument, and it is the one that
survives someone saying "does that actually matter?"

## How a ticket is sorted

**On outcome alone.** This is the load-bearing decision and everything else
depends on it.

| | |
|---|---|
| **shipped** | a change was merged for it, nobody reopened it, and it drew at most one clarifying question |
| **stalled** | closed with nothing merged, or reopened twice, or it took more than four "what do you mean?" comments |
| **neither** | closed with a merge but reopened once, still open, or closed by a staleness bot |

Nothing in that table looks at the description. If it did, the whole exercise
would be circular: sections would "predict" success because having sections is
what put the ticket in the successful group. Splitting on outcome and comparing
content is the only version of this that means anything.

The third row matters as much as the first two. A ticket that is genuinely
ambiguous is left out rather than pushed into a group to make the sample
bigger.

## Why staleness bots are excluded

This was found on a real board rather than reasoned about. `home-assistant/core`
came back saying that **every field of their mandatory issue form was more
common among the tickets that stalled** - which reads as "filling in the form
makes tickets fail", and is nonsense.

Their bot closes anything quiet for long enough. So "stalled" had filled up
with well-written tickets whose only fault was age, and because the form is
mandatory, all of them carried every field. A bot closure says something about
attention, not about the ticket, so it now lands in neither group.

The phrasings took two passes to get right. A first version matched "closed as
inactive" and missed "closing as inactive", which is the same event in the
present tense.

## The guards

- **Both groups need eight tickets.** Below that one ticket moves a rate by
  more than ten points, and a "signal" over four tickets is one person's
  opinion with a percent sign on it.
- **The gap has to reach 25 points.** Rates over samples this size drift by ten
  on noise alone.
- **Features are yes/no.** Rates compare between groups of different sizes;
  averages do not. "Runs to 400 characters or more" is comparable, "average
  length" is one outlier away from meaningless.

## Reading a signal backwards

Sometimes a feature is commoner among the tickets that stalled. The report
prints those separately and says to read them twice, because the obvious
reading is usually wrong:

> A feature commoner among the tickets that stalled is as likely to be a
> symptom as a cause - hard problems attract long descriptions.

A board where difficult work gets thorough tickets and still takes three
attempts will show "long description" as a stall signal. Acting on that by
asking for shorter tickets would be exactly backwards.

## When it says nothing

On `astral-sh/uv`, with 44 shipped and 60 stalled, no feature cleared the gap:

> Nothing in the descriptions separates the two groups. On this board whether a
> ticket got built is not predicted by how it was written.

That is a real answer and a useful one. It says the bottleneck is somewhere
other than ticket quality, and it is worth knowing before anyone is asked to
write differently.

## Where it shows up

- `ticket-ai learn` prints the comparison after the profile.
- Findings quote both rates when the evidence supports it: a missing section
  reads *"85% of the tickets that shipped have one, against 30% of the ones
  that stalled"* rather than a single rate.
- The profile stores the section signals, so a review can cite them without
  re-mining the tracker.
