from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_TEMPLATE = ""
DEFAULT_API_BASE = "https://discord.com/api/v10"


def load_dotenv(path: Path | str = ".env") -> None:
    """Load a small, dependency-free subset of .env syntax.

    Existing process environment variables always win.
    """

    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_template_code(value: str) -> str:
    """Accept a Discord template URL or a raw template code."""

    candidate = value.strip()
    if not candidate:
        raise ValueError("Template reference is empty.")

    if re.fullmatch(r"[A-Za-z0-9_-]{2,100}", candidate):
        return candidate

    parsed = urlparse(candidate)
    host = parsed.netloc.lower().split(":", 1)[0]
    parts = [part for part in parsed.path.split("/") if part]

    if host in {"discord.new", "www.discord.new"} and parts:
        code = parts[-1]
    elif host in {"discord.com", "www.discord.com", "canary.discord.com", "ptb.discord.com"}:
        if len(parts) >= 2 and parts[-2] == "template":
            code = parts[-1]
        else:
            raise ValueError(f"Unsupported Discord template URL: {value}")
    else:
        raise ValueError(f"Unsupported template reference: {value}")

    if not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", code):
        raise ValueError(f"Invalid Discord template code: {code}")
    return code


@dataclass(frozen=True)
class Settings:
    bot_token: str | None
    application_id: str | None
    target_guild_id: str | None
    template_ref: str
    template_code: str
    state_dir: Path
    state_file: Path
    plan_file: Path
    placement: str
    adopt_existing: bool
    require_admin: bool
    api_base: str
    request_timeout: float
    audit_reason: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        template_ref = os.getenv("DISCORD_TEMPLATE", DEFAULT_TEMPLATE).strip()
        if not template_ref:
            raise ValueError(
                "DISCORD_TEMPLATE is required. Provide a Discord template URL "
                "or raw template code in .env."
            )
        template_code = parse_template_code(template_ref)
        target_guild_id = _clean_optional(os.getenv("TARGET_GUILD_ID"))
        state_dir = Path(os.getenv("FORGE_STATE_DIR", "./state")).expanduser()
        state_name = (
            f"{target_guild_id or 'unbound'}-{template_code}-state.json"
        )
        plan_name = f"{target_guild_id or 'unbound'}-{template_code}-plan.json"

        placement = os.getenv("FORGE_PLACEMENT", "top").strip().lower()
        if placement not in {"top", "append"}:
            raise ValueError("FORGE_PLACEMENT must be either 'top' or 'append'.")

        return cls(
            bot_token=_clean_optional(os.getenv("DISCORD_BOT_TOKEN")),
            application_id=_clean_optional(os.getenv("DISCORD_APPLICATION_ID")),
            target_guild_id=target_guild_id,
            template_ref=template_ref,
            template_code=template_code,
            state_dir=state_dir,
            state_file=state_dir / state_name,
            plan_file=state_dir / plan_name,
            placement=placement,
            adopt_existing=env_bool("FORGE_ADOPT_EXISTING", True),
            require_admin=env_bool("FORGE_REQUIRE_ADMIN", True),
            api_base=os.getenv("DISCORD_API_BASE", DEFAULT_API_BASE).rstrip("/"),
            request_timeout=float(os.getenv("FORGE_REQUEST_TIMEOUT", "30")),
            audit_reason=os.getenv(
                "FORGE_AUDIT_REASON",
                f"G2D Discord Forge template {template_code}",
            ),
        )

    def require_token(self) -> str:
        if not self.bot_token:
            raise ValueError("DISCORD_BOT_TOKEN is required for this command.")
        return self.bot_token

    def require_guild_id(self) -> str:
        if not self.target_guild_id:
            raise ValueError("TARGET_GUILD_ID is required for this command.")
        if not self.target_guild_id.isdigit():
            raise ValueError("TARGET_GUILD_ID must be the numeric Discord server ID.")
        return self.target_guild_id


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None
