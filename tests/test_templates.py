# SPDX-License-Identifier: MIT

"""Reading the form a project declares, and lining it up against reality.

Every fixture here is the shape of a real issue form - the GitHub YAML one,
the markdown one, and GitLab's. The parser is hand-rolled rather than a YAML
dependency, so it earns closer testing than a library call would.
"""

from __future__ import annotations

from conftest import GOOD_BODY, make_detail, make_ticket

from ticket_ai_mcp.mining import pick
from ticket_ai_mcp.profile import build
from ticket_ai_mcp.report import render_gaps
from ticket_ai_mcp.templates import compare, read

# The shape GitHub issue forms take, down to the nesting.
BUG_FORM = """name: Bug
description: Report a bug
body:
  - type: markdown
    attributes:
      value: Thanks for taking the time.
  - type: checkboxes
    id: checks
    attributes:
      label: Initial Checks
      options:
        - label: I am on the latest version
          required: true
    validations:
      required: true
  - type: textarea
    id: description
    attributes:
      label: Description
    validations:
      required: true
  - type: textarea
    id: example
    attributes:
      label: Example Code
  - type: input
    id: version
    attributes:
      label: Python & OS Version
    validations:
      required: true
"""

FEATURE_FORM = """name: Feature
body:
  - type: textarea
    id: description
    attributes:
      label: Description
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
"""

MARKDOWN_TEMPLATE = """---
name: Bug report
about: Something is broken
---

## Steps to reproduce

## Expected behaviour

**Environment**
"""


def corpus(bodies: list[str]):
    details = [
        make_detail(make_ticket(f"#{i}", description=b, labels=("bug",), author=f"dev{i % 5}"))
        for i, b in enumerate(bodies)
    ]
    taken, _ = pick(details, want=len(details))
    return build(taken, project="acme/shop", tracker="github")


