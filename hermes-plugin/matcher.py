"""Pattern matching for the .aiignore + .aiattributes convention (v0.2).

Pure-Python module — no Hermes dependencies. Easily unit-testable and reusable
for other implementations.

Two files, two parsers, one walk:

* ``.aiignore``    — absolute block (gitignore syntax, no mode prefixes)
* ``.aiattributes`` — modulated behavior (gitattributes suffix-style)

Standard attributes: ``readonly``, ``writeonly``, ``noaccess``.
Extension attributes use ``name=value`` form; unknown attributes are ignored.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


__all__ = [
    "Rule",
    "AttrRule",
    "Decision",
    "AttributeSet",
    "parse_aiignore",
    "parse_aiattributes",
    "match_path",
    "evaluate_ignore_rules",
    "collect_attributes",
    "walk_and_decide",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "classify_tool",
    "extract_paths",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """One parsed line from an .aiignore file."""

    pattern: str
    negate: bool
    raw: str


@dataclass(frozen=True)
class AttrRule:
    """One parsed line from an .aiattributes file.

    `attributes` is a tuple of (name, value) where value is None for bare
    flags, the string value for `name=value`, or the sentinel "" for
    `-name` (unset).
    """

    pattern: str
    attributes: tuple[tuple[str, Optional[str]], ...]
    raw: str


@dataclass(frozen=True)
class Decision:
    """Outcome of evaluating a path. `kind` is 'ignore', 'attribute', or
    None (allowed)."""

    blocked: bool
    kind: Optional[str] = None  # 'ignore' | 'attribute' | None
    rule_raw: Optional[str] = None
    attribute: Optional[str] = None  # for attribute blocks, the triggering attr
    source_file: Optional[Path] = None

    @classmethod
    def allow(cls) -> "Decision":
        return cls(blocked=False)


@dataclass
class AttributeSet:
    """Collected attributes for a path, after walking and resolving precedence."""

    flags: dict[str, str] = field(default_factory=dict)
    sources: dict[str, tuple[str, Path]] = field(default_factory=dict)

    def get(self, name: str) -> Optional[str]:
        return self.flags.get(name)

    def has(self, name: str) -> bool:
        return name in self.flags

    def source_of(self, name: str) -> Optional[tuple[str, Path]]:
        return self.sources.get(name)


# ---------------------------------------------------------------------------
# Parsing — .aiignore
# ---------------------------------------------------------------------------


def parse_aiignore(text: str) -> list[Rule]:
    """Parse .aiignore text into Rule objects. gitignore syntax."""
    rules: list[Rule] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        original = stripped
        negate = False
        if stripped.startswith("!"):
            negate = True
            stripped = stripped[1:].lstrip()
            if not stripped:
                continue
        rules.append(Rule(pattern=stripped, negate=negate, raw=original))
    return rules


# ---------------------------------------------------------------------------
# Parsing — .aiattributes
# ---------------------------------------------------------------------------


def _parse_attribute_token(tok: str) -> Optional[tuple[str, Optional[str]]]:
    """Parse one attribute token. Returns (name, value) or None if malformed.

    Encoding of value:
      None        — bare flag (e.g. ``readonly``)
      ""          — unset / negation marker (e.g. ``-readonly``)
      "<string>"  — typed value (e.g. ``tool=obsidian-cli``)
    """
    tok = tok.strip()
    if not tok:
        return None
    if tok.startswith("-"):
        name = tok[1:]
        if not name:
            return None
        return (name, "")  # empty-string value means "unset"
    if "=" in tok:
        name, _, value = tok.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            return None
        return (name, value)
    return (tok, None)


def parse_aiattributes(text: str) -> list[AttrRule]:
    """Parse .aiattributes text. gitattributes-style: pattern followed by
    whitespace-separated attribute tokens."""
    rules: list[AttrRule] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # shlex so values can be quoted: pattern="some path" attr="with spaces"
        try:
            tokens = shlex.split(stripped, posix=True)
        except ValueError:
            # malformed quoting — skip the line
            continue
        if len(tokens) < 2:
            continue

        pattern = tokens[0]
        attr_tokens = tokens[1:]
        attributes: list[tuple[str, Optional[str]]] = []
        for tok in attr_tokens:
            parsed = _parse_attribute_token(tok)
            if parsed is not None:
                attributes.append(parsed)
        if not attributes:
            continue
        rules.append(
            AttrRule(
                pattern=pattern,
                attributes=tuple(attributes),
                raw=stripped,
            )
        )
    return rules


# ---------------------------------------------------------------------------
# Glob translation (shared by both files)
# ---------------------------------------------------------------------------


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate gitignore-style glob → compiled regex."""
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern[1:]

    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern[:-1]

    has_slash = "/" in pattern
    if not anchored and not has_slash:
        pattern = "**/" + pattern

    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
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
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(ch))
                i += 1
            else:
                cls = pattern[i:j + 1]
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
    regex_str += r"(?:/.*)?$"
    return re.compile("^" + regex_str)


