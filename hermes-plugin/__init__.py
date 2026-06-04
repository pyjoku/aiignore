"""aiignore — Hermes Agent plugin enforcing the .aiignore convention.

Wires one behaviour:

* ``pre_tool_call`` hook — for each file-touching tool call, walks from the
  target path up to the filesystem root collecting ``.aiignore`` files, then
  matches the path against their rules. On match, refuses the tool call with
  a clear reason and logs an audit record.

Modes (via environment variables):

* ``AIIGNORE_MODE=block`` (default) — refuse blocked operations
* ``AIIGNORE_MODE=warn`` — allow, but attach a warning in the result
* ``AIIGNORE_MODE=off`` — kill switch, plugin loads but does nothing

Implements the .aiignore convention v0.1 — see SPEC.md in this repo.
"""

from __future__ import annotations

import json
import logging
import os
import time
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
# Mode handling
# ---------------------------------------------------------------------------


def _mode() -> str:
    raw = os.environ.get("AIIGNORE_MODE", "block").lower().strip()
    if raw in {"block", "warn", "off"}:
        return raw
    return "block"


def _audit_log_path() -> Path:
    custom = os.environ.get("AIIGNORE_AUDIT_LOG")
    if custom:
        return Path(custom).expanduser()
    base = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return base / "logs" / "aiignore-blocks.log"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _write_audit_record(
    *,
    tool_name: str,
    target_path: Path,
    decision: Decision,
    mode: str,
    task_id: Optional[str],
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "path": str(target_path),
        "mode": mode,
        "task_id": task_id or "",
    }
    if decision.rule is not None:
        record["matched_rule"] = decision.rule.raw
        record["matched_pattern"] = decision.rule.pattern
        record["matched_mode"] = decision.rule.mode or "both"
    if decision.source_file is not None:
        record["matched_source"] = str(decision.source_file)

    log_path = _audit_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("aiignore: failed to write audit log: %s", exc)


# ---------------------------------------------------------------------------
# Decision formatting
# ---------------------------------------------------------------------------


def _format_block_message(
    target_path: Path, decision: Decision
) -> str:
    rule_text = decision.rule.raw if decision.rule else "(unknown)"
    source = (
        str(decision.source_file)
        if decision.source_file is not None
        else "(unknown)"
    )
    return (
        f"⚠️ Path blocked by .aiignore rule.\n"
        f"  Target:  {target_path}\n"
        f"  Rule:    {rule_text}\n"
        f"  Source:  {source}\n"
        f"This path is outside the policy for AI tool access. "
        f"If you believe this is wrong, the user can edit the .aiignore "
        f"file or disable the plugin with AIIGNORE_MODE=off."
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
        # Unknown tool — don't intercept
        return None

    paths = extract_paths(tool_name, args or {})
    if not paths:
        return None

    for path in paths:
        decision = walk_and_decide(path, operation)
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
            # warn mode: don't block, but attach warning via transform_tool_result
            # would be cleaner; for v0.1 we surface a log line.
            logger.warning("aiignore [warn]: %s", message)
    return None


# ---------------------------------------------------------------------------
# Hermes plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Called by Hermes at plugin load time."""
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    logger.info("aiignore plugin loaded (mode=%s)", _mode())
