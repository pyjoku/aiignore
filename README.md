# .aiignore + .aiattributes

> **Folder-local conventions for telling AI agents which paths they may not access (`.aiignore`) and how access is modulated (`.aiattributes`).**

These files are to AI agents what `.gitignore` + `.gitattributes` are to git: familiar, self-documenting, per-folder, version-controllable. Drop them in any folder; agents that understand the convention will respect them.

| File | What it does | Inspired by |
|---|---|---|
| **`.aiignore`** | Lists paths the agent must NOT access at all (read or write). Absolute block. | `.gitignore` |
| **`.aiattributes`** | Lists paths and attributes constraining HOW the agent accesses them — `readonly`, `writeonly`, `tool=name`, etc. | `.gitattributes` |

## Why two files

`.aiignore` answers a yes/no question: may the agent touch this path at all? `.aiattributes` answers a how question: if it may touch this, under what constraints? The split mirrors git's split — and for the same reason: collapsing both into one file makes both harder to read.

## Quick example

```bash
# Vault root: keep patient + finance data completely off-limits
cat > .aiignore <<'EOF'
patient/**
Finanzen/**
EOF

# Vault root: PDFs read-only, /bank read-only, /skills only via obsidian-cli
cat > .aiattributes <<'EOF'
*.pdf          readonly
/bank/**       readonly
/skills/**     tool=obsidian-cli
EOF
```

The agent now:
- Cannot read or write anything under `patient/` or `Finanzen/`
- Can read PDFs but cannot edit/overwrite/delete them
- Can read `/bank/**` but cannot modify
- Can only operate on `/skills/**` via the `obsidian-cli` tool — raw `write_file` is refused

## How it works

When an agent is about to perform a filesystem operation on path `P`:

1. **Walk up `.aiignore`.** From `P`'s parent directory to filesystem root, collect every `.aiignore`. If any rule matches → operation blocked. Stop.
2. **Walk up `.aiattributes`.** Collect attributes from every matching rule. Nearer files override distant ones.
3. **Apply attributes** to the operation class (read/write):
   - `readonly` → block writes
   - `writeonly` → block reads
   - `noaccess` → block both
   - `tool=X` → block if the tool being used isn't `X`

See [SPEC.md](./SPEC.md) for the full specification (v0.2).

## Pattern syntax

Both files use **gitignore-style globs**:

| Token | Matches |
|---|---|
| `*` | any chars except `/` |
| `**` | any directory levels |
| `?` | one char except `/` |
| `[abc]` | one of the listed chars |
| Leading `/` | anchored to file's directory |
| Trailing `/` | directories only |

`.aiignore` adds `!` for negation:

```
patient/**
!patient/_anonymized/**
```

`.aiattributes` adds `-name` to unset an attribute set by a more-distant rule:

```
# root/.aiattributes
/sub/**         readonly

# root/sub/exception/.aiattributes
*               -readonly
```

## Standard attributes (core spec)

Every conforming implementation supports these:

| Attribute | Effect |
|---|---|
| `readonly` | Block write-class operations (write, edit, patch, delete, move, rename, chmod). Reads proceed. |
| `writeonly` | Block read-class operations (read, glob, grep, find, stat). Writes proceed. |
| `noaccess` | Block both classes (equivalent to listing in `.aiignore`, kept for symmetry). |

## Extension attributes (recommended, namespace-based)

Implementations MAY define additional attributes. Unknown attributes are silently ignored — your `.aiattributes` stays forward-compatible.

Common recommended extensions:

| Attribute | Suggested meaning |
|---|---|
| `tool=<name>` | Path may only be touched via the named agent tool |
| `agent=<name>` | Path may only be touched by the named agent identity |
| `audit=<level>` | Extra audit-logging for matching accesses |
| `time=<window>` | Time-of-day restriction |

## Reference implementations

| Agent | Status | Location |
|---|---|---|
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (Nous Research) | ✅ v0.2 spec, 33 tests green | [`hermes-plugin/`](./hermes-plugin/) |
| Claude Code (Anthropic) | 🟡 planned via PreToolUse hook | — |
| Cowork / Knowledge Work | 🟡 planned | — |
| OpenClaw | 🟡 planned | — |

Pull requests for additional implementations welcome.

## Design principles

1. **Default-permissive.** No `.aiignore` / `.aiattributes` → no restriction. Same posture as gitignore/gitattributes.
2. **Local to the data.** No central config. The policy lives with the folder it protects.
3. **Portable.** Move/copy/sync the folder → the policy moves with it.
4. **Block, don't warn.** When matched, the operation is refused. The agent receives the reason in the tool result and can self-correct or surface to the user.
5. **Audit by default.** Every block event is logged so the user can verify the policy is doing what they intended.
6. **Familiar.** Same glob syntax as gitignore. Attribute syntax mirrors gitattributes.

## Related work / acknowledgments

This convention emerged from multiple independent implementations grappling with the same problem. We aim to converge the syntax and semantics into one canonical spec rather than fragment further:

- [ItzBubschki/aiignore](https://github.com/ItzBubschki/aiignore) — npm `@aiignore/cli`, Claude Code hook (gitignore syntax, no attributes layer)
- JetBrains Junie (AI Assistant in IntelliJ/PyCharm) — proprietary native support; gitignore syntax with explicit known limitations (file-name leakage, Brave Mode bypass)
- [LIT-Protocol/Vincent](https://github.com/LIT-Protocol/Vincent/blob/main/.aiignore) — real-world `.aiignore` from a JetBrains project
- [Naman Jain, "aiignore: the next gitignore"](https://www.linkedin.com/pulse/aiignore-next-gitignore-we-cant-afford-ignore-naman-jain-3owxc/) — strategic case for the convention
- [JetBrains LLM-21159](https://youtrack.jetbrains.com/projects/LLM/issues/LLM-21159) — public issue acknowledging effectiveness gaps in their implementation

What this repo contributes:

- A **canonical spec** that's tool-agnostic (the spec docs are not Hermes-specific)
- The **two-file split** (`.aiignore` for blocks + `.aiattributes` for modulation) — gitattributes-style attributes are absent from the other implementations
- A **block-by-default enforcement model** (not approval prompts that can be bypassed)
- A **working Hermes reference implementation** with full test coverage

## Status

v0.2 draft. The spec may evolve based on cross-implementation feedback. Production use is encouraged for the Hermes plugin; treat the spec as stable enough to build against but expect minor refinement.

## License

MIT — see [LICENSE](./LICENSE).
