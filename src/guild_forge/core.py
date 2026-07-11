from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

# Discord permission bits used by preflight checks.
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
VIEW_CHANNEL = 1 << 10
MANAGE_ROLES = 1 << 28

SUPPORTED_CHANNEL_TYPES = {0, 2, 4, 5, 13, 15, 16}
CHANNEL_TYPE_NAMES = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    13: "stage",
    15: "forum",
    16: "media",
}


def sid(value: Any) -> str:
    return str(value)


def channel_type_name(value: Any) -> str:
    try:
        return CHANNEL_TYPE_NAMES.get(int(value), f"type-{value}")
    except (TypeError, ValueError):
        return f"type-{value}"


def template_guild(template: dict[str, Any]) -> dict[str, Any]:
    guild = template.get("serialized_source_guild")
    if not isinstance(guild, dict):
        raise ValueError("Discord returned a template without serialized_source_guild.")
    return guild


def source_roles(template: dict[str, Any]) -> list[dict[str, Any]]:
    roles = template_guild(template).get("roles", [])
    if not isinstance(roles, list):
        raise ValueError("Template roles payload is not a list.")
    result: list[dict[str, Any]] = []
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            continue
        item = deepcopy(role)
        item["_source_index"] = index
        item["_source_id"] = sid(role.get("id"))
        item["_source_position"] = _int_or(role.get("position"), index)
        result.append(item)
    return result


def source_channels(template: dict[str, Any]) -> list[dict[str, Any]]:
    channels = template_guild(template).get("channels", [])
    if not isinstance(channels, list):
        raise ValueError("Template channels payload is not a list.")
    result: list[dict[str, Any]] = []
    for index, channel in enumerate(channels):
        if not isinstance(channel, dict):
            continue
        item = deepcopy(channel)
        item["_source_index"] = index
        item["_source_id"] = sid(channel.get("id"))
        item["_source_position"] = _int_or(channel.get("position"), index)
        parent_id = channel.get("parent_id")
        item["_parent_source_id"] = None if parent_id is None else sid(parent_id)
        result.append(item)
    return result


def is_everyone_role(role: dict[str, Any]) -> bool:
    return role.get("name") == "@everyone" or sid(role.get("id")) == "0"


def role_sort_top_to_bottom(roles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        roles,
        key=lambda role: (
            _int_or(role.get("_source_position", role.get("position")), 0),
            _int_or(role.get("_source_index"), 0),
        ),
        reverse=True,
    )