def match_path(pattern: str, relative_path: str) -> bool:
    norm = relative_path.replace("\\", "/").lstrip("/")
    return _pattern_to_regex(pattern).match(norm) is not None


# ---------------------------------------------------------------------------
# Evaluation — .aiignore
# ---------------------------------------------------------------------------


def evaluate_ignore_rules(
    rules: Iterable[Rule],
    relative_path: str,
) -> Optional[Rule]:
    """Return the rule that finally blocks the path, or None if not blocked."""
    last_blocking: Optional[Rule] = None
    for rule in rules:
        if not match_path(rule.pattern, relative_path):
            continue
        last_blocking = None if rule.negate else rule
    return last_blocking


# ---------------------------------------------------------------------------
# Evaluation — .aiattributes
# ---------------------------------------------------------------------------


def apply_attribute_rules_to_set(
    rules: Iterable[AttrRule],
    relative_path: str,
    aiattr_source: Path,
    aset: AttributeSet,
    *,
    only_set_if_absent: bool = False,
) -> None:
    """Apply matching rules from one .aiattributes file to the AttributeSet.

    When ``only_set_if_absent`` is True, an attribute already present in
    ``aset`` is not overwritten — this implements "nearest .aiattributes wins"
    when the walk processes files in nearest-first order.
    """
    for rule in rules:
        if not match_path(rule.pattern, relative_path):
            continue
        for name, value in rule.attributes:
            if only_set_if_absent and name in aset.flags:
                continue
            if value == "":  # unset / negation
                aset.flags.pop(name, None)
                aset.sources.pop(name, None)
                continue
            aset.flags[name] = value if value is not None else ""
            aset.sources[name] = (rule.raw, aiattr_source)


# ---------------------------------------------------------------------------
# Walking
# ---------------------------------------------------------------------------


def collect_attributes(
    target_path: Path,
    *,
    resolve_symlinks: bool = True,
) -> AttributeSet:
    """Walk from target_path upward, collecting .aiattributes rules into
    an AttributeSet.

    Walk discovery order is target → root, but rules are applied in
    root → target order so that nearer rules (including ``-attr`` unset
    markers) override distant ones. This matches gitattributes'
    "last match wins" semantics.
    """
    if resolve_symlinks:
        try:
            target_path = target_path.resolve()
        except (OSError, RuntimeError):
            target_path = target_path.absolute()
    else:
        target_path = target_path.absolute()

    # Discover containing directories from target upward.
    walk_dirs: list[Path] = []
    current = target_path.parent
    while True:
        walk_dirs.append(current)
        if (current / ".aiignore-root").is_file():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Apply rules root-first so nearer files overwrite distant ones.
    aset = AttributeSet()
    for current in reversed(walk_dirs):
        aiattr_file = current / ".aiattributes"
        if not aiattr_file.is_file():
            continue
        try:
            text = aiattr_file.read_text(encoding="utf-8")
        except OSError:
            text = ""
        rules = parse_aiattributes(text)
        try:
            rel = target_path.relative_to(current).as_posix()
        except ValueError:
            rel = target_path.name
        apply_attribute_rules_to_set(rules, rel, aiattr_file, aset)

    return aset


