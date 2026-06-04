"""Pattern matching for the .aiignore convention.

Pure-Python module — no Hermes dependencies. Easily unit-testable and reusable
for other implementations.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


__all__ = [
    "Rule",
    "Decision",
    "parse_aiignore",
    "match_path",
    "walk_and_decide",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One parsed line from an .aiignore file."""

    pattern: str
    negate: bool
    mode: Optional[str]  # None | "read" | "write"
    raw: str  # original line, for audit log


@dataclass(frozen=True)
class Decision:
    """Outcome of evaluating a path against .aiignore files."""

    blocked: bool
    rule: Optional[Rule] = None
    source_file: Optional[Path] = None

    @classmethod
    def allow(cls) -> "Decision":
        return cls(blocked=False)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


_MODE_PREFIX_RE = re.compile(r"^\[mode:(read|write)\]\s*", re.IGNORECASE)


def parse_aiignore(text: str) -> list[Rule]:
    """Parse the text of an .aiignore file into Rule objects."""
    rules: list[Rule] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        original = stripped
        mode: Optional[str] = None

        # Optional mode prefix
        m = _MODE_PREFIX_RE.match(stripped)
        if m:
            mode = m.group(1).lower()
            stripped = stripped[m.end():].lstrip()
            if not stripped:
                continue

        # Negation prefix
        negate = False
        if stripped.startswith("!"):
            negate = True
            stripped = stripped[1:].lstrip()
            if not stripped:
                continue

        rules.append(
            Rule(pattern=stripped, negate=negate, mode=mode, raw=original)
        )
    return rules


# ---------------------------------------------------------------------------
# Glob translation
# ---------------------------------------------------------------------------


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob into a compiled regex.

    Supports:
        * — any chars except /
        ** — zero or more path components
        ? — single char except /
        [...] — char class
        leading / — anchor to root of the .aiignore directory
        trailing / — directory-only (we treat as "and any subpath")
    """
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]

    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]

    # If pattern contains no slash AND isn't anchored, gitignore says it can
    # match at any depth — translate as "**/" prefix.
    has_slash = "/" in pattern
    if not anchored and not has_slash:
        pattern = "**/" + pattern

    # Walk character by character to translate, respecting ** carefully.
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            # Look ahead for **
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** — match zero or more path components
                # Consume the two stars; also consume any trailing /
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
                    out.append(r"(?:.*/)?")
                else:
                    out.append(r".*")
            else:
                out.append(r"[^/]*")
                i += 1
        elif ch == "?":
            out.append(r"[^/]")
            i += 1
        elif ch == "[":
            # Char class — copy until ]
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                cls = pattern[i:j + 1]
                # gitignore uses [!...] for negation; regex uses [^...]
                if cls.startswith("[!"):
                    cls = "[^" + cls[2:]
                out.append(cls)
                i = j + 1
        elif ch == "/":
            out.append(r"/")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1

    regex_str = "".join(out)
    if dir_only:
        regex_str += r"(?:/.*)?$"
    else:
        regex_str += r"(?:/.*)?$"

    return re.compile("^" + regex_str)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def match_path(rule: Rule, relative_path: str) -> bool:
    """Does this rule match the given path (relative to the .aiignore dir)?"""
    # Normalize: forward slashes, no leading slash
    norm = relative_path.replace("\\", "/").lstrip("/")
    regex = _pattern_to_regex(rule.pattern)
    return regex.match(norm) is not None


def evaluate_rules(
    rules: Iterable[Rule],
    relative_path: str,
    operation: str,  # "read" or "write"
) -> Optional[Rule]:
    """Evaluate all rules against the path. Returns the last matching rule
    that resulted in 'blocked' state, or None if not blocked.

    Negation rules override earlier positive matches.
    """
    last_blocking: Optional[Rule] = None
    for rule in rules:
        if rule.mode is not None and rule.mode != operation:
            continue
        if not match_path(rule, relative_path):
            continue
        if rule.negate:
            last_blocking = None
        else:
            last_blocking = rule
    return last_blocking


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


def walk_and_decide(
    target_path: Path,
    operation: str,  # "read" or "write"
    resolve_symlinks: bool = True,
) -> Decision:
    """Walk from target_path upward, collecting and applying .aiignore rules.

    Returns a Decision indicating whether the operation is blocked.
    """
    if resolve_symlinks:
        try:
            target_path = target_path.resolve()
        except (OSError, RuntimeError):
            # Couldn't resolve — fall back to raw path
            target_path = target_path.absolute()
    else:
        target_path = target_path.absolute()

    # Walk parents (nearest first)
    nearest_blocking: Optional[tuple[Rule, Path]] = None
    nearest_allowing_above_blocker: bool = False

    current = target_path.parent
    while True:
        aiignore_file = current / ".aiignore"
        if aiignore_file.is_file():
            try:
                text = aiignore_file.read_text(encoding="utf-8")
            except OSError:
                text = ""
            rules = parse_aiignore(text)
            try:
                rel = target_path.relative_to(current).as_posix()
            except ValueError:
                rel = target_path.name
            blocker = evaluate_rules(rules, rel, operation)
            if blocker is not None and nearest_blocking is None:
                nearest_blocking = (blocker, aiignore_file)
            # If this nearer .aiignore explicitly allowed (negation won),
            # blocker is None for this file, and we should NOT let a more-distant
            # block override it.
            if blocker is None and (current / ".aiignore").is_file() and nearest_blocking is None:
                # A nearer file evaluated to "allowed" — record so distant
                # blockers don't apply.
                # (Empty .aiignore files don't count: they were caught by parse
                # returning an empty rules list and evaluate_rules returning None
                # — but that's distinguishable: if rules list is empty, we treat
                # as "no .aiignore here" per spec §9.2.)
                if rules:
                    nearest_allowing_above_blocker = True

        # Walk-terminator marker
        if (current / ".aiignore-root").is_file():
            break

        parent = current.parent
        if parent == current:
            break
        current = parent

    if nearest_blocking is not None and not nearest_allowing_above_blocker:
        rule, source = nearest_blocking
        return Decision(blocked=True, rule=rule, source_file=source)

    return Decision.allow()


# ---------------------------------------------------------------------------
# Helpers for embedding implementations
# ---------------------------------------------------------------------------


READ_TOOLS: frozenset[str] = frozenset({
    "read_file",
    "read",
    "glob",
    "find",
    "grep",
    "search",
    "stat",
    "ls",
    "list_dir",
    "list_directory",
})

WRITE_TOOLS: frozenset[str] = frozenset({
    "write_file",
    "write",
    "create",
    "edit_file",
    "edit",
    "patch",
    "apply_patch",
    "delete",
    "rm",
    "unlink",
    "move",
    "rename",
    "chmod",
    "chown",
    "skill_manage",
})


def classify_tool(tool_name: str) -> Optional[str]:
    """Return 'read', 'write', or None for unknown tools."""
    name = tool_name.lower()
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    return None


def extract_paths(tool_name: str, args: dict) -> list[Path]:
    """Best-effort extraction of filesystem paths from tool arguments.

    Returns a list of Path objects; an empty list means the tool call has no
    discernible filesystem target (and should not be blocked by aiignore).
    """
    if not isinstance(args, dict):
        return []
    candidates: list[str] = []
    for key in (
        "file_path", "path", "filename", "filepath", "target", "destination",
        "source", "src", "dst", "from", "to",
    ):
        v = args.get(key)
        if isinstance(v, str) and v:
            candidates.append(v)
    return [Path(os.path.expanduser(p)) for p in candidates]
