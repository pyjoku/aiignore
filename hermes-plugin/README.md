# aiignore — Hermes Agent plugin (v0.2)

Reference implementation of the [`.aiignore` + `.aiattributes` convention v0.2](../README.md) for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Enforces two file types:

- **`.aiignore`** — absolute block (gitignore syntax)
- **`.aiattributes`** — modulated behavior: `readonly`, `writeonly`, `noaccess`, `tool=<name>`

## Install

```bash
# Clone or symlink this folder into your Hermes plugins directory
ln -s "$(pwd)" ~/.hermes/plugins/aiignore

# Enable the plugin
hermes plugins enable aiignore
```

## Configure — drop `.aiignore` and/or `.aiattributes` files

```bash
# .aiignore = absolute block (no read, no write)
cat > ~/obsidianvaults/MyVault/patient/.aiignore <<'EOF'
*
EOF

# .aiattributes = readonly (block writes, allow reads)
cat > ~/obsidianvaults/MyVault/Family/.aiattributes <<'EOF'
*    readonly
EOF

# .aiattributes = tool-restriction
cat > ~/obsidianvaults/MyVault/skills/.aiattributes <<'EOF'
*    tool=obsidian-cli
EOF
```

See [examples/](../examples/) at the repo root.

## Modes (environment variables)

| Variable | Effect |
|---|---|
| `AIIGNORE_MODE=block` (default) | Refuse blocked operations with a clear reason |
| `AIIGNORE_MODE=warn` | Allow but log a warning |
| `AIIGNORE_MODE=off` | Plugin loads but does nothing (kill switch) |
| `AIIGNORE_AUDIT_LOG=<path>` | Override audit log location (default: `~/.hermes/logs/aiignore-blocks.log`) |
| `AIIGNORE_ALLOW_POLICY_EDITS=1` | Allow the agent to write `.aiignore` / `.aiattributes` / `.aiignore-root`. **Off by default** — a policy the agent can rewrite is a policy it can silently dismantle. |

## Audit log

Every block event is written as one JSON object per line:

```json
{"ts":"2026-06-04T14:23:11.901+00:00","tool":"write_file","path":"/Users/me/vault/patient/records.md","mode":"block","matched_rule":"*","matched_pattern":"*","matched_mode":"both","matched_source":"/Users/me/vault/patient/.aiignore","task_id":"abc123"}
```

Tail it during testing:

```bash
tail -f ~/.hermes/logs/aiignore-blocks.log | jq -r '"\(.ts) [\(.tool)] \(.path) → \(.matched_rule)"'
```

## How it works

When Hermes is about to call a filesystem tool (`read_file`, `write_file`, `patch`, `glob`, `grep`, etc.), the plugin's `pre_tool_call` hook fires. It:

1. Classifies the tool as **read** or **write** based on its name.
2. Extracts target paths from the tool's arguments.
3. For each path, walks from the path's parent directory up to the filesystem root collecting `.aiignore` files.
4. Evaluates each `.aiignore`'s rules against the path (relative to the file's location), respecting mode prefixes (`[mode:read]` / `[mode:write]`) and negations (`!`).
5. If any rule matches, returns `{"block": True, "reason": "..."}` to Hermes — the tool call never executes, and the model sees the block reason as the tool result.

See [SPEC.md](../SPEC.md) for full pattern semantics, edge cases, and conformance criteria.

## Testing locally

```bash
cd hermes-plugin
python -m pytest tests/
```

## License

MIT — see [LICENSE](../LICENSE) at the repo root.
