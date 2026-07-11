from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from guild_forge.config import Settings
from guild_forge.core import approval_code
from guild_forge.engine import apply, generate_invite_url, rollback, verify

from test_core import TARGET_CHANNELS, TARGET_GUILD, TARGET_ROLES, TEMPLATE


class FakeDiscordAPI:
    def __init__(self) -> None:
        self.template = deepcopy(TEMPLATE)
        self.guild = deepcopy(TARGET_GUILD)
        self.roles = deepcopy(TARGET_ROLES)
        self.channels = deepcopy(TARGET_CHANNELS)
        self.bot_user = {"id": "777"}
        self.bot_member = {"roles": ["201"]}
        self.next_role = 500
        self.next_channel = 600

    def get(self, path: str, *, auth: bool = True):
        if path == "/guilds/templates/AbCdEf123456":
            return deepcopy(self.template)
        if path == "/guilds/999":
            return deepcopy(self.guild)
        if path == "/guilds/999/roles":
            return deepcopy(self.roles)
        if path == "/guilds/999/channels":
            return deepcopy(self.channels)
        if path == "/users/@me":
            return deepcopy(self.bot_user)
        if path == "/guilds/999/members/777":
            return deepcopy(self.bot_member)
        raise AssertionError(f"Unexpected GET {path}")

    def post(self, path: str, payload):
        if path == "/guilds/999/roles":
            for existing in self.roles:
                if int(existing.get("position", 0)) >= 1:
                    existing["position"] = int(existing.get("position", 0)) + 1
            role = {
                "id": str(self.next_role),
                "position": 1,
                "managed": False,
                **deepcopy(payload),
            }
            self.next_role += 1
            self.roles.append(role)
            return deepcopy(role)
        if path == "/guilds/999/channels":
            channel = {
                "id": str(self.next_channel),
                "position": len(self.channels),
                **deepcopy(payload),
            }
            self.next_channel += 1
            self.channels.append(channel)
            return deepcopy(channel)
        raise AssertionError(f"Unexpected POST {path}")

    def patch(self, path: str, payload):
        if path == "/guilds/999/roles":
            by_id = {item["id"]: item for item in self.roles}
            for change in payload:
                if change["id"] in by_id:
                    by_id[change["id"]]["position"] = change["position"]
            return deepcopy(self.roles)
        if path.startswith("/guilds/999/roles/"):
            role_id = path.rsplit("/", 1)[-1]
            role = next(item for item in self.roles if item["id"] == role_id)
            role.update(deepcopy(payload))
            return deepcopy(role)
        if path == "/guilds/999/channels":
            by_id = {item["id"]: item for item in self.channels}
            for change in payload:
                channel = by_id.get(change["id"])
                if channel is None:
                    continue
                if "position" in change:
                    channel["position"] = change["position"]
                if "parent_id" in change:
                    channel["parent_id"] = change["parent_id"]
            return deepcopy(self.channels)
        if path.startswith("/channels/"):
            channel_id = path.rsplit("/", 1)[-1]
            channel = next(item for item in self.channels if item["id"] == channel_id)
            channel.update(deepcopy(payload))
            return deepcopy(channel)
        raise AssertionError(f"Unexpected PATCH {path}")

    def delete(self, path: str, *, auth: bool = True):
        if path.startswith("/channels/"):
            channel_id = path.rsplit("/", 1)[-1]
            self.channels = [item for item in self.channels if item["id"] != channel_id]
            return None
        if path.startswith("/guilds/999/roles/"):
            role_id = path.rsplit("/", 1)[-1]
            self.roles = [item for item in self.roles if item["id"] != role_id]
            return None
        raise AssertionError(f"Unexpected DELETE {path}")


class FakeApplicationAPI:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path: str, *, auth: bool = True):
        self.paths.append(path)
        if path == "/applications/@me":
            return {"id": "424242"}
        raise AssertionError(f"Unexpected GET {path}")


class InviteURLTests(unittest.TestCase):
    def test_invite_url_uses_current_application_endpoint(self) -> None:
        fake = FakeApplicationAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            settings = Settings(
                bot_token="private-test-token",
                application_id=None,
                target_guild_id="999",
                template_ref="https://discord.new/AbCdEf123456",
                template_code="AbCdEf123456",
                state_dir=state_dir,
                state_file=state_dir / "state.json",
                plan_file=state_dir / "plan.json",
                placement="top",
                adopt_existing=True,
                require_admin=True,
                api_base="https://discord.com/api/v10",
                request_timeout=1.0,
                audit_reason="test",
            )

            with patch("guild_forge.engine.make_api", return_value=fake):
                url = generate_invite_url(settings)

            self.assertEqual(fake.paths, ["/applications/@me"])
            self.assertIn("client_id=424242", url)
            self.assertIn("permissions=8", url)
            self.assertIn("scope=bot", url)
            self.assertIn("guild_id=999", url)



class EngineLifecycleTests(unittest.TestCase):
    def test_apply_verify_rollback_lifecycle(self) -> None:
        fake = FakeDiscordAPI()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_dir = Path(temp_dir)
            settings = Settings(
                bot_token="private-test-token",
                application_id="201",
                target_guild_id="999",
                template_ref="https://discord.new/AbCdEf123456",
                template_code="AbCdEf123456",
                state_dir=state_dir,
                state_file=state_dir / "state.json",
                plan_file=state_dir / "plan.json",
                placement="top",
                adopt_existing=True,
                require_admin=True,
                api_base="https://discord.com/api/v10",
                request_timeout=1.0,
                audit_reason="test",
            )

            apply_code = approval_code("APPLY", settings.template_code, "999")
            rollback_code = approval_code("ROLLBACK", settings.template_code, "999")

            with patch("guild_forge.engine.make_api", return_value=fake):
                result = apply(settings, confirmation=apply_code)
                self.assertEqual(result["run"]["status"], "succeeded")
                self.assertEqual(len(result["run"]["created_roles"]), 1)
                self.assertEqual(len(result["run"]["created_channels"]), 1)
                self.assertTrue(settings.state_file.exists())

                verification = verify(settings)
                self.assertTrue(verification["ok"], verification["drift"])

                # A second apply must converge existing Forge-owned resources rather than duplicate them.
                second = apply(settings, confirmation=apply_code)
                self.assertEqual(len(second["run"]["created_roles"]), 0)
                self.assertEqual(len(second["run"]["created_channels"]), 0)
                self.assertEqual(len([role for role in fake.roles if role["name"] == "Admin"]), 1)
                self.assertEqual(len([channel for channel in fake.channels if channel["name"] == "voice"]), 1)

                rolled_back = rollback(settings, confirmation=rollback_code)
                self.assertEqual(len(rolled_back["deleted_roles"]), 1)
                self.assertEqual(len(rolled_back["deleted_channels"]), 1)
                self.assertFalse(settings.state_file.exists())
                self.assertIsNotNone(rolled_back["archived_state"])

                self.assertFalse(any(role["name"] == "Admin" for role in fake.roles))
                self.assertFalse(any(channel["name"] == "voice" for channel in fake.channels))
                self.assertTrue(any(channel["name"] == "preexisting" for channel in fake.channels))


if __name__ == "__main__":
    unittest.main()
