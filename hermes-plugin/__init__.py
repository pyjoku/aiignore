"""aiignore — Hermes Agent plugin enforcing the .aiignore + .aiattributes
convention (spec v0.2).

Wires one behaviour:

* ``pre_tool_call`` hook — for each file-touching tool call:
    1. Walk and check ``.aiignore`` (absolute block)
    2. If allowed, walk and collect ``.aiattributes``; apply standard
       attributes (``readonly``, ``writeonly``, ``noaccess``) and the
       ``tool=<name>`` extension

Modes (via environment variables):

* ``AIIGNORE_MODE=block`` (default) — refuse blocked operations
* ``AIIGNORE_MODE=warn`` — allow, but log a warning
* ``AIIGNORE_MODE=off`` — kill switch, plugin loads but does nothing

Implements the .aiignore + .aiattributes convention v0.2 — see SPEC.md.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .matcher import (
    Decision,
    classify_tool,
    extract_paths,
    walk_and_decide,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode + audit-log helpers
# ---------------------------------------------------------------------------


def _mode() -> str:
    raw = os.environ.get("AIIGNORE_MODE", "block").lower().strip()
    if raw in {"block", "warn", "off"}:
        return raw
    return "block"


def _allow_policy_edits() -> bool:
    """When AIIGNORE_ALLOW_POLICY_EDITS=1, the agent is allowed to edit
    .aiignore / .aiattributes / .aiignore-root files. Default is to refuse —
    a policy the agent can rewrite is a policy the agent can dismantle."""
    raw = os.environ.get("AIIGNORE_ALLOW_POLICY_EDITS", "").lower().strip()
    return raw in {"1", "true", "yes", "on"}


def _audit_log_path() -> Path:
    custom = os.environ.get("AIIGNORE_AUDIT_LOG")
    if custom:
        return Path(custom).expanduser()
    base = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return base / "logs" / "aiignore-blocks.log"


def _write_audit_record(
    *,
    tool_name: str,
    target_path: Path,
    decision: Decision,
    mode: str,
    task_id: Optional[str],
) -> None:
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "path": str(target_path),
        "mode": mode,
        "task_id": task_id or "",
        "policy_kind": decision.kind or "",
    }
    if decision.rule_raw is not None:
        record["matched_rule"] = decision.rule_raw
    if decision.attribute is not None:
        record["matched_attribute"] = decision.attribute
    if decision.source_file is not None:
        record["policy_file"] = str(decision.source_file)

    log_path = _audit_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("aiignore: failed to write audit log: %s", exc)


def _format_block_message(target_path: Path, decision: Decision) -> str:
    rule = decision.rule_raw or "(unknown)"
    source = str(decision.source_file) if decision.source_file else "(unknown)"
    kind = decision.kind or "policy"
    attr_line = ""
    if decision.attribute:
        attr_line = f"  Attr:    {decision.attribute}\n"
    return (
        f"⚠️ Path blocked by .{kind} rule.\n"
        f"  Target:  {target_path}\n"
        f"  Rule:    {rule}\n"
        f"{attr_line}"
        f"  Source:  {source}\n"
        f"This path is outside the AI tool access policy. "
        f"The user can edit the policy file or set AIIGNORE_MODE=off to disable."
    )


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    task_id: Optional[str] = None,
    **_: Any,
) -> Optional[dict[str, Any]]:
    """Hermes pre_tool_call hook.

    Returns a dict with ``block=True`` and a reason to refuse the call,
    or None to allow.
    """
    mode = _mode()
    if mode == "off":
        return None

    operation = classify_tool(tool_name)
    if operation is None:
        return None

    paths = extract_paths(tool_name, args or {})
    if not paths:
        return None

    allow_policy_edits = _allow_policy_edits()
    for path in paths:
        decision = walk_and_decide(
            path,
            operation,
            tool_name=tool_name,
            allow_policy_edits=allow_policy_edits,
        )
        if decision.blocked:
            _write_audit_record(
                tool_name=tool_name,
                target_path=path,
                decision=decision,
                mode=mode,
                task_id=task_id,
            )
            message = _format_block_message(path, decision)
            if mode == "block":
                return {"block": True, "reason": message}
            logger.warning("aiignore [warn]: %s", message)
    return None


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Called by Hermes at plugin load time."""
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    logger.info("aiignore plugin loaded (mode=%s, spec=v0.2)", _mode())
