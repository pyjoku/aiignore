# .aiignore

> **A folder-local convention for telling AI agents which paths they may not read or write.**

`.aiignore` is to AI agents what `.gitignore` is to git: a familiar, self-documenting, per-folder file that declares what is off-limits. Drop one in any folder; agents that understand the convention will respect it.

## Why this exists

AI coding agents and personal assistants increasingly have direct filesystem access — read, write, edit, glob, grep. As the surface grows, so does the risk of an agent silently reading sensitive material or writing into a folder it shouldn't touch.

The current options are unsatisfying:

- **Prompt-level exclusions** are not enforced — the model may ignore or forget them.
- **OS file permissions** are heavy: per-user, per-process, hard to scope per workflow.
- **Per-agent custom config** locks you into one agent.

`.aiignore` is portable, familiar, granular, and lives next to the data it protects. Move the folder, the protection moves with it. Sync it via Dropbox/Nextcloud/git, the protection syncs.

## How it works

When an agent is about to perform a filesystem operation on path `P`, it walks from `P` up toward the filesystem root, collecting `.aiignore` files along the way. The patterns in those files are evaluated against `P`'s path relative to each file's location. If any pattern matches, the operation is blocked.

```
File on disk:                       What it means:

Vault/
├── .aiignore                       # rules for the whole vault
├── patient/
│   ├── .aiignore                   # extra rules just for /patient
│   └── records.md                  # ← agent calls read_file on this
└── _safe/
    └── note.md                     # ← agent calls write_file on this
```

For `read_file Vault/patient/records.md`, the agent checks:
1. `Vault/patient/.aiignore` — does any pattern match `records.md`?
2. `Vault/.aiignore` — does any pattern match `patient/records.md`?

If yes at any step → block.

For `write_file Vault/_safe/note.md`, the same walk happens; if no pattern matches, allowed.

## Pattern syntax

`.aiignore` uses gitignore-style glob syntax with three additions:

| Pattern | Meaning |
|---|---|
| `*` | Block everything in this folder and all subfolders |
| `*.md` | Block only files matching the glob |
| `patient/**` | Block a subfolder and everything inside it |
| `!exception.md` | Negate: explicitly allow this even if a broader rule blocks it |
| `[mode:read] secret*` | Mode prefix: this rule only applies to reads |
| `[mode:write] *` | Mode prefix: only writes are blocked (reads still allowed) |
| `# comment` | Comments and blank lines are ignored |

Without a `[mode:...]` prefix, a rule blocks both reads and writes.

See [SPEC.md](./SPEC.md) for the full pattern-matching semantics including edge cases.

## Example

A top-level `.aiignore` for an Obsidian vault that contains a mix of public notes, patient records, and finances:

```
# Block sensitive subtrees outright
patient/**
Finanzen/**
private-*/**

# Allow reads in family notes, but block writes
[mode:write] Familie/**

# Never overwrite Obsidian's workspace state
.obsidian/workspace.json
```

More examples in [examples/](./examples/).

## Reference implementations

| Agent | Status | Location |
|---|---|---|
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (Nous Research) | ✅ working | [`hermes-plugin/`](./hermes-plugin/) |
| Claude Code (Anthropic) | 🟡 planned (via hook) | — |
| Cowork / Knowledge Work | 🟡 planned | — |
| OpenClaw | 🟡 planned | — |

Pull requests for additional implementations welcome.

## Design principles

1. **Default-permissive.** No `.aiignore` → no restriction. Same posture as `.gitignore`.
2. **Local to the data.** No central config file to forget about. The protection lives in the folder it protects.
3. **Portable.** Move a folder → the protection moves with it. Sync via git/Dropbox/Nextcloud → the protection syncs.
4. **Block, don't warn.** When a pattern matches, the operation is refused with a clear reason in the tool result. The agent gets feedback and can self-correct or surface to the user.
5. **Audit by default.** Every block event should be logged so the user can verify the policy is doing what they intended.

## Status

This is a v0.1 convention draft plus a reference implementation for Hermes Agent. The spec may evolve based on feedback before stabilizing.

## License

MIT — see [LICENSE](./LICENSE).
