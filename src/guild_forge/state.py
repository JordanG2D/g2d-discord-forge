from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"State file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"State file root must be a JSON object: {path}")
    return payload


def save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def initial_state(template_code: str, guild_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "template_code": template_code,
        "target_guild_id": guild_id,
        "template_updated_at": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "last_apply_at": None,
        "roles": {},
        "channels": {},
        "runs": [],
    }


def validate_state_binding(
    state: dict[str, Any] | None,
    *,
    template_code: str,
    guild_id: str,
) -> None:
    if state is None:
        return
    bound_template = str(state.get("template_code"))
    bound_guild = str(state.get("target_guild_id"))
    if bound_template != template_code or bound_guild != guild_id:
        raise ValueError(
            "State file binding mismatch: "
            f"expected template={template_code}, guild={guild_id}; "
            f"found template={bound_template}, guild={bound_guild}."
        )


def archive_state(path: Path, suffix: str = "rolled-back") -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archived = path.with_name(f"{path.stem}.{suffix}.{timestamp}{path.suffix}")
    os.replace(path, archived)
    return archived
