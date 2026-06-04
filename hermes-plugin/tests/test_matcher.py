"""Tests for matcher.py — v0.2 spec.

Run from project root:    python -m pytest
Or from this folder:      python -m pytest test_matcher.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import matcher  # noqa: E402


# ---------------------------------------------------------------------------
# parse_aiignore — gitignore syntax, no mode prefixes anymore
# ---------------------------------------------------------------------------


def test_parse_ignore_skips_blanks_and_comments():
    rules = matcher.parse_aiignore("""
# comment
patient/**

# another
Finanzen/**
""")
    assert len(rules) == 2
    assert rules[0].pattern == "patient/**"
    assert rules[1].pattern == "Finanzen/**"


def test_parse_ignore_negation():
    rules = matcher.parse_aiignore("!exception.md")
    assert rules[0].negate is True
    assert rules[0].pattern == "exception.md"


def test_parse_ignore_no_longer_supports_mode_prefix():
    """v0.2: [mode:read] is just text — not a special prefix.

    A line like '[mode:read] secret*' is parsed as a literal pattern starting
    with '['. It will simply not match real paths the user cares about.
    """
    rules = matcher.parse_aiignore("[mode:read] secret*")
    assert len(rules) == 1
    # The pattern is taken verbatim
    assert rules[0].pattern.startswith("[mode:read]")


# ---------------------------------------------------------------------------
# parse_aiattributes — gitattributes-style suffix tokens
# ---------------------------------------------------------------------------


def test_parse_attributes_simple_bare_flag():
    rules = matcher.parse_aiattributes("*.pdf readonly")
    assert len(rules) == 1
    assert rules[0].pattern == "*.pdf"
    assert rules[0].attributes == (("readonly", None),)


def test_parse_attributes_keyvalue():
    rules = matcher.parse_aiattributes("/skills/** tool=obsidian-cli")
    assert rules[0].pattern == "/skills/**"
    assert rules[0].attributes == (("tool", "obsidian-cli"),)


def test_parse_attributes_multiple_on_one_line():
    rules = matcher.parse_aiattributes("*.lock readonly audit=high")
    attrs = dict(rules[0].attributes)
    assert attrs["readonly"] is None
    assert attrs["audit"] == "high"


def test_parse_attributes_negation_marker():
    rules = matcher.parse_aiattributes("/exception/** -readonly")
    assert rules[0].attributes == (("readonly", ""),)


def test_parse_attributes_skips_blanks_and_comments():
    rules = matcher.parse_aiattributes("""
# header
*.pdf readonly

# section
/bank/** readonly audit=high
""")
    assert len(rules) == 2


def test_parse_attributes_pattern_only_is_skipped():
    """A line with only a pattern (no attribute) is not a valid rule."""
    rules = matcher.parse_aiattributes("*.pdf")
    assert rules == []


# ---------------------------------------------------------------------------
# match_path — gitignore globs
# ---------------------------------------------------------------------------


def test_match_path_star_at_any_depth():
    assert matcher.match_path("*.md", "note.md")
    assert matcher.match_path("*.md", "sub/note.md")


def test_match_path_anchored():
    assert matcher.match_path("/root-only.md", "root-only.md")
    assert not matcher.match_path("/root-only.md", "sub/root-only.md")


def test_match_path_double_star():
    assert matcher.match_path("patient/**", "patient/records.md")
    assert matcher.match_path("patient/**", "patient/sub/deep/file.md")


def test_match_path_bare_star_matches_everything():
    assert matcher.match_path("*", "anything.md")
    assert matcher.match_path("*", "sub/deep/file.md")


def test_match_path_char_class():
    assert matcher.match_path("file[0-9].md", "file1.md")
    assert not matcher.match_path("file[0-9].md", "fileA.md")


# ---------------------------------------------------------------------------
# evaluate_ignore_rules — negation order
# ---------------------------------------------------------------------------


def test_evaluate_ignore_block():
    rules = matcher.parse_aiignore("patient/**")
    blocker = matcher.evaluate_ignore_rules(rules, "patient/x.md")
    assert blocker is not None


def test_evaluate_ignore_negation_overrides_earlier_block():
    rules = matcher.parse_aiignore("""
*
!keep.md
""")
    assert matcher.evaluate_ignore_rules(rules, "blocked.md") is not None
    assert matcher.evaluate_ignore_rules(rules, "keep.md") is None


def test_evaluate_ignore_later_block_overrides_earlier_negation():
    rules = matcher.parse_aiignore("""
!keep.md
keep.md
""")
    assert matcher.evaluate_ignore_rules(rules, "keep.md") is not None


# ---------------------------------------------------------------------------
# walk_and_decide — Phase 1: .aiignore (absolute block)
# ---------------------------------------------------------------------------


def test_walk_aiignore_blocks_read():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secret = root / "secret"
        secret.mkdir()
        (secret / ".aiignore").write_text("*\n", encoding="utf-8")
        target = secret / "passwords.txt"
        target.write_text("seekrit", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True
        assert d.kind == "ignore"


def test_walk_aiignore_blocks_write_too():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secret = root / "secret"
        secret.mkdir()
        (secret / ".aiignore").write_text("*\n", encoding="utf-8")
        target = secret / "out.txt"

        d = matcher.walk_and_decide(target, "write")
        assert d.blocked is True
        assert d.kind == "ignore"


def test_walk_aiignore_allows_unrelated():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        secret = root / "secret"
        secret.mkdir()
        (secret / ".aiignore").write_text("*\n", encoding="utf-8")
        (root / "public").mkdir()
        target = root / "public" / "ok.md"
        target.write_text("hi", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is False


def test_walk_nested_aiignore_negation_overrides_parent():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("private/**\n", encoding="utf-8")
        private = root / "private"
        private.mkdir()
        (private / ".aiignore").write_text("!public.md\n", encoding="utf-8")
        target = private / "public.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is False


def test_walk_aiignore_root_marker_bounds_search():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("inner/**\n", encoding="utf-8")
        inner = root / "inner"
        inner.mkdir()
        (inner / ".aiignore-root").write_text("", encoding="utf-8")
        target = inner / "file.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is False


# ---------------------------------------------------------------------------
# walk_and_decide — Phase 2: .aiattributes standard attributes
# ---------------------------------------------------------------------------


def test_walk_attribute_readonly_blocks_write():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        family = root / "family"
        family.mkdir()
        (family / ".aiattributes").write_text(
            "* readonly\n", encoding="utf-8"
        )
        target = family / "notes.md"
        target.write_text("x", encoding="utf-8")

        # Read allowed
        assert matcher.walk_and_decide(target, "read").blocked is False
        # Write blocked
        d = matcher.walk_and_decide(target, "write")
        assert d.blocked is True
        assert d.kind == "attribute"
        assert d.attribute == "readonly"


def test_walk_attribute_writeonly_blocks_read():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inbox = root / "inbox"
        inbox.mkdir()
        (inbox / ".aiattributes").write_text(
            "* writeonly\n", encoding="utf-8"
        )
        target = inbox / "draft.md"
        target.write_text("x", encoding="utf-8")

        assert matcher.walk_and_decide(target, "write").blocked is False
        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True
        assert d.attribute == "writeonly"


def test_walk_attribute_noaccess_blocks_both():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vault = root / "vault"
        vault.mkdir()
        (vault / ".aiattributes").write_text(
            "* noaccess\n", encoding="utf-8"
        )
        target = vault / "x.md"
        target.write_text("x", encoding="utf-8")

        assert matcher.walk_and_decide(target, "read").blocked is True
        assert matcher.walk_and_decide(target, "write").blocked is True


def test_walk_attribute_pattern_specific():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiattributes").write_text(
            "*.pdf readonly\n", encoding="utf-8"
        )
        pdf = root / "doc.pdf"
        pdf.write_text("fake pdf", encoding="utf-8")
        md = root / "note.md"
        md.write_text("x", encoding="utf-8")

        # PDF write blocked
        assert matcher.walk_and_decide(pdf, "write").blocked is True
        # PDF read allowed
        assert matcher.walk_and_decide(pdf, "read").blocked is False
        # MD anything allowed
        assert matcher.walk_and_decide(md, "write").blocked is False
        assert matcher.walk_and_decide(md, "read").blocked is False


def test_walk_attribute_nearest_wins():
    """A nearer .aiattributes overrides a more-distant one for the same attr."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Distant says readonly
        (root / ".aiattributes").write_text(
            "/sub/** readonly\n", encoding="utf-8"
        )
        sub = root / "sub"
        sub.mkdir()
        # Nearer unsets readonly
        (sub / ".aiattributes").write_text(
            "* -readonly\n", encoding="utf-8"
        )
        target = sub / "x.md"
        target.write_text("x", encoding="utf-8")

        # Nearer file unset readonly → write should be allowed
        assert matcher.walk_and_decide(target, "write").blocked is False


# ---------------------------------------------------------------------------
# walk_and_decide — Phase 2: tool= extension
# ---------------------------------------------------------------------------


def test_walk_attribute_tool_restriction_blocks_other_tool():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skills = root / "skills"
        skills.mkdir()
        (skills / ".aiattributes").write_text(
            "* tool=obsidian-cli\n", encoding="utf-8"
        )
        target = skills / "x.md"
        target.write_text("x", encoding="utf-8")

        # Calling with the wrong tool → blocked
        d = matcher.walk_and_decide(
            target, "write", tool_name="write_file"
        )
        assert d.blocked is True
        assert d.attribute.startswith("tool=")

        # Calling with the right tool → allowed
        d = matcher.walk_and_decide(
            target, "write", tool_name="obsidian-cli"
        )
        assert d.blocked is False


def test_walk_unknown_attribute_is_ignored():
    """Per spec §7: unknown attributes don't block."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiattributes").write_text(
            "* some-custom-vendor=value\n", encoding="utf-8"
        )
        target = root / "x.md"
        target.write_text("x", encoding="utf-8")

        # Unknown attribute alone doesn't block
        assert matcher.walk_and_decide(target, "read").blocked is False
        assert matcher.walk_and_decide(target, "write").blocked is False


# ---------------------------------------------------------------------------
# Phase order: .aiignore beats .aiattributes
# ---------------------------------------------------------------------------


def test_walk_aiignore_beats_aiattributes_negation():
    """An .aiignore block stands even if .aiattributes would have allowed."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".aiignore").write_text("private/**\n", encoding="utf-8")
        (root / ".aiattributes").write_text(
            "private/** -readonly\n", encoding="utf-8"
        )
        private = root / "private"
        private.mkdir()
        target = private / "x.md"
        target.write_text("x", encoding="utf-8")

        d = matcher.walk_and_decide(target, "read")
        assert d.blocked is True
        assert d.kind == "ignore"  # not "attribute"


# ---------------------------------------------------------------------------
# classify_tool + extract_paths (unchanged from v0.1)
# ---------------------------------------------------------------------------


def test_classify_tool_read_and_write():
    assert matcher.classify_tool("read_file") == "read"
    assert matcher.classify_tool("write_file") == "write"
    assert matcher.classify_tool("Patch") == "write"
    assert matcher.classify_tool("totally_unknown") is None


def test_extract_paths_picks_common_keys():
    paths = matcher.extract_paths("write_file", {"file_path": "/tmp/a.md"})
    assert paths == [Path("/tmp/a.md")]

    paths = matcher.extract_paths(
        "move", {"source": "/tmp/a", "destination": "/tmp/b"}
    )
    assert Path("/tmp/a") in paths
    assert Path("/tmp/b") in paths


def test_extract_paths_expands_tilde():
    paths = matcher.extract_paths("read_file", {"path": "~/x.md"})
    assert paths[0] == Path(os.path.expanduser("~/x.md"))
