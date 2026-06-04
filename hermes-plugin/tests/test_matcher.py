"""Tests for the matcher module.

Run with:  python -m pytest tests/
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make the plugin source importable when tests are run directly.
# hermes-plugin/ is the plugin folder (Hermes loads it by name); from a Python
# perspective we import matcher.py directly from that folder.
sys.path.insert(0, str(Path(__file__).parent.parent))

import matcher  # noqa: E402  -- import after sys.path tweak


# ---------------------------------------------------------------------------
# parse_aiignore
# ---------------------------------------------------------------------------


def test_parse_skips_blanks_and_comments():
    text = """
# this is a comment
patient/**

# another comment
Finanzen/**
"""
    rules = matcher.parse_aiignore(text)
    assert len(rules) == 2
    assert rules[0].pattern == "patient/**"
    assert rules[1].pattern == "Finanzen/**"


def test_parse_mode_prefix_read():
    rules = matcher.parse_aiignore("[mode:read] secret*")
    assert len(rules) == 1
    assert rules[0].mode == "read"
    assert rules[0].pattern == "secret*"


def test_parse_mode_prefix_write_case_insensitive():
    rules = matcher.parse_aiignore("[MODE:WRITE] *")
    assert rules[0].mode == "write"


def test_parse_negation():
    rules = matcher.parse_aiignore("!exception.md")
    assert rules[0].negate is True
    assert rules[0].pattern == "exception.md"


def test_parse_mode_then_negation():
    rules = matcher.parse_aiignore("[mode:write] !readme.md")
    assert rules[0].mode == "write"
    assert rules[0].negate is True
    assert rules[0].pattern == "readme.md"


# ---------------------------------------------------------------------------
# match_path — gitignore-style globs
# ---------------------------------------------------------------------------


def _rule(pattern: str) -> matcher.Rule:
    return matcher.Rule(pattern=pattern, negate=False, mode=None, raw=pattern)


def test_match_star_at_any_depth():
    r = _rule("*.md")
    assert matcher.match_path(r, "note.md")
    assert matcher.match_path(r, "subfolder/note.md")
    assert matcher.match_path(r, "a/b/c/note.md")


def test_match_anchored_path():
    r = _rule("/root-only.md")
    assert matcher.match_path(r, "root-only.md")
    assert not matcher.match_path(r, "sub/root-only.md")


def test_match_double_star_subfolder():
    r = _rule("patient/**")
    assert matcher.match_path(r, "patient/records.md")
    assert matcher.match_path(r, "patient/sub/deep/file.md")
    # The folder itself should also match if we walk to its contents
    assert matcher.match_path(r, "patient/anything")


def test_match_wildcard_does_not_cross_slash():
    r = _rule("note*.md")
    assert matcher.match_path(r, "notes.md")
    # `note*.md` is treated as "**/note*.md", so it should match at depth too
    assert matcher.match_path(r, "sub/note42.md")


def test_match_bare_star_all_files_in_folder():
    """Pattern '*' should match everything below the .aiignore directory."""
    r = _rule("*")
    assert matcher.match_path(r, "anyfile.md")
    assert matcher.match_path(r, "sub/file.md")
    assert matcher.match_path(r, "deep/sub/file.md")


def test_match_char_class():
    r = _rule("file[0-9].md")
    assert matcher.match_path(r, "file1.md")
    assert matcher.match_path(r, "sub/file9.md")
    assert not matcher.match_path(r, "fileA.md")


# ---------------------------------------------------------------------------
# evaluate_rules — mode filtering, negation order
# ---------------------------------------------------------------------------


def test_evaluate_mode_filter_skips_other_class():
    rules = [
        matcher.Rule(pattern="*", negate=False, mode="write", raw="[mode:write] *"),
    ]
    # Read operation should not be blocked by a write-only rule
    assert matcher.evaluate_rules(rules, "anything.md", "read") is None
    # Write operation IS blocked
    blocker = matcher.evaluate_rules(rules, "anything.md", "write")
    assert blocker is not None
    assert blocker.pattern == "*"


def test_evaluate_negation_overrides_earlier_block():
    rules = matcher.parse_aiignore("""
*
!keep.md
""")
    assert matcher.evaluate_rules(rules, "blocked.md", "write") is not None
    assert matcher.evaluate_rules(rules, "keep.md", "write") is None


