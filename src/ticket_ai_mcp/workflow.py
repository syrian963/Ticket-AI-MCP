# SPDX-License-Identifier: MIT

"""A workflow that fills in an issue opened with only a title.

Printed by `ticket-ai models --workflow` rather than written into
`.github/workflows`, because the useful version is the one someone reads and
adapts, and a file a tool drops into a repository is one nobody reads.

It needs a model endpoint and a key in repository secrets. The keyless options
- a model on your own machine, or an assistant that already has one - are not
available to a runner, so CI is the one place where a key is unavoidable. In
secrets it at least stays off developers' laptops.
"""

from __future__ import annotations


def snippet(
    model: str = "openai/gpt-4o-mini", base_url: str = "https://openrouter.ai/api/v1"
) -> str:
    return f"""# Open an issue with a title; this writes the body in the house style,
# measures it against the team's own tickets, and posts it.
#
# Any OpenAI-compatible endpoint works - OpenRouter, Azure AI Foundry, a
# provider directly. Put its key in repository secrets as TICKET_AI_API_KEY.
name: Draft the ticket

on:
  issues:
    types: [opened]

permissions:
  contents: read
  issues: write

jobs:
  draft:
    # A title with no body is the case worth acting on. Anything else is
    # someone's actual ticket and must not be overwritten.
    if: github.event.issue.body == null || github.event.issue.body == ''
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5

      - name: Learn the house style
        env:
          TICKET_AI_TRACKER: github
          TICKET_AI_PROJECT: ${{{{ github.repository }}}}
          TICKET_AI_GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        # Cache this in a real setup; it is minutes of API calls and the
        # answer barely moves week to week.
        run: uvx ticket-ai-mcp learn --sample 80

      - name: Write it, and check what was written
        env:
          TICKET_AI_TRACKER: github
          TICKET_AI_PROJECT: ${{{{ github.repository }}}}
          TICKET_AI_GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          TICKET_AI_WRITER: openai
          TICKET_AI_BASE_URL: {base_url}
          TICKET_AI_MODEL: {model}
          TICKET_AI_API_KEY: ${{{{ secrets.TICKET_AI_API_KEY }}}}
        # --fail-under stops a draft that missed the house style from being
        # posted at all. Better an empty issue than a confidently wrong one.
        run: |
          uvx ticket-ai-mcp compose \\
            --title "${{{{ github.event.issue.title }}}}" \\
            --out draft.md \\
            --fail-under 0.5

      - name: Post it
        env:
          GH_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
        run: gh issue edit ${{{{ github.event.issue.number }}}} --body-file draft.md
"""
