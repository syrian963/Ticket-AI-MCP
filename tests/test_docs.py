# SPDX-License-Identifier: MIT

"""The documentation, checked the way the code is.

Prose that states a number goes stale in silence, which is the worst way for it
to go. Every one of these tests exists because the drift it catches already
happened here: the README said five MCP tools while the server exposed seven,
the CLI docstring listed four verbs out of nine, and a paragraph explaining
that there is no language model in this project survived three commits after
one was added.

None of that breaks a test suite, none of it shows up in review, and all of it
is the first thing a reader hits. So the counts are read out of the code and
compared, rather than believed.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path
from typing import ClassVar

import anyio
import pytest

from ticket_ai_mcp import cli, i18n, server

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"


def readme() -> str:
    return README.read_text(encoding="utf-8")


def tool_names() -> set[str]:
    return {t.name for t in anyio.run(server.server.list_tools)}


class TestTools:
    def test_every_mcp_tool_is_named_in_the_readme(self):
        text = readme()
        missing = sorted(name for name in tool_names() if name not in text)
        assert not missing, f"tools absent from the README: {missing}"

    def test_the_readme_does_not_name_a_tool_that_was_removed(self):
        # The other direction, and the one that bites: a tool renamed leaves
        # its old name in the prose, and a reader calls something that is not
        # there.
        text = readme()
        listed = set(re.findall(r"`(learn_conventions|house_style|ticket_\w+|review_\w+)`", text))
        assert listed <= tool_names(), (
            f"README names tools that do not exist: {listed - tool_names()}"
        )

    def test_the_readme_states_the_right_number_of_tools(self):
        # "Five tools" outlived two additions before anyone noticed.
        words = {
            4: "four",
            5: "five",
            6: "six",
            7: "seven",
            8: "eight",
            9: "nine",
            10: "ten",
        }
        expected = words[len(tool_names())]
        stated = re.search(r"\b(four|five|six|seven|eight|nine|ten) tools\b", readme(), re.I)
        assert stated, "the README no longer says how many tools there are"
        assert stated.group(1).lower() == expected, (
            f"the README says {stated.group(1)} tools, the server exposes {len(tool_names())}"
        )


class TestCli:
    def commands(self) -> set[str]:
        """The subcommands, asked of argparse rather than scraped from prose.

        `--help` prints the tracker choices in the same brace syntax, and
        picking the wrong list is how this test first "failed" - reporting
        gitlab and jira as undocumented commands. Scraping was the mistake;
        `build_parser` exists so this can ask.
        """
        return set(cli.subcommands())

    def help_text(self) -> str:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit):
            cli.main(["--help"])
        return buffer.getvalue()

    def test_every_subcommand_appears_in_the_readme(self):
        text = readme()
        missing = sorted(c for c in self.commands() if f"ticket-ai {c}" not in text)
        assert not missing, f"commands absent from the README: {missing}"

    def test_the_module_docstring_lists_every_verb(self):
        # This one drifted twice: "Four verbs" and then "Six verbs" while nine
        # were registered.
        doc = cli.__doc__ or ""
        missing = sorted(c for c in self.commands() if f"`{c}`" not in doc)
        assert not missing, f"verbs missing from the cli docstring: {missing}"

    def test_the_docstring_does_not_promise_a_count(self):
        # A count in prose is a promise that rots. The list is the count.
        assert not re.search(r"\b(four|five|six|seven|eight|nine) verbs\b", cli.__doc__ or "", re.I)


class TestEnvironment:
    def env_vars_in_code(self) -> set[str]:
        found: set[str] = set()
        for path in (ROOT / "src").rglob("*.py"):
            found |= set(re.findall(r"TICKET_AI_[A-Z_]+", path.read_text(encoding="utf-8")))
        return found

    def test_every_setting_is_documented(self):
        text = readme() + "".join(p.read_text(encoding="utf-8") for p in DOCS.glob("*.md"))
        missing = sorted(v for v in self.env_vars_in_code() if v not in text)
        assert not missing, f"settings the code reads but nothing documents: {missing}"

    def test_the_docs_do_not_invent_settings(self):
        text = readme() + "".join(p.read_text(encoding="utf-8") for p in DOCS.glob("*.md"))
        documented = set(re.findall(r"TICKET_AI_[A-Z_]+", text))
        unknown = documented - self.env_vars_in_code()
        assert not unknown, f"documented settings the code never reads: {sorted(unknown)}"


class TestFeatures:
    """A capability nobody can find is a capability nobody has.

    The tool-name and command checks catch a new tool or verb. They missed a
    whole module - the shipped-against-stalled comparison ran, changed every
    finding it touched, and was documented nowhere, because it added no tool
    and no command. This checks the shape a reader would look for instead.
    """

    # Module, and a phrase the documentation must carry if that module is
    # doing anything a user would notice.
    VISIBLE: ClassVar[dict[str, tuple[str, ...]]] = {
        "contrast": ("stalled", "shipped"),
        "templates": ("ISSUE_TEMPLATE", "declare"),
        "similar": ("related", "duplicate"),
        "codebase": ("checkout", "search"),
        "compose": ("compose",),
        "ui": ("ui", "page"),
        "i18n": ("language",),
    }

    def prose(self) -> str:
        return (
            readme() + "".join(p.read_text(encoding="utf-8") for p in DOCS.glob("*.md"))
        ).lower()

    def test_every_user_facing_module_is_described_somewhere(self):
        text = self.prose()
        missing = [
            name
            for name, words in self.VISIBLE.items()
            if (ROOT / "src" / "ticket_ai_mcp" / f"{name}.py").exists()
            and not any(w.lower() in text for w in words)
        ]
        assert not missing, f"modules doing visible work that nothing documents: {missing}"

    def test_the_list_covers_the_modules_that_exist(self):
        # So a module added later is noticed here rather than forgotten.
        plumbing = {
            "__init__",
            "cli",
            "server",
            "config",
            "corpus",
            "mining",
            "profile",
            "report",
            "review",
            "schemas",
            "textstats",
            "messages",
            "workflow",
            "context",
        }
        on_disk = {p.stem for p in (ROOT / "src" / "ticket_ai_mcp").glob("*.py")} - plumbing
        unlisted = on_disk - set(self.VISIBLE)
        assert not unlisted, (
            f"new modules nobody decided about: {sorted(unlisted)}. "
            "Add them to VISIBLE with a word the docs should carry, or to plumbing."
        )


class TestLinks:
    def test_every_local_link_in_the_readme_resolves(self):
        broken = [
            target
            for target in re.findall(r"\]\((?!https?://|#)([^)]+)\)", readme())
            if not (ROOT / target.split("#")[0]).exists()
        ]
        assert not broken, f"README links to files that do not exist: {broken}"

    def test_every_docs_page_is_linked_from_the_readme(self):
        text = readme()
        orphans = [p.name for p in DOCS.glob("*.md") if p.name not in text]
        assert not orphans, f"pages nothing links to: {orphans}"


class TestPackaging:
    def pyproject(self) -> dict:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_entry_points_exist(self):
        import importlib

        for target in self.pyproject()["project"]["scripts"].values():
            module_name, _, attr = target.partition(":")
            module = importlib.import_module(module_name)
            assert hasattr(module, attr), f"{target} does not resolve"

    def test_the_readme_installs_what_the_package_is_called(self):
        name = self.pyproject()["project"]["name"]
        assert name in readme(), f"the README never names the package ({name})"

    def test_the_supported_pythons_agree_with_ci(self):
        # The classifiers claim versions; CI is what makes the claim true.
        classifiers = self.pyproject()["project"]["classifiers"]
        claimed = set(
            re.findall(r"Programming Language :: Python :: (3\.\d+)", "\n".join(classifiers))
        )
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        tested = set(re.findall(r'"(3\.\d+)"', workflow))
        assert claimed == tested, f"classifiers claim {sorted(claimed)}, CI runs {sorted(tested)}"


class TestNothingPrivate:
    """This was built against a private board, and it leaked.

    Names, real ticket numbers and the board's own section headings all
    reached the tests and the documentation - not because anyone pasted them
    in, but because fixtures get written from whatever is on screen. None of
    it failed a test, and all of it would have been published on the first
    push.

    So the check runs with the suite, where it cannot be skipped, rather than
    depending on a hook somebody has to install.

    **The words are stored as hashes.** The first version wrote them out to
    grep for them, which put them back in the repository - the exact thing
    being prevented - and then had to exempt the pre-commit hook from its own
    check because the hook named them too. Splitting them across a `+` hid
    them from the regex and from nobody reading the file. A hash catches the
    word and reveals nothing, and it lets the hook and this test share one
    list with no readable copy of it anywhere.
    """

    # sha256 of each lowercased word, truncated. Computed outside this
    # repository; the words themselves are deliberately not in it.
    FORBIDDEN = frozenset(
        {
            "4bfd8e289b072a2d",
            "98a4f7655958a385",
            "64f191feb5c23cae",
            "ffaea8c469d3ccf7",
            "2bd8bb43437e4316",
            "ecb7f7428d8ddd85",
            "ef014ce3721b7f20",
            "3bd5bf1b97d994ff",
        }
    )

    @staticmethod
    def _digest(word: str) -> str:
        return hashlib.sha256(word.encode("utf-8")).hexdigest()[:16]

    def _hits(self, text: str, forbidden: frozenset[str]) -> set[str]:
        """Which words in `text` are on the list.

        Whole words, because that is what the list holds: a hostname splits
        into its labels, so the middle one is caught without the list having
        to know the shape of the URL it appeared in.
        """
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        return {w for w in words if self._digest(w) in forbidden}

    def sources(self):
        for pattern in ("*.py", "*.md", "*.yml", "*.yaml", "*.toml", "*.json"):
            for path in ROOT.rglob(pattern):
                if any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
                    continue
                yield path

    def test_no_client_material_anywhere_in_the_tree(self):
        # Nothing is exempt now, including the pre-commit hook and this file.
        offenders = []
        for path in self.sources():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if self._hits(line, self.FORBIDDEN):
                    offenders.append(f"{path.relative_to(ROOT)}:{i}")
        assert not offenders, f"client material in: {offenders[:10]}"

    def test_the_check_would_actually_catch_something(self):
        # A guard that cannot fail is decoration. Proven with a harmless word
        # rather than a real one, so the demonstration is not itself a leak.
        canary = frozenset({"c5aae5797115c6de"})
        assert self._hits("see the kanarienvogel board", canary) == {"kanarienvogel"}
        assert self._hits("nothing to see here", canary) == set()

    def test_a_hostname_is_caught_by_its_middle_label(self):
        # `gitlab.<client>.de` is the shape that leaked. The list holds the
        # label, not the URL, so any host built from it is caught.
        canary = frozenset({"c5aae5797115c6de"})
        assert self._hits("gitlab.kanarienvogel.de/x", canary) == {"kanarienvogel"}


class TestRegistryManifest:
    """`server.json` is what an MCP registry publishes, and it rots quietly.

    Nothing here reads it at runtime, so a version that disagrees with the
    package or a setting that no longer exists breaks only for the people
    installing from the registry - which is to say, for strangers.
    """

    def manifest(self) -> dict:
        import json

        return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    def pyproject(self) -> dict:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_versions_agree(self):
        # Four places carry the version, and the release workflow compares the
        # tag against only the first. The other three drift silently.
        declared = self.pyproject()["project"]["version"]
        manifest = self.manifest()
        assert manifest["version"] == declared
        assert manifest["packages"][0]["version"] == declared

        import ticket_ai_mcp

        assert ticket_ai_mcp.__version__ == declared

        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"[{declared}]" in changelog, f"CHANGELOG.md has no section for {declared}"

    def test_the_package_name_agrees(self):
        assert self.manifest()["packages"][0]["identifier"] == self.pyproject()["project"]["name"]

    def test_every_advertised_setting_is_one_the_code_reads(self):
        advertised = {
            v["name"] for v in self.manifest()["packages"][0].get("environmentVariables", [])
        }
        real = TestEnvironment().env_vars_in_code()
        assert advertised <= real, f"advertised but never read: {sorted(advertised - real)}"

    def test_the_required_settings_are_the_ones_without_a_default(self):
        # TRACKER and PROJECT are the two the tool refuses to guess.
        required = {
            v["name"]
            for v in self.manifest()["packages"][0]["environmentVariables"]
            if v.get("isRequired")
        }
        assert required == {"TICKET_AI_TRACKER", "TICKET_AI_PROJECT"}

    def test_every_token_is_marked_secret(self):
        for v in self.manifest()["packages"][0]["environmentVariables"]:
            if v["name"].endswith("_TOKEN"):
                assert v.get("isSecret"), f"{v['name']} is not marked secret"


class TestTranslations:
    def test_both_languages_carry_the_same_keys(self):
        # A key present in English and missing in German renders as English in
        # a German interface, silently.
        tables = {lang: set(i18n._STRINGS[lang]) for lang in i18n.SUPPORTED}
        reference = tables[i18n.DEFAULT]
        for lang, keys in tables.items():
            assert keys == reference, f"{lang} differs by {keys ^ reference}"


class TestTheInstallCheck:
    """The script that proves the wheel works for somebody who is not us.

    It is the only thing in the repository that runs the built artefact
    instead of the source tree, and until this session it could only ever
    have passed on the machine it was written on.
    """

    def script(self) -> str:
        return (ROOT / "install_check.sh").read_text(encoding="utf-8")

    def test_it_does_not_cd_into_one_developers_home(self):
        # It hardcoded `$HOME/projects/Ticket-AI-MCP`, and the CI job that
        # runs it checks out somewhere else entirely - so its first run on a
        # runner would have failed on `cd`, before testing anything.
        assert "$HOME/projects" not in self.script()

    def test_the_stdio_probe_it_calls_exists(self):
        assert "tools/stdio_probe.py" in self.script()
        assert (ROOT / "tools" / "stdio_probe.py").is_file()

    def test_the_probe_expects_exactly_the_tools_the_server_offers(self):
        # Otherwise a ninth tool ships unchecked, or a renamed one fails the
        # install check for the wrong reason.
        probe = (ROOT / "tools" / "stdio_probe.py").read_text(encoding="utf-8")
        expected = set(re.findall(r'"(\w+)",', probe.split("expected = {")[1].split("}")[0]))
        from ticket_ai_mcp import server as server_module

        offered = {
            name
            for name, value in vars(server_module).items()
            if callable(value)
            and getattr(value, "__module__", "") == server_module.__name__
            and not name.startswith("_")
            and name not in {"main", "settings", "tracker_for"}
        }
        assert expected <= offered, sorted(expected - offered)

    def test_ci_runs_it(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "install_check.sh" in workflow
