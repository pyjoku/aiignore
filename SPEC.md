# .aiignore + .aiattributes — Specification

**Version:** 0.2 (draft)
**Status:** Convention proposal + Hermes Agent reference implementation
**License:** MIT

## 1. Purpose

Two folder-local files declare AI-agent access boundaries to the surrounding filesystem:

- **`.aiignore`** — paths the agent must **not access at all** (read or write). Absolute block. Pattern-based, gitignore-syntax.
- **`.aiattributes`** — paths whose access is **modulated** (read-only, write-only, tool-restricted, etc.). Pattern + attributes, gitattributes-syntax.

The split mirrors git's separation:

| git's tool | what it does | our equivalent |
|---|---|---|
| `.gitignore` | exclude paths from tracking | `.aiignore` — exclude from AI access |
| `.gitattributes` | set per-pattern attributes that modify behavior | `.aiattributes` — set per-pattern attributes that constrain AI behavior |

Implementations enforce both files by intercepting the agent's filesystem tool calls and refusing operations that violate the rules.

This document specifies file formats, lookup algorithm, and the enforcement contract.

## 2. File discovery and walk algorithm

Both `.aiignore` and `.aiattributes` are plain UTF-8 text files placed in any directory.

For every filesystem operation on path `P`, the implementation MUST walk from `P`'s parent directory upward to the filesystem root, collecting both files in the order encountered (nearest-first). Each collected file's rules are evaluated against `P`, with paths interpreted **relative to the directory containing that file**.

The walk terminates at:
- The filesystem root (`/` on POSIX, drive root on Windows), OR
- A directory containing a marker `.aiignore-root` file.

Implementations MAY cache parse results; they MUST invalidate the cache when the file's mtime changes.

## 3. Evaluation order

For each tool call:

1. **Walk and check `.aiignore` first.** If any rule blocks the path, the operation is refused immediately with an `ignore` reason. Stop.
2. **Walk and check `.aiattributes`.** Collect all matching attributes (nearest-first wins on conflict for the same attribute name). Apply the resulting attribute set to the operation:
   - `readonly` → block write-class operations
   - `writeonly` → block read-class operations
   - `noaccess` → block all (redundant with `.aiignore`, but accepted)
   - `tool=<name>` → block if the tool being used is not `<name>`
   - Extension attributes → implementation-defined

`.aiignore` is a hard block. `.aiattributes` is a modifier. An empty `.aiattributes` (or its absence) means no modification — the operation proceeds as if no policy existed.

## 4. `.aiignore` format

Each non-empty, non-comment line is one rule.

```
<rule> ::= [<negation>] <pattern>
```

Patterns follow **gitignore glob semantics**:

| Token | Matches |
|---|---|
| `?` | Any single character (excluding `/`) |
| `*` | Zero or more characters (excluding `/`) |
| `**` | Zero or more directory levels |
| `[abc]` | One of the listed characters |
| `[!abc]` | Any character except listed |
| Literal | Itself |
| Trailing `/` | Matches directories only |
| Leading `/` | Anchors to the `.aiignore` directory's root |
| `!` prefix | Negation — explicitly allow even if a broader rule blocks |

`#` lines are comments. Blank lines are ignored.

Match in `.aiignore` = **absolute block, both read and write**. There is no mode prefix in `.aiignore` (v0.1 supported `[mode:read]` / `[mode:write]` — removed in v0.2 in favor of `.aiattributes`).

## 5. `.aiattributes` format

Each non-empty, non-comment line is one rule.

```
<rule> ::= <pattern> <whitespace> <attribute> [<whitespace> <attribute> …]
<attribute> ::= <name> | <name>=<value> | -<name>
```

- The first whitespace-delimited token is the **pattern** (same glob syntax as `.aiignore`).
- Subsequent tokens are **attributes** applied to paths matching that pattern.
- An attribute is one of:
  - **Bare name** (e.g. `readonly`) — boolean flag, set true
  - **Name=value** (e.g. `tool=obsidian-cli`) — typed value
  - **-name** (e.g. `-readonly`) — unset/negate (allows overriding a broader rule at a nearer location)
- `#` lines are comments. Blank lines are ignored.

When the same attribute appears multiple times across the walk, the **nearest** `.aiattributes` wins.

## 6. Standard attributes (core)

Conforming implementations MUST recognize and enforce these:

| Attribute | Effect |
|---|---|
| `readonly` | Block write-class operations on matching paths. Read-class operations proceed. |
| `writeonly` | Block read-class operations on matching paths. Write-class operations proceed. |
| `noaccess` | Block both read- and write-class operations (equivalent to listing the path in `.aiignore`). |

These are the entire core. Implementations MAY add extension attributes (§7).

## 7. Extension attributes

Implementations MAY define additional attributes. To prevent naming collisions, extension attributes SHOULD use a `<namespace>=<value>` form where `<namespace>` is short and unambiguous within the implementation's domain.

Examples reserved for common extension patterns (not normative):

| Attribute | Suggested meaning |
|---|---|
| `tool=<name>` | Access only allowed if the agent's tool name matches `<name>` |
| `agent=<name>` | Access only allowed if the agent identity matches `<name>` |
| `audit=<level>` | Audit-log this access at the specified level |
| `time=<window>` | Access only allowed in the specified time window |
| `<vendor>.<attr>=<value>` | Vendor-prefixed custom attribute |