def walk_and_decide(
    target_path: Path,
    operation: str,  # "read" or "write"
    *,
    tool_name: Optional[str] = None,
    resolve_symlinks: bool = True,
) -> Decision:
    """Full evaluation: .aiignore first (absolute block), then .aiattributes.

    Returns a Decision. tool_name is used for the `tool=` extension attribute.
    """
    if resolve_symlinks:
        try:
            target_path = target_path.resolve()
        except (OSError, RuntimeError):
            target_path = target_path.absolute()
    else:
        target_path = target_path.absolute()

    # ----- Phase 1: .aiignore -----
    nearest_ignore_block: Optional[tuple[Rule, Path]] = None
    nearest_allow_present: bool = False

    current = target_path.parent
    while True:
        aiignore_file = current / ".aiignore"
        if aiignore_file.is_file():
            try:
                text = aiignore_file.read_text(encoding="utf-8")
            except OSError:
                text = ""
            rules = parse_aiignore(text)
            if rules:
                try:
                    rel = target_path.relative_to(current).as_posix()
                except ValueError:
                    rel = target_path.name
                blocker = evaluate_ignore_rules(rules, rel)
                if blocker is not None and nearest_ignore_block is None:
                    nearest_ignore_block = (blocker, aiignore_file)
                elif blocker is None and nearest_ignore_block is None:
                    # A nearer file evaluated to "allowed" — record so distant
                    # blocks don't override it.
                    nearest_allow_present = True

        if (current / ".aiignore-root").is_file():
            break

        parent = current.parent
        if parent == current:
            break
        current = parent

    if nearest_ignore_block is not None and not nearest_allow_present:
        rule, source = nearest_ignore_block
        return Decision(
            blocked=True,
            kind="ignore",
            rule_raw=rule.raw,
            source_file=source,
        )

    # ----- Phase 2: .aiattributes -----
    aset = collect_attributes(target_path, resolve_symlinks=False)

    # Standard attributes
    if aset.has("noaccess"):
        rule_raw, source = aset.source_of("noaccess") or ("noaccess", Path())
        return Decision(
            blocked=True,
            kind="attribute",
            rule_raw=rule_raw,
            attribute="noaccess",
            source_file=source,
        )

    if operation == "write" and aset.has("readonly"):
        rule_raw, source = aset.source_of("readonly") or ("readonly", Path())
        return Decision(
            blocked=True,
            kind="attribute",
            rule_raw=rule_raw,
            attribute="readonly",
            source_file=source,
        )

    if operation == "read" and aset.has("writeonly"):
        rule_raw, source = aset.source_of("writeonly") or ("writeonly", Path())
        return Decision(
            blocked=True,
            kind="attribute",
            rule_raw=rule_raw,
            attribute="writeonly",
            source_file=source,
        )

    # Extension: tool=<name>
    required_tool = aset.get("tool")
    if required_tool and tool_name and required_tool != tool_name:
        rule_raw, source = aset.source_of("tool") or (
            f"tool={required_tool}",
            Path(),
        )
        return Decision(
            blocked=True,
            kind="attribute",
            rule_raw=rule_raw,
            attribute=f"tool={required_tool}",
            source_file=source,
        )

    return Decision.allow()


# ---------------------------------------------------------------------------
# Tool classification + path extraction
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
    name = tool_name.lower()
    if name in READ_TOOLS:
        return "read"
    if name in WRITE_TOOLS:
        return "write"
    return None


def extract_paths(tool_name: str, args: dict) -> list[Path]:
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
