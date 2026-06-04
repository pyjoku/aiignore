# .aiignore — Specification

**Version:** 0.1 (draft)
**Status:** Convention proposal + Hermes Agent reference implementation
**License:** MIT

## 1. Purpose

`.aiignore` is a per-folder file declaring filesystem paths an AI agent must not access. Implementations enforce this by intercepting the agent's filesystem tool calls and refusing operations whose target paths match an `.aiignore` rule.

This document specifies the file format, the lookup algorithm, and the enforcement contract that conforming implementations must implement.

## 2. File location and discovery

`.aiignore` is a plain UTF-8 text file named exactly `.aiignore` placed in any directory.

For every filesystem operation on path `P`, the implementation MUST walk from `P`'s parent directory upward to the filesystem root, collecting `.aiignore` files in the order encountered (nearest-first). Each collected file's rules are evaluated against `P`, with paths interpreted **relative to the directory containing that `.aiignore` file.**

The walk terminates at:
- The filesystem root (`/` on POSIX, drive root on Windows), OR
- A directory containing a marker `.aiignore-root` file (sentinel that explicitly bounds the search — useful inside symlinks and chrooted setups).

Implementations MAY cache `.aiignore` parse results but MUST invalidate the cache when the file's mtime changes.

## 3. Pattern syntax

Each non-empty, non-comment line in `.aiignore` is one rule.

```
<rule> ::= [<mode-prefix>] [<negation>] <pattern>
```

### 3.1 Pattern (gitignore-compatible)

The pattern follows gitignore glob semantics:

| Token | Matches |
|---|---|
| `?` | Any single character (excluding `/`) |
| `*` | Zero or more characters (excluding `/`) |
| `**` | Zero or more directory levels |
| `[abc]` | One of the listed characters |
| `[!abc]` | Any character except listed |
| Literal | Itself |
| Trailing `/` | Matches directories only |
| Leading `/` | Anchors to the `.aiignore` directory's root (not the filesystem root) |

A pattern with no slash matches at any depth below the `.aiignore` file's location. A pattern containing a slash matches only at the location specified.

### 3.2 Negation

A `!` prefix negates the rule — the matched path is **allowed** even if a broader rule earlier in the file blocks it. Negation order matters: later rules override earlier ones in the same file.

A negation cannot re-include a path inside a directory blocked by a parent pattern. This matches `.gitignore` semantics: you must not block `secret/` and try to allow `!secret/exception.md`.

### 3.3 Mode prefix

An optional `[mode:read]` or `[mode:write]` prefix restricts the rule to one operation class:

| Prefix | Rule applies to |
|---|---|
| (none) | Both read and write operations |
| `[mode:read]` | Read operations only (read, glob, grep, stat) |
| `[mode:write]` | Write operations only (write, edit, patch, delete, chmod) |

The prefix is case-insensitive. Whitespace between the prefix and the pattern is permitted.

### 3.4 Comments and blank lines

Lines beginning with `#` are comments. Blank lines are ignored. Inline comments are NOT supported (a `#` mid-line is part of the pattern).

## 4. Operation classes

For the purpose of mode matching, agent tools are grouped into two classes:

**Read class** — operations that obtain content or metadata without modifying it:
- `read_file`, `read`
- `glob`, `find`
- `grep`, `search`
- `stat`, `ls`, `list_dir`
- Any tool whose effect is read-only

**Write class** — operations that modify content or metadata:
- `write_file`, `write`, `create`
- `edit_file`, `edit`, `patch`, `apply_patch`
- `delete`, `rm`, `unlink`
- `move`, `rename`
- `chmod`, `chown`
- Skill-management operations that produce or modify files

Implementations MAY define additional tool-to-class mappings appropriate to their agent's tool surface.

## 5. Matching algorithm

For a filesystem operation `op` on path `P`:

