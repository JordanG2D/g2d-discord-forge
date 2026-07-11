from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass
class DiscordAPIError(RuntimeError):
    status: int | None
    message: str
    code: int | None = None
    body: Any = None

    def __str__(self) -> str:
        status = f"HTTP {self.status}" if self.status is not None else "Network error"
        code = f" / Discord code {self.code}" if self.code is not None else ""
        return f"{status}{code}: {self.message}"


class DiscordAPI:
    """Small Discord REST v10 client with rate-limit and transient retry handling."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        timeout: float = 30.0,
        audit_reason: str | None = None,
        max_attempts: int = 7,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.audit_reason = audit_reason
        self.max_attempts = max_attempts

    def get(self, path: str, *, auth: bool = True) -> Any:
        return self.request("GET", path, auth=auth)

    def post(self, path: str, payload: Any, *, auth: bool = True) -> Any:
        return self.request("POST", path, payload=payload, auth=auth)

    def patch(self, path: str, payload: Any, *, auth: bool = True) -> Any:
        return self.request("PATCH", path, payload=payload, auth=auth)

    def put(self, path: str, payload: Any | None = None, *, auth: bool = True) -> Any:
        return self.request("PUT", path, payload=payload, auth=auth)

    def delete(self, path: str, *, auth: bool = True) -> Any:
        return self.request("DELETE", path, auth=auth)

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        auth: bool = True,
    ) -> Any:
        if auth and not self.token:
            raise ValueError("A Discord bot token is required for this API request.")

        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "User-Agent": "DiscordBot (https://discord.com/developers/docs, 0.1.0)",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if auth and self.token:
            headers["Authorization"] = f"Bot {self.token}"
        if self.audit_reason and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
            headers["X-Audit-Log-Reason"] = quote(self.audit_reason, safe="")

        last_error: DiscordAPIError | None = None
        for attempt in range(1, self.max_attempts + 1):
            request = Request(url, data=body, headers=headers, method=method.upper())
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" in content_type or raw[:1] in {b"{", b"["}:
                        return json.loads(raw.decode("utf-8"))
                    return raw.decode("utf-8", errors="replace")
            except HTTPError as exc:
                raw = exc.read()
                parsed = _parse_json(raw)
                message, code = _discord_error_details(parsed, raw)
                error = DiscordAPIError(
                    status=exc.code,
                    message=message,
                    code=code,
                    body=parsed if parsed is not None else raw.decode("utf-8", errors="replace"),
                )
                last_error = error

                if exc.code == 429 and attempt < self.max_attempts:
                    retry_after = 1.0
                    if isinstance(parsed, dict):
                        try:
                            retry_after = float(parsed.get("retry_after", retry_after))
                        except (TypeError, ValueError):
                            pass
                    time.sleep(max(0.05, retry_after) + random.uniform(0.02, 0.20))
                    continue

                if exc.code in {500, 502, 503, 504} and attempt < self.max_attempts:
                    time.sleep(min(8.0, (2 ** (attempt - 1)) * 0.35) + random.uniform(0.02, 0.20))
                    continue

                raise error from exc
            except URLError as exc:
                error = DiscordAPIError(status=None, message=str(exc.reason), body=str(exc))
                last_error = error
                if attempt < self.max_attempts:
                    time.sleep(min(8.0, (2 ** (attempt - 1)) * 0.35) + random.uniform(0.02, 0.20))
                    continue
                raise error from exc

        if last_error is not None:
            raise last_error
        raise DiscordAPIError(status=None, message="Unknown Discord API failure.")


def _parse_json(raw: bytes) -> Any | None:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _discord_error_details(parsed: Any | None, raw: bytes) -> tuple[str, int | None]:
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or parsed)
        code_raw = parsed.get("code")
        try:
            code = int(code_raw) if code_raw is not None else None
        except (TypeError, ValueError):
            code = None
        errors = parsed.get("errors")
        if errors:
            message = f"{message} | details={json.dumps(errors, sort_keys=True)}"
        return message, code
    text = raw.decode("utf-8", errors="replace").strip()
    return text or "Discord API request failed.", None