Implementations encountering an **unknown** attribute MUST NOT treat the rule as blocking on that ground alone — unknown attributes are silently ignored, so a `.aiattributes` file authored for one implementation remains forward-compatible with others.

## 8. Operation classes

For the purpose of `readonly` / `writeonly` matching, agent tools are grouped into two classes:

**Read class** — operations that obtain content or metadata without modifying it:
- `read_file`, `read`, `glob`, `find`, `grep`, `search`, `stat`, `ls`, `list_dir`
- Any tool whose effect is read-only

**Write class** — operations that modify content or metadata:
- `write_file`, `write`, `create`, `edit_file`, `edit`, `patch`, `apply_patch`
- `delete`, `rm`, `unlink`, `move`, `rename`, `chmod`, `chown`
- Skill-management operations that produce or modify files

Implementations MAY extend these mappings appropriate to their agent's tool surface.

## 9. Enforcement contract

When an operation is denied (by either `.aiignore` or by an attribute in `.aiattributes`), the implementation MUST:

1. Refuse to execute the underlying agent tool call.
2. Return to the agent a structured result containing:
   - A clear human-readable reason
   - The matched file path (`.aiignore` or `.aiattributes`)
   - The matched rule (verbatim) AND, for `.aiattributes`, which attribute triggered the block
3. Append an audit record before returning (§10).

The agent SHOULD treat the denial as a normal tool failure.

## 10. Audit log

Implementations MUST write one record per denied operation. Each record contains:

- ISO-8601 timestamp (UTC)
- Tool name
- Resolved target path
- `policy_file` — path to the `.aiignore` or `.aiattributes` that triggered the block
- `policy_kind` — `"ignore"` or `"attribute"`
- For `.aiignore` blocks: `matched_rule`
- For `.aiattributes` blocks: `matched_rule` AND `matched_attribute` (e.g. `"readonly"`, `"tool=obsidian-cli"`)
- Mode (`block` / `warn`)
- Agent session or task ID, if available

Log SHOULD be one JSON object per line.

## 11. Modes

Implementations SHOULD provide three environmental modes:

| Mode | Behavior |
|---|---|
| **block** (default) | Refuse the operation, return denial result, log |
| **warn** | Allow the operation, attach warning to the result, log |
| **off** | Plugin loads but performs no checks |

Mode SHOULD be controllable via an environment variable named `<IMPL>_AIIGNORE_MODE` or equivalent.

## 12. Edge cases

### 12.1 Symlinks

Default: resolve symlinks before matching. Prevents bypass via a symlink from an unrestricted folder into a restricted one. Implementations MAY offer a raw-path mode (document the trade-off).

### 12.2 Empty files

An empty `.aiignore` or `.aiattributes` (no rules, only comments/blanks) is treated as if absent. The walk continues to parent directories.

### 12.3 Self-reference

Neither `.aiignore` nor `.aiattributes` is protected by its own rules unless explicitly listed. Implementations SHOULD internally protect both file types from being overwritten by the agent as a safety measure, even without explicit rules.

### 12.4 Multi-path operations (glob, grep, find)

For tools that return multiple results, the implementation MUST apply the algorithm to each candidate path and filter blocked entries from the result silently — OR — block the operation entirely if any blocked path matches. The chosen behavior SHOULD be configurable; default is **filter silently** to preserve agent ergonomics.

### 12.5 Bulk operations (move, copy)

For source-and-destination operations, BOTH paths are checked. A blocked source OR a blocked destination causes the operation to be denied.

### 12.6 Tool-restriction (`tool=<name>`) ambiguity

The `tool=<name>` extension constrains *which agent tool* may operate on the path. The implementation MUST know its own tool's name (or an alias the user has configured) to match. If the matched tool name does not match the attribute, the operation is denied with reason `tool-restricted`.

### 12.7 Shell / terminal tool

If the agent has a generic shell/terminal tool, the implementation cannot intercept individual file accesses inside the shell process. Implementations SHOULD either restrict the shell's working directory, statically analyze the command for filesystem paths, OR document this limitation.

## 13. Conformance

A conforming implementation MUST:

- [ ] Parse `.aiignore` per §4 (gitignore semantics)
- [ ] Parse `.aiattributes` per §5
- [ ] Implement the walk + evaluation order per §3
- [ ] Enforce all standard attributes from §6 (`readonly`, `writeonly`, `noaccess`)
- [ ] Refuse blocked operations per §9
- [ ] Write an audit log per §10
- [ ] Silently ignore unknown extension attributes per §7

A conforming implementation SHOULD:

- [ ] Support `warn` and `off` modes
- [ ] Support `.aiignore-root` walk terminator
- [ ] Resolve symlinks by default
- [ ] Implement at least one extension attribute relevant to its agent (commonly `tool=`)

## 14. Versioning

Semantic versioning. Backward-incompatible changes increment the major version. Implementations SHOULD declare the highest spec version they implement in their documentation.

## 15. Changelog

- **0.2 (2026-06)** — Split into `.aiignore` (absolute block, gitignore-only) and `.aiattributes` (modulated behavior, gitattributes-style). Removed `[mode:read]` / `[mode:write]` prefix from `.aiignore`. Added standard attributes `readonly`, `writeonly`, `noaccess`. Defined extension-attribute namespace convention. Tool-restriction (`tool=<name>`) added as recommended extension.
- **0.1 (2026-06)** — Initial draft with mode-prefix inside `.aiignore`.