def role_sort_bottom_to_top(roles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(reversed(role_sort_top_to_bottom(roles)))


def channel_creation_order(channels: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items = list(channels)
    category_positions = {
        item["_source_id"]: _int_or(item.get("_source_position"), 0)
        for item in items
        if _int_or(item.get("type"), -1) == 4
    }

    categories = sorted(
        (item for item in items if _int_or(item.get("type"), -1) == 4),
        key=_channel_sort_key,
    )
    top_level = sorted(
        (
            item
            for item in items
            if _int_or(item.get("type"), -1) != 4 and item.get("_parent_source_id") is None
        ),
        key=_channel_sort_key,
    )
    children = sorted(
        (
            item
            for item in items
            if _int_or(item.get("type"), -1) != 4 and item.get("_parent_source_id") is not None
        ),
        key=lambda item: (
            category_positions.get(item.get("_parent_source_id"), 10**9),
            _int_or(item.get("_source_position"), 0),
            _int_or(item.get("_source_index"), 0),
        ),
    )
    return categories + top_level + children


def approval_code(prefix: str, template_code: str, guild_id: str) -> str:
    material = f"{prefix}:{template_code}:{guild_id}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()[:8].upper()
    return f"{prefix}-{template_code[-4:].upper()}-{guild_id[-4:]}-{digest}"


def effective_guild_permissions(
    guild_id: str,
    member_role_ids: Iterable[str],
    target_roles: Iterable[dict[str, Any]],
) -> int:
    assigned = {sid(role_id) for role_id in member_role_ids}
    assigned.add(sid(guild_id))
    permissions = 0
    for role in target_roles:
        role_id = sid(role.get("id"))
        if role_id in assigned:
            permissions |= _int_or(role.get("permissions"), 0)
    return permissions


def has_required_permissions(permissions: int, require_admin: bool) -> tuple[bool, list[str]]:
    if permissions & ADMINISTRATOR:
        return True, []
    if require_admin:
        return False, ["Administrator"]

    missing: list[str] = []
    if not permissions & MANAGE_CHANNELS:
        missing.append("Manage Channels")
    if not permissions & MANAGE_ROLES:
        missing.append("Manage Roles")
    if not permissions & VIEW_CHANNEL:
        missing.append("View Channels")
    return not missing, missing


def build_plan(
    *,
    template: dict[str, Any],
    guild: dict[str, Any],
    target_roles: list[dict[str, Any]],
    target_channels: list[dict[str, Any]],
    state: dict[str, Any] | None,
    adopt_existing: bool,
    placement: str,
) -> dict[str, Any]:
    guild_id = sid(guild["id"])
    state = state or {}
    state_roles = state.get("roles", {}) if isinstance(state.get("roles", {}), dict) else {}
    state_channels = state.get("channels", {}) if isinstance(state.get("channels", {}), dict) else {}

    target_roles_by_id = {sid(role.get("id")): role for role in target_roles}
    target_channels_by_id = {sid(channel.get("id")): channel for channel in target_channels}
    used_role_ids: set[str] = set()
    used_channel_ids: set[str] = set()
    conflicts: list[str] = []
    warnings: list[str] = []

    role_entries: list[dict[str, Any]] = []
    role_resolution: dict[str, str | None] = {}

    for role in role_sort_bottom_to_top(source_roles(template)):
        source_id = role["_source_id"]
        name = str(role.get("name", "unnamed-role"))
        entry = _base_role_entry(role)

        if is_everyone_role(role):
            entry.update(action="map", target_id=guild_id, owned=False, reason="maps to target @everyone")
            role_resolution[source_id] = guild_id
            used_role_ids.add(guild_id)
            target_everyone = target_roles_by_id.get(guild_id)
            if target_everyone is not None:
                differences = _role_setting_differences(role, target_everyone)
                if differences:
                    warnings.append(
                        "Target @everyone differs from the template in "
                        + ", ".join(differences)
                        + "; Forge preserves the target @everyone role."
                    )
            role_entries.append(entry)
            continue

        prior = state_roles.get(source_id)
        if isinstance(prior, dict):
            target_id = sid(prior.get("target_id"))
            target = target_roles_by_id.get(target_id)
            if target is not None:
                if target.get("managed"):
                    conflicts.append(
                        f"Role '{name}' is mapped to a Discord-managed role ({target_id}); it cannot be managed or adopted."
                    )
                    entry.update(action="conflict", target_id=target_id, owned=False, reason="managed role")
                else:
                    owned = bool(prior.get("owned", False))
                    entry.update(
                        action="managed" if owned else "reuse",
                        target_id=target_id,
                        owned=owned,
                        reason="state mapping",
                    )
                    if target.get("name") != name:
                        if owned:
                            warnings.append(
                                f"Managed role {target_id} drifted from '{name}' to '{target.get('name')}' and will be restored."
                            )
                        else:
                            warnings.append(
                                f"Adopted role {target_id} is now named '{target.get('name')}', not '{name}'; it will be preserved."
                            )
                    if not owned:
                        differences = _role_setting_differences(role, target)
                        if differences:
                            warnings.append(
                                f"Adopted role '{name}' differs from the template in "
                                + ", ".join(differences)
                                + "; those preexisting settings will be preserved."
                            )
                    role_resolution[source_id] = target_id
                    used_role_ids.add(target_id)
                role_entries.append(entry)
                continue
            warnings.append(f"State mapping for role '{name}' points to missing role {target_id}; it will be recreated.")

        candidates = [
            target
            for target in target_roles
            if sid(target.get("id")) not in used_role_ids
            and not target.get("managed", False)
            and target.get("name") == name
            and sid(target.get("id")) != guild_id
        ]
        if adopt_existing and len(candidates) == 1:
            target = candidates[0]
            target_id = sid(target["id"])
            entry.update(action="reuse", target_id=target_id, owned=False, reason="exact role-name match")
            role_resolution[source_id] = target_id
            used_role_ids.add(target_id)
            differences = _role_setting_differences(role, target)
            if differences:
                warnings.append(
                    f"Adopted role '{name}' differs from the template in "
                    + ", ".join(differences)
                    + "; those preexisting settings will be preserved."
                )
        elif adopt_existing and len(candidates) > 1:
            conflicts.append(
                f"Role '{name}' has {len(candidates)} exact matches in the target server; Forge will not guess."
            )
            entry.update(action="conflict", target_id=None, owned=False, reason="ambiguous exact matches")
            role_resolution[source_id] = None
        else:
            entry.update(action="create", target_id=None, owned=True, reason="missing from target")
            role_resolution[source_id] = None
        role_entries.append(entry)

    channel_entries: list[dict[str, Any]] = []
    channel_resolution: dict[str, str | None] = {}
    sources = source_channels(template)
    source_by_id = {channel["_source_id"]: channel for channel in sources}

    # Categories first so child matching can use an adopted or managed parent.
    ordered = sorted(
        sources,
        key=lambda channel: (
            0 if _int_or(channel.get("type"), -1) == 4 else 1,
            _int_or(channel.get("_source_position"), 0),
            _int_or(channel.get("_source_index"), 0),
        ),
    )

    for channel in ordered:
        source_id = channel["_source_id"]
        name = str(channel.get("name", "unnamed-channel"))
        channel_type = _int_or(channel.get("type"), -1)
        parent_source_id = channel.get("_parent_source_id")
        entry = _base_channel_entry(channel)

        if channel_type not in SUPPORTED_CHANNEL_TYPES:
            conflicts.append(
                f"Channel '{name}' uses unsupported template channel type {channel_type}."
            )
            entry.update(action="conflict", target_id=None, owned=False, reason="unsupported channel type")
            channel_resolution[source_id] = None
            channel_entries.append(entry)
            continue

        expected_parent_target_id = (
            channel_resolution.get(parent_source_id) if parent_source_id is not None else None
        )

        prior = state_channels.get(source_id)
        if isinstance(prior, dict):
            target_id = sid(prior.get("target_id"))
            target = target_channels_by_id.get(target_id)
            if target is not None:
                target_type = _int_or(target.get("type"), -1)
                owned = bool(prior.get("owned", False))
                if target_type != channel_type:
                    conflicts.append(
                        f"Channel '{name}' maps to {target_id}, but its type changed from "
                        f"{channel_type_name(channel_type)} to {channel_type_name(target_type)}."
                    )
                    entry.update(action="conflict", target_id=target_id, owned=owned, reason="channel type drift")
                else:
                    entry.update(
                        action="managed" if owned else "reuse",
                        target_id=target_id,
                        owned=owned,
                        reason="state mapping",
                    )
                    if target.get("name") != name:
                        if owned:
                            warnings.append(
                                f"Managed channel {target_id} drifted from '{name}' to '{target.get('name')}' and will be restored."
                            )
                        else:
                            warnings.append(
                                f"Adopted channel {target_id} is now named '{target.get('name')}', not '{name}'; it will be preserved."
                            )
                    if (
                        expected_parent_target_id is not None
                        and sid(target.get("parent_id")) != expected_parent_target_id
                    ):
                        if owned:
                            warnings.append(
                                f"Managed channel '{name}' has parent drift and will be moved back under its template category."
                            )
                        else:
                            warnings.append(
                                f"Adopted channel '{name}' is under a different parent and will not be moved."
                            )
                    channel_resolution[source_id] = target_id
                    used_channel_ids.add(target_id)
                channel_entries.append(entry)
                continue
            warnings.append(
                f"State mapping for channel '{name}' points to missing channel {target_id}; it will be recreated."
            )

        # A child whose template parent must be created cannot safely adopt a target channel yet.
        can_match_parent = parent_source_id is None or expected_parent_target_id is not None
        candidates: list[dict[str, Any]] = []
        if adopt_existing and can_match_parent:
            for target in target_channels:
                target_id = sid(target.get("id"))
                if target_id in used_channel_ids:
                    continue
                if target.get("name") != name:
                    continue
                if _int_or(target.get("type"), -1) != channel_type:
                    continue
                target_parent = target.get("parent_id")
                target_parent_id = None if target_parent is None else sid(target_parent)
                if target_parent_id != expected_parent_target_id:
                    continue
                candidates.append(target)

        if adopt_existing and len(candidates) == 1:
            target_id = sid(candidates[0]["id"])
            entry.update(action="reuse", target_id=target_id, owned=False, reason="exact channel/type/parent match")
            channel_resolution[source_id] = target_id
            used_channel_ids.add(target_id)
        elif adopt_existing and len(candidates) > 1:
            parent_label = _source_parent_label(parent_source_id, source_by_id)
            conflicts.append(
                f"Channel '{name}' under {parent_label} has {len(candidates)} exact matches; Forge will not guess."
            )
            entry.update(action="conflict", target_id=None, owned=False, reason="ambiguous exact matches")
            channel_resolution[source_id] = None
        else:
            entry.update(action="create", target_id=None, owned=True, reason="missing from target")
            channel_resolution[source_id] = None
        channel_entries.append(entry)

    mapped_role_targets = {
        sid(entry["target_id"])
        for entry in role_entries
        if entry.get("target_id") is not None
    }
    mapped_channel_targets = {
        sid(entry["target_id"])
        for entry in channel_entries
        if entry.get("target_id") is not None
    }

    extra_roles = [
        {
            "id": sid(role.get("id")),
            "name": role.get("name"),
            "managed": bool(role.get("managed", False)),
        }
        for role in target_roles
        if sid(role.get("id")) not in mapped_role_targets and sid(role.get("id")) != guild_id
    ]
    extra_channels = [
        {
            "id": sid(channel.get("id")),
            "name": channel.get("name"),
            "type": _int_or(channel.get("type"), -1),
            "type_name": channel_type_name(channel.get("type")),
        }
        for channel in target_channels
        if sid(channel.get("id")) not in mapped_channel_targets
    ]

    if any(entry.get("action") == "reuse" for entry in role_entries):
        warnings.append(
            "Mapped template roles are placed as a contiguous hierarchy directly below the Forge bot role; "
            "adopted role settings are preserved."
        )

    if any(entry.get("action") == "reuse" for entry in channel_entries):
        warnings.append(
            "Adopted channels/categories keep their existing settings and message history; "
            "Forge only repositions them when FORGE_PLACEMENT=top."
        )

    apply_code = approval_code("APPLY", sid(template.get("code")), guild_id)
    rollback_code = approval_code("ROLLBACK", sid(template.get("code")), guild_id)

    return {
        "version": 1,
        "template": {
            "code": sid(template.get("code")),
            "name": template.get("name"),
            "description": template.get("description"),
            "updated_at": template.get("updated_at"),
            "source_guild_id": sid(template.get("source_guild_id")),
            "source_guild_name": template_guild(template).get("name"),
        },
        "target": {
            "id": guild_id,
            "name": guild.get("name"),
        },
        "settings": {
            "adopt_existing": adopt_existing,
            "placement": placement,
        },
        "roles": role_entries,
        "channels": sorted(channel_entries, key=lambda entry: entry["source_index"]),
        "extras": {
            "roles": extra_roles,
            "channels": extra_channels,
        },
        "conflicts": conflicts,
        "warnings": warnings,
        "approval": {
            "apply": apply_code,
            "rollback": rollback_code,
        },
        "summary": {
            "roles_create": _count_action(role_entries, "create"),
            "roles_reuse": _count_actions(role_entries, {"reuse", "map"}),
            "roles_managed": _count_action(role_entries, "managed"),
            "channels_create": _count_action(channel_entries, "create"),
            "channels_reuse": _count_action(channel_entries, "reuse"),
            "channels_managed": _count_action(channel_entries, "managed"),
            "extra_roles_preserved": len(extra_roles),
            "extra_channels_preserved": len(extra_channels),
            "conflicts": len(conflicts),
            "warnings": len(warnings),
        },
    }


def role_payload(role: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": str(role.get("name", "new role"))[:100],
        "permissions": str(_int_or(role.get("permissions"), 0)),
        "color": max(0, _int_or(role.get("color"), 0)),
        "hoist": bool(role.get("hoist", False)),
        "mentionable": bool(role.get("mentionable", False)),
    }
    return payload


def channel_payload(
    channel: dict[str, Any],
    *,
    role_map: dict[str, str],
    channel_map: dict[str, str],
    include_type: bool,
) -> tuple[dict[str, Any], list[str]]:
    channel_type = _int_or(channel.get("type"), -1)
    if channel_type not in SUPPORTED_CHANNEL_TYPES:
        raise ValueError(f"Unsupported channel type: {channel_type}")

    warnings: list[str] = []
    payload: dict[str, Any] = {"name": str(channel.get("name", "new-channel"))[:100]}
    if include_type:
        payload["type"] = channel_type

    parent_source_id = channel.get("_parent_source_id")
    if parent_source_id is not None:
        parent_target_id = channel_map.get(sid(parent_source_id))
        if parent_target_id is None:
            raise ValueError(
                f"Parent category {parent_source_id} has not been provisioned for channel {channel.get('name')}."
            )
        payload["parent_id"] = parent_target_id
    elif channel_type != 4:
        payload["parent_id"] = None

    mapped_overwrites, overwrite_warnings = map_permission_overwrites(
        channel.get("permission_overwrites", []), role_map=role_map
    )
    warnings.extend(overwrite_warnings)
    payload["permission_overwrites"] = mapped_overwrites

    if channel_type in {0, 5, 15, 16}:
        _copy_non_null(payload, channel, "topic", "nsfw", "rate_limit_per_user", "default_auto_archive_duration")

    if channel_type in {2, 13}:
        bitrate = _int_or(channel.get("bitrate"), 64000)
        bitrate_cap = 64000 if channel_type == 13 else 96000
        if bitrate > bitrate_cap:
            warnings.append(
                f"Channel '{channel.get('name')}' bitrate {bitrate} was capped to {bitrate_cap} for target compatibility."
            )
            bitrate = bitrate_cap
        payload["bitrate"] = max(8000, bitrate)
        payload["user_limit"] = max(0, _int_or(channel.get("user_limit"), 0))
        _copy_non_null(payload, channel, "rtc_region", "video_quality_mode", "nsfw", "rate_limit_per_user")

    if channel_type in {15, 16}:
        tags = channel.get("available_tags")
        if isinstance(tags, list):
            payload["available_tags"] = [_sanitize_forum_tag(tag) for tag in tags if isinstance(tag, dict)]
        reaction = channel.get("default_reaction_emoji")
        if isinstance(reaction, dict):
            if reaction.get("emoji_id"):
                warnings.append(
                    f"Channel '{channel.get('name')}' uses a source-server custom default reaction; it was omitted."
                )
            elif reaction.get("emoji_name"):
                payload["default_reaction_emoji"] = {
                    "emoji_id": None,
                    "emoji_name": reaction.get("emoji_name"),
                }
        _copy_non_null(
            payload,
            channel,
            "default_sort_order",
            "default_thread_rate_limit_per_user",
        )
        if channel_type == 15:
            _copy_non_null(payload, channel, "default_forum_layout")

    return payload, warnings


def map_permission_overwrites(
    overwrites: Any,
    *,
    role_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(overwrites, list):
        return [], []

    mapped: list[dict[str, Any]] = []
    warnings: list[str] = []
    for overwrite in overwrites:
        if not isinstance(overwrite, dict):
            continue
        overwrite_type = _int_or(overwrite.get("type"), 0)
        source_target_id = sid(overwrite.get("id"))
        if overwrite_type != 0:
            raise ValueError(
                f"Template contains a member-specific permission overwrite for source id {source_target_id}; "
                "member identities are not portable between servers."
            )
        target_id = role_map.get(source_target_id)
        if target_id is None:
            raise ValueError(
                f"No target role mapping exists for permission overwrite source role {source_target_id}."
            )
        mapped.append(
            {
                "id": target_id,
                "type": 0,
                "allow": str(_int_or(overwrite.get("allow"), 0)),
                "deny": str(_int_or(overwrite.get("deny"), 0)),
            }
        )
    return mapped, warnings


def normalize_overwrites(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for overwrite in value:
        if not isinstance(overwrite, dict):
            continue
        normalized.append(
            {
                "id": sid(overwrite.get("id")),
                "type": _int_or(overwrite.get("type"), 0),
                "allow": str(_int_or(overwrite.get("allow"), 0)),
                "deny": str(_int_or(overwrite.get("deny"), 0)),
            }
        )
    return sorted(normalized, key=lambda item: (item["type"], item["id"]))


def plan_as_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, indent=2, sort_keys=True) + "\n"


def _base_role_entry(role: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": role["_source_id"],
        "source_index": _int_or(role.get("_source_index"), 0),
        "source_position": _int_or(role.get("_source_position"), 0),
        "name": role.get("name"),
        "action": None,
        "target_id": None,
        "owned": False,
        "reason": None,
    }


def _base_channel_entry(channel: dict[str, Any]) -> dict[str, Any]:
    channel_type = _int_or(channel.get("type"), -1)
    return {
        "source_id": channel["_source_id"],
        "source_index": _int_or(channel.get("_source_index"), 0),
        "source_position": _int_or(channel.get("_source_position"), 0),
        "parent_source_id": channel.get("_parent_source_id"),
        "name": channel.get("name"),
        "type": channel_type,
        "type_name": channel_type_name(channel_type),
        "action": None,
        "target_id": None,
        "owned": False,
        "reason": None,
    }


def _role_setting_differences(source: dict[str, Any], target: dict[str, Any]) -> list[str]:
    expected = role_payload(source)
    differences: list[str] = []
    for key in ("permissions", "color", "hoist", "mentionable"):
        actual = target.get(key)
        if key == "permissions":
            actual = str(_int_or(actual, 0))
        if actual != expected[key]:
            differences.append(key)
    return differences


def _source_parent_label(parent_source_id: str | None, source_by_id: dict[str, dict[str, Any]]) -> str:
    if parent_source_id is None:
        return "the server root"
    parent = source_by_id.get(parent_source_id)
    if parent is None:
        return f"source parent {parent_source_id}"
    return f"category '{parent.get('name')}'"


def _channel_sort_key(channel: dict[str, Any]) -> tuple[int, int]:
    return (
        _int_or(channel.get("_source_position"), 0),
        _int_or(channel.get("_source_index"), 0),
    )


def _count_action(entries: Iterable[dict[str, Any]], action: str) -> int:
    return sum(1 for entry in entries if entry.get("action") == action)


def _count_actions(entries: Iterable[dict[str, Any]], actions: set[str]) -> int:
    return sum(1 for entry in entries if entry.get("action") in actions)


def _copy_non_null(target: dict[str, Any], source: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if key in source and source[key] is not None:
            target[key] = source[key]


def _sanitize_forum_tag(tag: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": str(tag.get("name", "tag"))[:20],
        "moderated": bool(tag.get("moderated", False)),
    }
    if tag.get("emoji_name"):
        result["emoji_name"] = tag.get("emoji_name")
        result["emoji_id"] = None
    # Custom emoji IDs belong to the source guild and cannot be copied safely.
    return result


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