1. Determine `class = read_class(op) | write_class(op)`.
2. Normalize `P` to an absolute path with symlinks resolved. (Implementations MAY make resolved-symlinks vs. raw-path matching configurable; default is resolved.)
3. Walk from `parent(P)` upward to the filesystem root or `.aiignore-root` marker.
4. At each visited directory `D`, if `D/.aiignore` exists:
   - Parse it (or use cached result).
   - Compute `R = relative_path(P, D)`.
   - For each rule in `D/.aiignore`, in order:
     - Skip if rule's mode prefix doesn't match `class`.
     - Test rule's pattern against `R`.
     - If the rule is a negation and matches, mark "allowed" for this `.aiignore`.
     - If the rule is positive and matches, mark "blocked" for this `.aiignore`.
   - The last marker set wins for this file.
5. If any visited `.aiignore` ended in "blocked" and no nearer `.aiignore` ended in "allowed", the operation is **denied**.

## 6. Enforcement contract

When an operation is denied, the implementation MUST:

1. Refuse to execute the underlying agent tool call.
2. Return to the agent a structured result containing:
   - A clear human-readable reason ("path blocked by .aiignore rule")
   - The matched `.aiignore` file path
   - The matched rule (verbatim)
3. Append an audit record to a log file (see §7) before returning.

The agent SHOULD treat the denial as a normal tool failure and decide how to proceed (retry on a different path, ask the user, surface the issue, etc.).

## 7. Audit log

Implementations MUST write one record per denied operation to an audit log. The default location is implementation-defined (e.g. `~/.<agent>/logs/aiignore-blocks.log`). Each record contains:

- ISO-8601 timestamp (UTC)
- Tool name
- Resolved target path
- Matched `.aiignore` file
- Matched rule text
- Agent session or task ID (if available)

The log format SHOULD be one JSON object per line for machine-readability.

## 8. Modes

Implementations SHOULD provide three environmental modes:

| Mode | Behavior |
|---|---|
| **block** (default) | Refuse the operation, return denial result, log |
| **warn** | Allow the operation, append warning to tool result, log |
| **off** | Plugin loads but performs no checks (kill switch) |

Mode SHOULD be controllable via an environment variable named `<IMPL>_AIIGNORE_MODE` or equivalent.

## 9. Edge cases and implementation notes

### 9.1 Symlinks

Default: resolve symlinks before matching. This prevents bypass via a symlink from an unrestricted folder into a restricted one.

Implementations MAY offer a "raw path" mode for the rare case where a symlink is the intended access route. Document the trade-off.

### 9.2 Empty `.aiignore`

An `.aiignore` file with no rules (or only comments/blanks) is treated as if it did not exist. It does not bound the search; the walk continues to parent directories.

### 9.3 Self-reference

`.aiignore` files themselves are not protected by their own rules unless explicitly listed. The implementation MAY internally protect `.aiignore` files from being overwritten by the agent as a safety measure, even without an explicit rule — but this is RECOMMENDED, not REQUIRED.

### 9.4 Operations spanning multiple paths

For tools like `glob`, `grep`, `find` that return multiple results, the implementation MUST apply the matching algorithm to each candidate path and filter blocked entries from the result silently — OR — block the operation entirely if any blocked path matches. The chosen behavior SHOULD be configurable; default is **filter silently** to preserve agent ergonomics.

### 9.5 Bulk operations (move, copy)

For operations that involve both a source and destination, BOTH paths are checked. A blocked source OR a blocked destination causes the operation to be denied.

### 9.6 No agent tool, raw shell

If the agent has a generic shell/terminal tool, the implementation cannot intercept individual file accesses inside the shell process. Implementations SHOULD either:
- Restrict the shell tool's working directory, OR
- Statically analyze the shell command for filesystem paths and block on match, OR
- Document this limitation.

## 10. Conformance

A conforming implementation MUST:

- [ ] Parse the pattern syntax defined in §3
- [ ] Implement the matching algorithm in §5
- [ ] Refuse blocked operations as specified in §6
- [ ] Write an audit log as specified in §7
- [ ] Support at minimum the **block** and **off** modes

A conforming implementation SHOULD:

- [ ] Support the **warn** mode
- [ ] Support the `[mode:read]` / `[mode:write]` prefix
- [ ] Support the `.aiignore-root` walk terminator
- [ ] Resolve symlinks by default

## 11. Versioning

This spec follows semantic versioning. Backward-incompatible changes increment the major version. Implementations SHOULD declare the highest spec version they implement in their documentation.

## 12. Changelog

- **0.1 (2026-06)** — Initial draft.
