# SPDX-License-Identifier: MIT

"""The counting has to be right, because everything downstream is a rate over it."""

from __future__ import annotations

from ticket_ai_mcp.textstats import (
    headings,
    language,
    normalise_heading,
    quantile,
    shape,
)


class TestHeadings:
    def test_markdown(self):
        assert headings("## Problem\ntext\n### Steps\nmore") == ("Problem", "Steps")

    def test_bold_line_counts_as_a_heading(self):
        # The team that writes **Problem** has a template; a tool that only
        # looks for ## would tell them they do not.
        assert headings("**Problem**\ntext\n__Steps__\nmore") == ("Problem", "Steps")

    def test_trailing_colon_line(self):
        assert headings("Steps to reproduce:\n1. open it") == ("Steps to reproduce",)

    def test_jira_wiki_markup(self):
        assert headings("h2. Beschreibung\ntext") == ("Beschreibung",)

    def test_one_line_is_only_one_heading(self):
        # `## Steps:` matches the markdown and the colon pattern. Counted
        # twice it would inflate every section rate built on top of it.
        assert headings("## Steps:\n1. go") == ("Steps",)

    def test_a_sentence_ending_in_a_colon_is_not_a_heading(self):
        long_line = "This is a long sentence that happens to end with a colon " * 2 + ":"
        assert headings(long_line) == ()

    def test_headings_inside_code_fences_are_ignored(self):
        text = "## Real\n```\n## NotAHeading\n```\n"
        assert headings(text) == ("Real",)


class TestNormaliseHeading:
    def test_case_accents_and_punctuation_fold_together(self):
        assert normalise_heading("Akzeptanzkriterien:") == normalise_heading("AKZEPTANZKRITERIEN")
        assert normalise_heading("**Schritte**") == "schritte"

    def test_different_languages_stay_different(self):
        # Deciding these are the same is a judgement about the team, and the
        # tool reports what it counted instead of guessing.
        assert normalise_heading("Akzeptanzkriterien") != normalise_heading("Acceptance criteria")


class TestShape:
    def test_code_blocks_do_not_count_as_prose(self):
        prose = "Short problem statement."
        with_log = prose + "\n```\n" + ("stack frame line\n" * 200) + "```\n"
        assert shape(with_log).chars == shape(prose).chars
        assert shape(with_log).code_blocks == 1

    def test_lists_and_checkboxes(self):
        s = shape("- [ ] one\n- [x] two\n- three\n")
        assert s.list_items == 3
        assert s.checkboxes == 2

    def test_cross_references(self):
        s = shape("follows #412 and PROJ-7, fixed by !93")
        assert set(s.ticket_refs) == {"#412", "PROJ-7", "!93"}

    def test_a_url_is_not_a_cross_reference(self):
        assert shape("see https://example.com/a#12").ticket_refs == ()


class TestLanguage:
    def test_german(self):
        text = (
            "Wenn der Nutzer auf den Button klickt, wird die Seite nicht "
            "aktualisiert und das Formular ist danach leer."
        )
        assert language(text) == "de"

    def test_english(self):
        text = (
            "When the user clicks the button the page is not refreshed and "
            "the form that was there before is empty after that."
        )
        assert language(text) == "en"

    def test_too_short_to_tell(self):
        # A wrong guess becomes a "language_mismatch" finding in a report, so
        # thin evidence has to produce no answer rather than a coin flip.
        assert language("Etikettendruck kaputt") is None


class TestQuantile:
    def test_returns_a_real_observation(self):
        values = [10.0, 20.0, 30.0, 40.0]
        assert quantile(values, 0.25) in values

    def test_empty(self):
        assert quantile([], 0.5) == 0.0


class TestATemplatesOwnInstructions:
    """HTML comments are what the form said, not what the reporter wrote.

    Found on inkscape/inkscape, driving the MCP tools against the live board:
    the house style listed a section called `<!-- Example file` carried by 30%
    of tickets. Their template ends a comment with a colon on its own line, and
    a colon on its own line is one of the four heading shapes. Nobody can see
    that section - it is invisible in the rendered ticket - so the team is
    being credited with a convention that does not exist, and held to a length
    that includes every word of the instructions they were handed.
    """

    REAL = (
        "Operating System: Ubuntu\n\n"
        "<!-- Example file:\n"
        "Attach a sample file (or files) highlighting the issue. -->\n\n"
        "## Steps to reproduce\nOpen it."
    )

    def test_a_comment_is_not_a_section(self):
        assert shape(self.REAL).headings == ("Steps to reproduce",)

    def test_and_its_words_are_not_the_teams_words(self):
        # The instructions ran to about sixty characters on their own.
        assert shape(self.REAL).chars < 70

    def test_a_comment_inside_a_fence_is_content(self):
        # A ticket showing HTML is showing HTML, and the fence says so.
        # No colon-terminated line outside the fence, or the fixture sprouts a
        # heading of its own and the assertion stops being about comments.
        fenced = "This markup breaks it.\n\n```html\n<!-- keep me -->\n<div>x</div>\n```\n\n## Problem\nIt breaks."
        s = shape(fenced)
        assert s.code_blocks == 1
        assert s.headings == ("Problem",)

    def test_an_unclosed_comment_swallows_the_rest(self):
        # Which is exactly what it does on the board, too: somebody deleted the
        # closing marker and the ticket renders as nothing. Measuring it as
        # nothing is the honest answer, not a special case.
        broken = "Real text.\n<!-- someone deleted the closing marker\n## Not a heading any more"
        assert shape(broken).headings == ()