def test_evaluate_later_block_overrides_earlier_negation():
    rules = matcher.parse_aiignore("""
!keep.md
keep.md
""")
    # Later positive rule wins
    assert matcher.evaluate_rules(rules, "keep.md", "write") is not None


# ---------------------------------------------------------------------------
# walk_and_decide — integration with the filesystem
# ---------------------------------------------------------------------------


def test_walk_blocks_inside_folder_with_star():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secret = root / "secret"
        secret.mkdir()
        (secret / ".aiignore").write_text("*\n", encoding="utf-8")

        target = secret / "passwords.txt"
        target.write_text("seekrit", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True
        assert d.rule.pattern == "*"


def test_walk_allows_unrelated_path():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "secret").mkdir()
        (root / "secret" / ".aiignore").write_text("*\n", encoding="utf-8")
        (root / "public").mkdir()
        target = root / "public" / "ok.md"
        target.write_text("hi", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is False


def test_walk_respects_mode_prefix():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        family = root / "family"
        family.mkdir()
        (family / ".aiignore").write_text(
            "[mode:write] *\n", encoding="utf-8"
        )
        target = family / "notes.md"
        target.write_text("hi", encoding="utf-8")

        # Reads should be allowed
        assert matcher.walk_and_decide(target, "read").blocked is False
        # Writes should be blocked
        d = matcher.walk_and_decide(target, "write")
        assert d.blocked is True


def test_walk_parent_rule_blocks_child():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("patient/**\n", encoding="utf-8")
        patient = root / "patient"
        patient.mkdir()
        target = patient / "deep" / "sub" / "file.md"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True
        assert d.rule.pattern == "patient/**"


def test_walk_nested_negation_overrides_parent():
    """Nearest .aiignore wins. A nearer 'allow' beats a more-distant 'block'."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("private/**\n", encoding="utf-8")
        private = root / "private"
        private.mkdir()
        # Nearer .aiignore explicitly allows public.md
        (private / ".aiignore").write_text("!public.md\n", encoding="utf-8")
        target = private / "public.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        # The nearer file's allow should win over the distant file's block
        assert d.blocked is False


def test_walk_empty_aiignore_is_skipped():
    """An empty .aiignore should not stop the walk (spec §9.2)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("secrets/**\n", encoding="utf-8")
        secrets = root / "secrets"
        secrets.mkdir()
        # Empty inner .aiignore should be treated as if absent
        (secrets / ".aiignore").write_text("\n# nothing\n", encoding="utf-8")
        target = secrets / "file.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True


def test_walk_terminator_marker_bounds_search():
    """The .aiignore-root marker should stop the walk."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Outer .aiignore that WOULD block
        (root / ".aiignore").write_text("inner/**\n", encoding="utf-8")
        inner = root / "inner"
        inner.mkdir()
        # Marker stops the walk before reaching the outer .aiignore
        (inner / ".aiignore-root").write_text("", encoding="utf-8")
        target = inner / "file.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        # Not blocked because walk stopped at the marker
        assert d.blocked is False


# ---------------------------------------------------------------------------
# classify_tool + extract_paths
# ---------------------------------------------------------------------------


def test_classify_tool_read_and_write():
    assert matcher.classify_tool("read_file") == "read"
    assert matcher.classify_tool("write_file") == "write"
    assert matcher.classify_tool("Read_File") == "read"  # case-insensitive
    assert matcher.classify_tool("patch") == "write"
    assert matcher.classify_tool("totally_unknown_tool") is None


def test_extract_paths_picks_common_keys():
    paths = matcher.extract_paths(
        "write_file", {"file_path": "/tmp/a.md"}
    )
    assert paths == [Path("/tmp/a.md")]

    paths = matcher.extract_paths(
        "move", {"source": "/tmp/a", "destination": "/tmp/b"}
    )
    assert Path("/tmp/a") in paths
    assert Path("/tmp/b") in paths


def test_extract_paths_expands_tilde():
    paths = matcher.extract_paths("read_file", {"path": "~/test.md"})
    assert paths[0] == Path(os.path.expanduser("~/test.md"))


def test_extract_paths_returns_empty_for_no_path_args():
    assert matcher.extract_paths("compute", {"a": 1, "b": 2}) == []
