from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from guild_forge.config import parse_template_code
from guild_forge.core import (
    approval_code,
    build_plan,
    channel_creation_order,
    channel_payload,
    map_permission_overwrites,
    role_sort_top_to_bottom,
    source_channels,
    source_roles,
)
from guild_forge.state import initial_state, load_json, save_json_atomic, validate_state_binding


TEMPLATE = {
    "code": "AbCdEf123456",
    "name": "G2D Workspace",
    "updated_at": "2026-07-10T00:00:00+00:00",
    "source_guild_id": "123",
    "serialized_source_guild": {
        "name": "Source Workspace",
        "roles": [
            {
                "id": 0,
                "name": "@everyone",
                "position": 0,
                "permissions": "1024",
                "color": 0,
                "hoist": False,
                "mentionable": False,
            },
            {
                "id": 2,
                "name": "Member",
                "position": 1,
                "permissions": "2048",
                "color": 0,
                "hoist": False,
                "mentionable": True,
            },
            {
                "id": 1,
                "name": "Admin",
                "position": 2,
                "permissions": "8",
                "color": 123,
                "hoist": True,
                "mentionable": False,
            },
        ],
        "channels": [
            {
                "id": 10,
                "name": "WORK",
                "type": 4,
                "position": 0,
                "parent_id": None,
                "permission_overwrites": [],
            },
            {
                "id": 11,
                "name": "general",
                "type": 0,
                "position": 0,
                "parent_id": 10,
                "topic": "General work",
                "nsfw": False,
                "rate_limit_per_user": 0,
                "permission_overwrites": [
                    {"id": 0, "type": 0, "allow": "1024", "deny": "0"},
                    {"id": 2, "type": 0, "allow": "2048", "deny": "0"},
                ],
            },
            {
                "id": 12,
                "name": "voice",
                "type": 2,
                "position": 1,
                "parent_id": 10,
                "bitrate": 128000,
                "user_limit": 5,
                "permission_overwrites": [],
            },
        ],
    },
}

TARGET_GUILD = {"id": "999", "name": "Target Workspace"}
TARGET_ROLES = [
    {
        "id": "999",
        "name": "@everyone",
        "position": 0,
        "permissions": "1024",
        "managed": False,
    },
    {
        "id": "200",
        "name": "Member",
        "position": 1,
        "permissions": "2048",
        "managed": False,
    },
    {
        "id": "201",
        "name": "Forge Bot",
        "position": 2,
        "permissions": "8",
        "managed": True,
    },
]
TARGET_CHANNELS = [
    {"id": "300", "name": "WORK", "type": 4, "position": 0, "parent_id": None},
    {"id": "301", "name": "general", "type": 0, "position": 0, "parent_id": "300"},
    {"id": "302", "name": "preexisting", "type": 0, "position": 1, "parent_id": None},
]


class ConfigTests(unittest.TestCase):
    def test_template_code_parsing(self) -> None:
        self.assertEqual(parse_template_code("AbCdEf123456"), "AbCdEf123456")
        self.assertEqual(
            parse_template_code("https://discord.new/AbCdEf123456"),
            "AbCdEf123456",
        )
        self.assertEqual(
            parse_template_code("https://discord.com/template/AbCdEf123456?x=1"),
            "AbCdEf123456",
        )

    def test_invalid_template_reference(self) -> None:
        with self.assertRaises(ValueError):
            parse_template_code("https://example.com/nope")


class PlanTests(unittest.TestCase):
    def test_safe_reuse_and_create_plan(self) -> None:
        plan = build_plan(
            template=TEMPLATE,
            guild=TARGET_GUILD,
            target_roles=TARGET_ROLES,
            target_channels=TARGET_CHANNELS,
            state=None,
            adopt_existing=True,
            placement="top",
        )
        role_actions = {entry["name"]: entry["action"] for entry in plan["roles"]}
        channel_actions = {entry["name"]: entry["action"] for entry in plan["channels"]}

        self.assertEqual(role_actions["@everyone"], "map")
        self.assertEqual(role_actions["Member"], "reuse")
        self.assertEqual(role_actions["Admin"], "create")
        self.assertEqual(channel_actions["WORK"], "reuse")
        self.assertEqual(channel_actions["general"], "reuse")
        self.assertEqual(channel_actions["voice"], "create")
        self.assertEqual(plan["summary"]["extra_channels_preserved"], 1)
        self.assertFalse(plan["conflicts"])

    def test_ambiguous_role_is_conflict(self) -> None:
        roles = TARGET_ROLES + [
            {
                "id": "202",
                "name": "Member",
                "position": 1,
                "permissions": "0",
                "managed": False,
            }
        ]
        plan = build_plan(
            template=TEMPLATE,
            guild=TARGET_GUILD,
            target_roles=roles,
            target_channels=TARGET_CHANNELS,
            state=None,
            adopt_existing=True,
            placement="append",
        )
        self.assertTrue(any("Role 'Member'" in conflict for conflict in plan["conflicts"]))

    def test_approval_code_is_deterministic(self) -> None:
        first = approval_code("APPLY", "AbCdEf123456", "999")
        second = approval_code("APPLY", "AbCdEf123456", "999")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("APPLY-3456-999-"))


class PayloadTests(unittest.TestCase):
    def test_role_order_is_top_to_bottom(self) -> None:
        names = [role["name"] for role in role_sort_top_to_bottom(source_roles(TEMPLATE))]
        self.assertEqual(names, ["Admin", "Member", "@everyone"])

    def test_channel_creation_order_categories_first(self) -> None:
        names = [channel["name"] for channel in channel_creation_order(source_channels(TEMPLATE))]
        self.assertEqual(names, ["WORK", "general", "voice"])

    def test_overwrite_mapping(self) -> None:
        mapped, warnings = map_permission_overwrites(
            TEMPLATE["serialized_source_guild"]["channels"][1]["permission_overwrites"],
            role_map={"0": "999", "2": "200"},
        )
        self.assertEqual(warnings, [])
        self.assertEqual(mapped[0]["id"], "999")
        self.assertEqual(mapped[1]["id"], "200")

    def test_member_overwrite_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            map_permission_overwrites(
                [{"id": "123", "type": 1, "allow": "0", "deny": "0"}],
                role_map={},
            )

    def test_voice_bitrate_is_capped(self) -> None:
        voice = source_channels(TEMPLATE)[2]
        payload, warnings = channel_payload(
            voice,
            role_map={"0": "999", "2": "200", "1": "203"},
            channel_map={"10": "300"},
            include_type=True,
        )
        self.assertEqual(payload["bitrate"], 96000)
        self.assertTrue(any("capped" in warning for warning in warnings))


class StateTests(unittest.TestCase):
    def test_state_round_trip_and_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            state = initial_state("AbCdEf123456", "999")
            save_json_atomic(path, state)
            loaded = load_json(path)
            self.assertIsNotNone(loaded)
            validate_state_binding(loaded, template_code="AbCdEf123456", guild_id="999")
            with self.assertRaises(ValueError):
                validate_state_binding(loaded, template_code="other", guild_id="999")


if __name__ == "__main__":
    unittest.main()