def write_forms(tmp_path, folder=".github/ISSUE_TEMPLATE", **files):
    target = tmp_path / folder
    target.mkdir(parents=True)
    for name, text in files.items():
        (target / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return tmp_path


class TestParsing:
    def test_a_github_form_yields_labels_and_requiredness(self, tmp_path):
        root = write_forms(tmp_path, bug__yml=BUG_FORM)
        declared = read(root)
        by_label = {f.label: f for f in declared.fields}
        assert set(by_label) == {
            "Initial Checks",
            "Description",
            "Example Code",
            "Python & OS Version",
        }
        assert by_label["Description"].required
        assert not by_label["Example Code"].required
        assert declared.files == (".github/ISSUE_TEMPLATE/bug.yml",)

    def test_two_forms_merge_and_required_wins(self, tmp_path):
        # `Description` is required in the bug form and required in the feature
        # form; a field required anywhere is required.
        root = write_forms(tmp_path, bug__yml=BUG_FORM, feature__yml=FEATURE_FORM)
        declared = read(root)
        labels = [f.label for f in declared.fields]
        assert labels.count("Description") == 1
        assert declared.field("description").required
        assert declared.field("alternatives considered") is not None

    def test_config_yml_asks_for_nothing_and_is_skipped(self, tmp_path):
        root = write_forms(
            tmp_path,
            bug__yml=BUG_FORM,
            config__yml="blank_issues_enabled: false\ncontact_links:\n  - name: Discord\n",
        )
        assert all("Discord" not in f.label for f in read(root).fields)

    def test_a_markdown_template_gives_headings_and_no_requiredness(self, tmp_path):
        root = write_forms(tmp_path, bug__md=MARKDOWN_TEMPLATE)
        declared = read(root)
        labels = {f.label for f in declared.fields}
        assert {"Steps to reproduce", "Expected behaviour", "Environment"} <= labels
        # Markdown declares no requiredness; inventing one would put words in
        # the project's mouth.
        assert not any(f.required for f in declared.fields)

    def test_gitlab_templates_are_found_too(self, tmp_path):
        root = write_forms(tmp_path, folder=".gitlab/issue_templates", Bug__md=MARKDOWN_TEMPLATE)
        assert read(root).files == (".gitlab/issue_templates/Bug.md",)

    def test_no_templates_is_an_answer_not_a_crash(self, tmp_path):
        assert read(tmp_path).fields == ()
        assert read(tmp_path / "nope").files == ()


class TestComparison:
    def test_a_required_field_nobody_fills_in_is_surfaced(self, tmp_path):
        # The finding that matters: the form asks, the tickets do not answer.
        body = "## Description\n" + ("Real content about the problem. " * 20)
        profile = corpus([body] * 12)
        declared = read(write_forms(tmp_path, bug__yml=BUG_FORM))
        gaps, _ = compare(declared, profile)

        by_label = {g.label: g for g in gaps}
        assert by_label["Description"].rate == 1.0
        assert by_label["Initial Checks"].rate == 0.0
        assert by_label["Initial Checks"].required
        assert by_label["Initial Checks"].is_ignored
        assert by_label["Initial Checks"].declared_only

    def test_a_habit_no_form_declares_is_surfaced(self, tmp_path):
        # The other direction, and the more interesting one: a convention the
        # project grew and never wrote down.
        body = (
            "## Description\n"
            + ("Real content about the problem. " * 15)
            + "\n## Workaround\nRestart it.\n"
        )
        profile = corpus([body] * 12)
        declared = read(write_forms(tmp_path, bug__yml=BUG_FORM))
        _, undeclared = compare(declared, profile)
        assert "Workaround" in undeclared

    def test_a_rare_habit_is_not_called_a_convention(self, tmp_path):
        bodies = ["## Description\n" + ("Real content about the problem. " * 15) for _ in range(11)]
        bodies.append(bodies[0] + "\n## Nur Einmal\nx\n")
        profile = corpus(bodies)
        declared = read(write_forms(tmp_path, bug__yml=BUG_FORM))
        _, undeclared = compare(declared, profile)
        assert "Nur Einmal" not in undeclared


class TestReport:
    def test_it_names_the_files_it_read(self, tmp_path):
        profile = corpus([GOOD_BODY] * 12)
        declared = read(write_forms(tmp_path, bug__yml=BUG_FORM))
        gaps, undeclared = compare(declared, profile)
        text = render_gaps(declared, gaps, undeclared, profile.sample_size)
        assert ".github/ISSUE_TEMPLATE/bug.yml" in text
        assert "Asked for, and mostly not supplied" in text

    def test_no_template_says_where_it_looked(self, tmp_path):
        profile = corpus([GOOD_BODY] * 12)
        declared = read(tmp_path)
        gaps, undeclared = compare(declared, profile)
        text = render_gaps(declared, gaps, undeclared, profile.sample_size)
        assert "No issue template found" in text
        assert "ISSUE_TEMPLATE" in text


class TestRealIssueForms:
    """Shapes taken from templates that are live on GitHub today.

    The parser is hand-rolled, and it had only ever been read by fixtures the
    same person wrote. Pointing it at five real projects found two defects in
    one run.
    """

    def test_validations_before_attributes_still_marks_it_required(self, tmp_path):
        # home-assistant/core, verbatim shape. The first version assumed
        # `required` always came after its `label`; theirs comes before, so
        # every mandatory field on the form this project's own docs cite as
        # "mandatory" was being read as optional.
        form = tmp_path / ".github/ISSUE_TEMPLATE"
        form.mkdir(parents=True)
        (form / "bug_report.yml").write_text(
            "name: Report an issue\n"
            "body:\n"
            "  - type: markdown\n"
            "    attributes:\n"
            "      value: |\n"
            "        This issue form is for reporting bugs only!\n"
            "  - type: textarea\n"
            "    validations:\n"
            "      required: true\n"
            "    attributes:\n"
            "      label: The problem\n"
            "      description: Describe the issue.\n"
            "  - type: input\n"
            "    attributes:\n"
            "      label: What was the last working version?\n",
            encoding="utf-8",
        )
        found = read(tmp_path)
        by_label = {f.label: f.required for f in found.fields}
        assert by_label["The problem"] is True
        assert by_label["What was the last working version?"] is False

    def test_the_other_order_still_works(self, tmp_path):
        # pypa/pip writes attributes first. Both orders are valid YAML and
        # both are in use, so neither may be the one that works.
        form = tmp_path / ".github/ISSUE_TEMPLATE"
        form.mkdir(parents=True)
        (form / "bug-report.yml").write_text(
            "body:\n"
            "  - type: textarea\n"
            "    attributes:\n"
            "      label: Description\n"
            "    validations:\n"
            "      required: true\n",
            encoding="utf-8",
        )
        assert read(tmp_path).fields[0].required is True

    def test_a_template_that_declares_nothing_is_still_a_template(self, tmp_path):
        # denoland/deno: front matter and a single line, no headings. Skipping
        # it made the report say "no issue template found in the checkout"
        # about a repository carrying two of them.
        form = tmp_path / ".github/ISSUE_TEMPLATE"
        form.mkdir(parents=True)
        (form / "bug_report.md").write_text(
            "---\nname: Bug Report\nabout: Report an issue.\n---\n\nVersion: Deno x.x.x\n",
            encoding="utf-8",
        )
        found = read(tmp_path)
        assert found.files == (".github/ISSUE_TEMPLATE/bug_report.md",)
        assert found.fields == ()

    def test_config_yml_is_still_not_a_template(self, tmp_path):
        # It routes people to discussions and asks for nothing.
        form = tmp_path / ".github/ISSUE_TEMPLATE"
        form.mkdir(parents=True)
        (form / "config.yml").write_text("blank_issues_enabled: false\n", encoding="utf-8")
        assert read(tmp_path).files == ()
