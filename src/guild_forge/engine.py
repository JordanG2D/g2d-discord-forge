from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote, urlencode

from .api import DiscordAPI, DiscordAPIError
from .config import Settings
from .core import (
    approval_code,
    build_plan,
    channel_creation_order,
    channel_payload,
    channel_type_name,
    effective_guild_permissions,
    has_required_permissions,
    is_everyone_role,
    normalize_overwrites,
    role_payload,
    role_sort_bottom_to_top,
    role_sort_top_to_bottom,
    sid,
    source_channels,
    source_roles,
    template_guild,
)
from .state import (
    archive_state,
    initial_state,
    load_json,
    save_json_atomic,
    utc_now,
    validate_state_binding,
)


class ForgeError(RuntimeError):
    pass


def make_api(settings: Settings) -> DiscordAPI:
    return DiscordAPI(
        base_url=settings.api_base,
        token=settings.bot_token,
        timeout=settings.request_timeout,
        audit_reason=settings.audit_reason,
    )


def fetch_template(api: DiscordAPI, template_code: str) -> dict[str, Any]:
    payload = api.get(f"/guilds/templates/{quote(template_code, safe='')}", auth=False)
    if not isinstance(payload, dict):
        raise ForgeError("Discord returned an invalid guild template response.")
    return payload


def fetch_target_snapshot(
    api: DiscordAPI,
    guild_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    guild = api.get(f"/guilds/{guild_id}")
    roles = api.get(f"/guilds/{guild_id}/roles")
    channels = api.get(f"/guilds/{guild_id}/channels")
    if not isinstance(guild, dict) or not isinstance(roles, list) or not isinstance(channels, list):
        raise ForgeError("Discord returned an invalid target server snapshot.")
    return guild, roles, channels


def inspect_template(settings: Settings) -> dict[str, Any]:
    api = make_api(settings)
    template = fetch_template(api, settings.template_code)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    output = settings.state_dir / f"template-{settings.template_code}.json"
    save_json_atomic(output, template)
    return {
        "template": template,
        "output": output,
        "summary": template_summary(template),
    }


def template_summary(template: dict[str, Any]) -> dict[str, Any]:
    guild = template_guild(template)
    roles = source_roles(template)
    channels = source_channels(template)
    by_type: dict[str, int] = {}
    for channel in channels:
        label = channel_type_name(channel.get("type"))
        by_type[label] = by_type.get(label, 0) + 1
    return {
        "code": sid(template.get("code")),
        "template_name": template.get("name"),
        "source_server_name": guild.get("name"),
        "updated_at": template.get("updated_at"),
        "roles": len(roles),
        "channels": len(channels),
        "channels_by_type": dict(sorted(by_type.items())),
    }


def generate_invite_url(settings: Settings) -> str:
    application_id = settings.application_id
    if not application_id:
        settings.require_token()
        api = make_api(settings)
        application = api.get("/applications/@me")
        if not isinstance(application, dict) or not application.get("id"):
            raise ForgeError("Discord did not return an application ID for this bot token.")
        application_id = sid(application["id"])

    params: dict[str, str] = {
        "client_id": application_id,
        "permissions": "8",  # Temporary Administrator; needed to faithfully copy arbitrary overwrites.
        "scope": "bot",
    }
    if settings.target_guild_id:
        params["guild_id"] = settings.target_guild_id
        params["disable_guild_select"] = "true"
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def create_plan(settings: Settings, *, save: bool = True) -> dict[str, Any]:
    token = settings.require_token()
    del token  # Validation only; API reads it from Settings.
    guild_id = settings.require_guild_id()
    api = make_api(settings)
    template = fetch_template(api, settings.template_code)
    guild, roles, channels = fetch_target_snapshot(api, guild_id)
    state = load_json(settings.state_file)
    validate_state_binding(state, template_code=settings.template_code, guild_id=guild_id)

    plan = build_plan(
        template=template,
        guild=guild,
        target_roles=roles,
        target_channels=channels,
        state=state,
        adopt_existing=settings.adopt_existing,
        placement=settings.placement,
    )
    if save:
        settings.plan_file.parent.mkdir(parents=True, exist_ok=True)
        save_json_atomic(settings.plan_file, plan)
    return plan


def preflight_permissions(
    api: DiscordAPI,
    *,
    guild_id: str,
    roles: list[dict[str, Any]],
    require_admin: bool,
) -> dict[str, Any]:
    bot_user = api.get("/users/@me")
    if not isinstance(bot_user, dict) or not bot_user.get("id"):
        raise ForgeError("Discord did not return the bot user ID.")
    member = api.get(f"/guilds/{guild_id}/members/{bot_user['id']}")
    if not isinstance(member, dict):
        raise ForgeError("Discord did not return the bot's target-server member record.")
    permissions = effective_guild_permissions(guild_id, member.get("roles", []), roles)
    allowed, missing = has_required_permissions(permissions, require_admin)
    if not allowed:
        raise ForgeError(
            "The bot is missing required target-server permissions: " + ", ".join(missing) + ". "
            "Move its role above the roles it must create/manage and grant temporary Administrator access."
        )

    role_by_id = {sid(role.get("id")): role for role in roles}
    member_roles = [role_by_id.get(sid(role_id)) for role_id in member.get("roles", [])]
    highest = max((int(role.get("position", 0)) for role in member_roles if role), default=0)
    return {
        "permissions": permissions,
        "administrator": bool(permissions & (1 << 3)),
        "highest_role_position": highest,
    }


def apply(settings: Settings, *, confirmation: str) -> dict[str, Any]:
    guild_id = settings.require_guild_id()
    settings.require_token()
    api = make_api(settings)

    template = fetch_template(api, settings.template_code)
    guild, target_roles, target_channels = fetch_target_snapshot(api, guild_id)
    state = load_json(settings.state_file)
    validate_state_binding(state, template_code=settings.template_code, guild_id=guild_id)

    plan = build_plan(
        template=template,
        guild=guild,
        target_roles=target_roles,
        target_channels=target_channels,
        state=state,
        adopt_existing=settings.adopt_existing,
        placement=settings.placement,
    )
    settings.plan_file.parent.mkdir(parents=True, exist_ok=True)
    save_json_atomic(settings.plan_file, plan)

    if plan["conflicts"]:
        details = "\n".join(f"- {item}" for item in plan["conflicts"])
        raise ForgeError(f"Apply blocked by {len(plan['conflicts'])} conflict(s):\n{details}")

    expected_confirmation = plan["approval"]["apply"]
    if confirmation != expected_confirmation:
        raise ForgeError(
            f"Confirmation mismatch. Run plan and pass exactly: --confirm {expected_confirmation}"
        )

    preflight = preflight_permissions(
        api,
        guild_id=guild_id,
        roles=target_roles,
        require_admin=settings.require_admin,
    )
    _assert_bot_above_mapped_roles(plan, target_roles, preflight["highest_role_position"], guild_id)

    if state is None:
        state = initial_state(settings.template_code, guild_id)
    state.setdefault("roles", {})
    state.setdefault("channels", {})
    state.setdefault("runs", [])
    state.setdefault("baseline", {})

    if not state["baseline"].get("roles"):
        state["baseline"]["roles"] = {
            sid(role.get("id")): {
                "position": int(role.get("position", 0)),
                "managed": bool(role.get("managed", False)),
                "name": role.get("name"),
            }
            for role in target_roles
        }

    if not state["baseline"].get("channels"):
        state["baseline"]["channels"] = {
            sid(channel.get("id")): {
                "position": int(channel.get("position", 0)),
                "parent_id": None if channel.get("parent_id") is None else sid(channel.get("parent_id")),
                "type": int(channel.get("type", -1)),
            }
            for channel in target_channels
        }

    run = {
        "started_at": utc_now(),
        "finished_at": None,
        "status": "running",
        "template_updated_at": template.get("updated_at"),
        "created_roles": [],
        "updated_roles": [],
        "created_channels": [],
        "updated_channels": [],
        "warnings": list(plan.get("warnings", [])),
    }
    state["runs"].append(run)
    state["updated_at"] = utc_now()
    save_json_atomic(settings.state_file, state)

    role_sources = {role["_source_id"]: role for role in source_roles(template)}
    channel_sources = {channel["_source_id"]: channel for channel in source_channels(template)}
    role_entries = {entry["source_id"]: deepcopy(entry) for entry in plan["roles"]}
    channel_entries = {entry["source_id"]: deepcopy(entry) for entry in plan["channels"]}

    # Persist all non-create mappings first so a partial run can be resumed safely.
    for entry in plan["roles"]:
        if entry.get("target_id") is None:
            continue
        prior = state["roles"].get(entry["source_id"], {})
        state["roles"][entry["source_id"]] = {
            **(prior if isinstance(prior, dict) else {}),
            **_state_mapping(entry),
        }
    for entry in plan["channels"]:
        if entry.get("target_id") is None:
            continue
        prior = state["channels"].get(entry["source_id"], {})
        state["channels"][entry["source_id"]] = {
            **(prior if isinstance(prior, dict) else {}),
            **_state_mapping(entry),
        }
    save_json_atomic(settings.state_file, state)

    try:
        creation_sequence = _next_creation_sequence(state)

        # Create top-to-bottom so Discord's bottom insertion keeps the template's internal order.
        for role in role_sort_top_to_bottom(role_sources.values()):
            source_id = role["_source_id"]
            entry = role_entries[source_id]
            if is_everyone_role(role):
                continue
            if entry["action"] == "create":
                created = api.post(f"/guilds/{guild_id}/roles", role_payload(role))
                if not isinstance(created, dict) or not created.get("id"):
                    raise ForgeError(f"Discord did not return a created role for '{role.get('name')}'.")
                target_id = sid(created["id"])
                entry.update(target_id=target_id, action="managed", owned=True)
                state["roles"][source_id] = {
                    **_state_mapping(entry),
                    "created_at": utc_now(),
                    "created_sequence": creation_sequence,
                }
                creation_sequence += 1
                run["created_roles"].append({"id": target_id, "name": role.get("name")})
                save_json_atomic(settings.state_file, state)
            elif entry["action"] == "managed" and entry.get("owned"):
                target_id = sid(entry["target_id"])
                updated = api.patch(f"/guilds/{guild_id}/roles/{target_id}", role_payload(role))
                if not isinstance(updated, dict):
                    raise ForgeError(f"Discord did not return the updated role '{role.get('name')}'.")
                run["updated_roles"].append({"id": target_id, "name": role.get("name")})

        role_map = {
            source_id: sid(mapping["target_id"])
            for source_id, mapping in state["roles"].items()
            if isinstance(mapping, dict) and mapping.get("target_id") is not None
        }
        # Make absolutely sure @everyone maps even if an old state file omitted it.
        for role in role_sources.values():
            if is_everyone_role(role):
                role_map[role["_source_id"]] = guild_id

        _place_template_roles_below_bot(api, guild_id, role_sources, state)

        # Preload channel map with adopted/managed resources, then create categories before children.
        channel_map = {
            source_id: sid(mapping["target_id"])
            for source_id, mapping in state["channels"].items()
            if isinstance(mapping, dict) and mapping.get("target_id") is not None
        }

        for channel in channel_creation_order(channel_sources.values()):
            source_id = channel["_source_id"]
            entry = channel_entries[source_id]
            if entry["action"] == "create":
                payload, payload_warnings = channel_payload(
                    channel,
                    role_map=role_map,
                    channel_map=channel_map,
                    include_type=True,
                )
                run["warnings"].extend(payload_warnings)
                created = api.post(f"/guilds/{guild_id}/channels", payload)
                if not isinstance(created, dict) or not created.get("id"):
                    raise ForgeError(f"Discord did not return a created channel for '{channel.get('name')}'.")
                target_id = sid(created["id"])
                channel_map[source_id] = target_id
                entry.update(target_id=target_id, action="managed", owned=True)
                state["channels"][source_id] = {
                    **_state_mapping(entry),
                    "created_at": utc_now(),
                    "created_sequence": creation_sequence,
                }
                creation_sequence += 1
                run["created_channels"].append(
                    {"id": target_id, "name": channel.get("name"), "type": channel.get("type")}
                )
                save_json_atomic(settings.state_file, state)
            elif entry["action"] == "managed" and entry.get("owned"):
                target_id = sid(entry["target_id"])
                payload, payload_warnings = channel_payload(
                    channel,
                    role_map=role_map,
                    channel_map=channel_map,
                    include_type=False,
                )
                run["warnings"].extend(payload_warnings)
                updated = api.patch(f"/channels/{target_id}", payload)
                if not isinstance(updated, dict):
                    raise ForgeError(f"Discord did not return the updated channel '{channel.get('name')}'.")
                run["updated_channels"].append({"id": target_id, "name": channel.get("name")})

        if settings.placement == "top":
            _place_template_channels_at_top(
                api,
                guild_id=guild_id,
                channels=list(channel_sources.values()),
                channel_map=channel_map,
            )

        state["template_updated_at"] = template.get("updated_at")
        state["last_apply_at"] = utc_now()
        state["updated_at"] = utc_now()
        run["status"] = "succeeded"
        run["finished_at"] = utc_now()
        save_json_atomic(settings.state_file, state)

        return {
            "plan": plan,
            "state_file": settings.state_file,
            "preflight": preflight,
            "run": run,
        }
    except Exception as exc:
        run["status"] = "failed"
        run["finished_at"] = utc_now()
        run["error"] = str(exc)
        state["updated_at"] = utc_now()
        save_json_atomic(settings.state_file, state)
        raise


def verify(settings: Settings) -> dict[str, Any]:
    guild_id = settings.require_guild_id()
    settings.require_token()
    state = load_json(settings.state_file)
    if state is None:
        raise ForgeError(f"No Forge state exists at {settings.state_file}; run apply first.")
    validate_state_binding(state, template_code=settings.template_code, guild_id=guild_id)

    api = make_api(settings)
    template = fetch_template(api, settings.template_code)
    guild, target_roles, target_channels = fetch_target_snapshot(api, guild_id)
    role_by_id = {sid(role.get("id")): role for role in target_roles}
    channel_by_id = {sid(channel.get("id")): channel for channel in target_channels}
    role_map = {
        source_id: sid(mapping["target_id"])
        for source_id, mapping in state.get("roles", {}).items()
        if isinstance(mapping, dict) and mapping.get("target_id") is not None
    }
    channel_map = {
        source_id: sid(mapping["target_id"])
        for source_id, mapping in state.get("channels", {}).items()
        if isinstance(mapping, dict) and mapping.get("target_id") is not None
    }

    drift: list[str] = []
    warnings: list[str] = []

    for role in source_roles(template):
        source_id = role["_source_id"]
        if is_everyone_role(role):
            role_map[source_id] = guild_id
            continue
        mapping = state.get("roles", {}).get(source_id)
        if not isinstance(mapping, dict) or not mapping.get("target_id"):
            drift.append(f"Role '{role.get('name')}' has no state mapping.")
            continue
        target_id = sid(mapping["target_id"])
        target = role_by_id.get(target_id)
        if target is None:
            drift.append(f"Role '{role.get('name')}' ({target_id}) is missing.")
            continue
        if mapping.get("owned", False):
            expected = role_payload(role)
            for key in ("name", "permissions", "color", "hoist", "mentionable"):
                actual = target.get(key)
                if key == "permissions":
                    actual = str(actual)
                if actual != expected[key]:
                    drift.append(
                        f"Owned role '{role.get('name')}' field {key} drifted: expected {expected[key]!r}, got {actual!r}."
                    )
        elif target.get("name") != role.get("name"):
            warnings.append(
                f"Adopted role {target_id} is named '{target.get('name')}', not template name '{role.get('name')}'."
            )

    ordered_template_roles = [
        role for role in role_sort_bottom_to_top(source_roles(template)) if not is_everyone_role(role)
    ]
    mapped_positions: list[tuple[str, int]] = []
    for role in ordered_template_roles:
        target_id = role_map.get(role["_source_id"])
        target = role_by_id.get(target_id) if target_id is not None else None
        if target is not None:
            mapped_positions.append((str(role.get("name")), int(target.get("position", 0))))
    for lower, higher in zip(mapped_positions, mapped_positions[1:]):
        if lower[1] >= higher[1]:
            drift.append(
                f"Template role hierarchy drifted: '{higher[0]}' must be above '{lower[0]}'."
            )

    for channel in source_channels(template):
        source_id = channel["_source_id"]
        mapping = state.get("channels", {}).get(source_id)
        if not isinstance(mapping, dict) or not mapping.get("target_id"):
            drift.append(f"Channel '{channel.get('name')}' has no state mapping.")
            continue
        target_id = sid(mapping["target_id"])
        target = channel_by_id.get(target_id)
        if target is None:
            drift.append(f"Channel '{channel.get('name')}' ({target_id}) is missing.")
            continue
        if int(target.get("type", -1)) != int(channel.get("type", -1)):
            drift.append(
                f"Channel '{channel.get('name')}' type drifted: expected {channel_type_name(channel.get('type'))}, "
                f"got {channel_type_name(target.get('type'))}."
            )
            continue

        expected_parent = None
        parent_source_id = channel.get("_parent_source_id")
        if parent_source_id is not None:
            expected_parent = channel_map.get(sid(parent_source_id))
        actual_parent = None if target.get("parent_id") is None else sid(target.get("parent_id"))

        if mapping.get("owned", False):
            try:
                expected, payload_warnings = channel_payload(
                    channel,
                    role_map=role_map,
                    channel_map=channel_map,
                    include_type=False,
                )
                warnings.extend(payload_warnings)
            except ValueError as exc:
                drift.append(f"Channel '{channel.get('name')}' cannot be verified: {exc}")
                continue
            _compare_owned_channel(channel.get("name"), expected, target, drift)
        else:
            if target.get("name") != channel.get("name"):
                warnings.append(
                    f"Adopted channel {target_id} is named '{target.get('name')}', not template name '{channel.get('name')}'."
                )
            if actual_parent != expected_parent:
                warnings.append(
                    f"Adopted channel '{target.get('name')}' is not under its template parent; Forge preserves adopted content."
                )

    return {
        "target": {"id": guild_id, "name": guild.get("name")},
        "template": template_summary(template),
        "state_file": settings.state_file,
        "drift": drift,
        "warnings": warnings,
        "ok": not drift,
    }


def rollback(settings: Settings, *, confirmation: str) -> dict[str, Any]:
    guild_id = settings.require_guild_id()
    settings.require_token()
    state = load_json(settings.state_file)
    if state is None:
        raise ForgeError(f"No Forge state exists at {settings.state_file}; nothing to roll back.")
    validate_state_binding(state, template_code=settings.template_code, guild_id=guild_id)

    expected = _rollback_code(settings.template_code, guild_id)
    if confirmation != expected:
        raise ForgeError(f"Confirmation mismatch. Pass exactly: --confirm {expected}")

    api = make_api(settings)
    _, target_roles, target_channels = fetch_target_snapshot(api, guild_id)
    preflight = preflight_permissions(
        api,
        guild_id=guild_id,
        roles=target_roles,
        require_admin=settings.require_admin,
    )
    channel_by_id = {sid(channel.get("id")): channel for channel in target_channels}
    role_by_id = {sid(role.get("id")): role for role in target_roles}

    deleted_channels: list[dict[str, Any]] = []
    deleted_roles: list[dict[str, Any]] = []
    warnings: list[str] = []

    owned_channels: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for source_id, mapping in state.get("channels", {}).items():
        if not isinstance(mapping, dict) or not mapping.get("owned", False):
            continue
        target_id = sid(mapping.get("target_id"))
        owned_channels.append((target_id, mapping, channel_by_id.get(target_id)))
    owned_channels.sort(
        key=lambda item: (
            1 if item[2] and int(item[2].get("type", -1)) == 4 else 0,
            -int(item[1].get("created_sequence", 0)),
        )
    )

    for target_id, mapping, current in owned_channels:
        if current is None:
            warnings.append(f"Owned channel {target_id} was already missing.")
            continue
        try:
            api.delete(f"/channels/{target_id}")
            deleted_channels.append({"id": target_id, "name": current.get("name")})
        except DiscordAPIError as exc:
            if exc.status == 404:
                warnings.append(f"Owned channel {target_id} disappeared during rollback.")
            else:
                raise

    owned_roles: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for source_id, mapping in state.get("roles", {}).items():
        if not isinstance(mapping, dict) or not mapping.get("owned", False):
            continue
        target_id = sid(mapping.get("target_id"))
        owned_roles.append((target_id, mapping, role_by_id.get(target_id)))
    owned_roles.sort(key=lambda item: -int(item[1].get("created_sequence", 0)))

    for target_id, mapping, current in owned_roles:
        if current is None:
            warnings.append(f"Owned role {target_id} was already missing.")
            continue
        try:
            api.delete(f"/guilds/{guild_id}/roles/{target_id}")
            deleted_roles.append({"id": target_id, "name": current.get("name")})
        except DiscordAPIError as exc:
            if exc.status == 404:
                warnings.append(f"Owned role {target_id} disappeared during rollback.")
            else:
                raise

    _restore_baseline_role_positions(api, guild_id, state, warnings)
    _restore_baseline_channel_positions(api, guild_id, state, warnings)
    archived = archive_state(settings.state_file)

    return {
        "preflight": preflight,
        "deleted_channels": deleted_channels,
        "deleted_roles": deleted_roles,
        "warnings": warnings,
        "archived_state": archived,
    }


def _state_mapping(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_id": sid(entry["target_id"]),
        "owned": bool(entry.get("owned", False)),
        "name": entry.get("name"),
        "source_position": entry.get("source_position"),
        "source_index": entry.get("source_index"),
        "type": entry.get("type"),
        "parent_source_id": entry.get("parent_source_id"),
    }


def _next_creation_sequence(state: dict[str, Any]) -> int:
    values: list[int] = []
    for group in (state.get("roles", {}), state.get("channels", {})):
        if not isinstance(group, dict):
            continue
        for mapping in group.values():
            if isinstance(mapping, dict):
                try:
                    values.append(int(mapping.get("created_sequence", 0)))
                except (TypeError, ValueError):
                    pass
    return max(values, default=0) + 1


def _assert_bot_above_mapped_roles(
    plan: dict[str, Any],
    target_roles: list[dict[str, Any]],
    highest_bot_position: int,
    guild_id: str,
) -> None:
    role_by_id = {sid(role.get("id")): role for role in target_roles}
    blocked: list[str] = []
    for entry in plan.get("roles", []):
        target_id = entry.get("target_id")
        if target_id is None or sid(target_id) == sid(guild_id):
            continue
        target = role_by_id.get(sid(target_id))
        if target is None:
            continue
        if int(target.get("position", 0)) >= highest_bot_position:
            blocked.append(str(target.get("name") or target_id))
    if blocked:
        raise ForgeError(
            "Move the Forge bot role above these mapped target roles before apply: "
            + ", ".join(blocked)
        )


def _place_template_roles_below_bot(
    api: DiscordAPI,
    guild_id: str,
    role_sources: dict[str, dict[str, Any]],
    state: dict[str, Any],
) -> None:
    mapped: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for role in role_sort_bottom_to_top(role_sources.values()):
        if is_everyone_role(role):
            continue

        mapping = state.get("roles", {}).get(role["_source_id"])
        if isinstance(mapping, dict) and mapping.get("target_id"):
            mapped.append((role, mapping))

    if not mapped:
        return

    current_roles = api.get(f"/guilds/{guild_id}/roles")
    bot_user = api.get("/users/@me")

    if not isinstance(current_roles, list):
        raise ForgeError("Discord did not return the target server roles.")

    if not isinstance(bot_user, dict) or not bot_user.get("id"):
        raise ForgeError("Discord did not return the bot user ID.")

    bot_id = sid(bot_user["id"])
    member = api.get(f"/guilds/{guild_id}/members/{bot_id}")

    if not isinstance(member, dict):
        raise ForgeError("Discord did not return the bot's server member record.")

    role_by_id = {
        sid(role.get("id")): role
        for role in current_roles
        if role.get("id") is not None
    }

    bot_role_ids = {
        sid(role_id)
        for role_id in member.get("roles", [])
    }

    bot_positions = [
        int(role_by_id[role_id].get("position", 0))
        for role_id in bot_role_ids
        if role_id in role_by_id
    ]

    highest_bot_position = max(bot_positions, default=0)

    if highest_bot_position <= 0:
        raise ForgeError(
            "Could not determine the Forge bot role position. "
            "Move the bot role above the roles it must manage."
        )

    blocked: list[str] = []

    for source_role, mapping in mapped:
        target_id = sid(mapping["target_id"])
        target_role = role_by_id.get(target_id)
        role_name = str(source_role.get("name") or target_id)

        if target_role is None:
            raise ForgeError(
                f"Mapped target role '{role_name}' no longer exists."
            )

        if target_id in bot_role_ids:
            blocked.append(f"{role_name} (Forge bot role)")
            continue

        if bool(target_role.get("managed", False)):
            blocked.append(f"{role_name} (integration-managed)")
            continue

        if int(target_role.get("position", 0)) >= highest_bot_position:
            blocked.append(f"{role_name} (above or equal to Forge)")

    if blocked:
        raise ForgeError(
            "Forge cannot reorder these mapped roles: "
            + ", ".join(blocked)
            + ". Move Forge above them or disable adoption of managed roles."
        )

    if highest_bot_position <= len(mapped):
        raise ForgeError(
            "There are not enough editable role positions beneath the Forge "
            "bot role. Move Forge to the top of the role hierarchy and rerun."
        )

    first_position = highest_bot_position - len(mapped)

    payload = [
        {
            "id": sid(mapping["target_id"]),
            "position": first_position + offset,
        }
        for offset, (_, mapping) in enumerate(mapped)
    ]

    updated = api.patch(f"/guilds/{guild_id}/roles", payload)

    if not isinstance(updated, list):
        raise ForgeError(
            "Discord did not return the updated role hierarchy."
        )

def _place_template_channels_at_top(
    api: DiscordAPI,
    *,
    guild_id: str,
    channels: list[dict[str, Any]],
    channel_map: dict[str, str],
) -> None:
    # Parent/category relationships and permission overwrites were already
    # applied during each channel create/update request. The final pass must
    # change positions only because Discord rejects multiple parent_id changes
    # in one bulk channel-position request.
    top_level = sorted(
        [channel for channel in channels if channel.get("_parent_source_id") is None],
        key=lambda channel: (
            int(channel.get("_source_position", 0)),
            int(channel.get("_source_index", 0)),
        ),
    )

    by_parent: dict[str, list[dict[str, Any]]] = {}
    for channel in channels:
        parent_source_id = channel.get("_parent_source_id")
        if parent_source_id is None:
            continue
        by_parent.setdefault(sid(parent_source_id), []).append(channel)

    payload: list[dict[str, Any]] = []

    for position, channel in enumerate(top_level):
        target_id = channel_map.get(channel["_source_id"])
        if target_id is not None:
            payload.append({
                "id": target_id,
                "position": position,
            })

    for parent_source_id, children in by_parent.items():
        if channel_map.get(parent_source_id) is None:
            continue

        ordered_children = sorted(
            children,
            key=lambda item: (
                int(item.get("_source_position", 0)),
                int(item.get("_source_index", 0)),
            ),
        )

        for position, channel in enumerate(ordered_children):
            target_id = channel_map.get(channel["_source_id"])
            if target_id is not None:
                payload.append({
                    "id": target_id,
                    "position": position,
                })

    if payload:
        api.patch(f"/guilds/{guild_id}/channels", payload)

def _compare_owned_channel(
    channel_name: Any,
    expected: dict[str, Any],
    actual: dict[str, Any],
    drift: list[str],
) -> None:
    numeric_keys = {
        "bitrate",
        "user_limit",
        "rate_limit_per_user",
        "default_auto_archive_duration",
        "video_quality_mode",
        "default_sort_order",
        "default_forum_layout",
        "default_thread_rate_limit_per_user",
    }
    for key, expected_value in expected.items():
        if key == "permission_overwrites":
            actual_value = normalize_overwrites(actual.get(key))
            expected_normalized = normalize_overwrites(expected_value)
            if actual_value != expected_normalized:
                drift.append(f"Owned channel '{channel_name}' permission overwrites drifted.")
            continue
        if key == "available_tags":
            expected_tags = _normalize_tags(expected_value)
            actual_tags = _normalize_tags(actual.get(key))
            if expected_tags != actual_tags:
                drift.append(f"Owned channel '{channel_name}' forum/media tags drifted.")
            continue
        actual_value = actual.get(key)
        if key in numeric_keys:
            try:
                actual_value = int(actual_value)
            except (TypeError, ValueError):
                pass
        if key == "parent_id":
            actual_value = None if actual_value is None else sid(actual_value)
            expected_value = None if expected_value is None else sid(expected_value)
        if actual_value != expected_value:
            drift.append(
                f"Owned channel '{channel_name}' field {key} drifted: expected {expected_value!r}, got {actual_value!r}."
            )


def _normalize_tags(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for tag in value:
        if not isinstance(tag, dict):
            continue
        result.append(
            {
                "name": tag.get("name"),
                "moderated": bool(tag.get("moderated", False)),
                "emoji_name": tag.get("emoji_name"),
            }
        )
    return sorted(result, key=lambda item: str(item["name"]))


def _restore_baseline_role_positions(
    api: DiscordAPI,
    guild_id: str,
    state: dict[str, Any],
    warnings: list[str],
) -> None:
    baseline = state.get("baseline", {}).get("roles", {})
    if not isinstance(baseline, dict) or not baseline:
        return
    current = api.get(f"/guilds/{guild_id}/roles")
    if not isinstance(current, list):
        warnings.append("Could not restore baseline role positions: invalid role snapshot.")
        return
    current_ids = {sid(role.get("id")) for role in current}
    payload: list[dict[str, Any]] = []
    for role_id, item in baseline.items():
        if role_id == sid(guild_id) or role_id not in current_ids or not isinstance(item, dict):
            continue
        if bool(item.get("managed", False)):
            continue
        payload.append({"id": role_id, "position": int(item.get("position", 0))})
    if payload:
        api.patch(f"/guilds/{guild_id}/roles", payload)


def _restore_baseline_channel_positions(
    api: DiscordAPI,
    guild_id: str,
    state: dict[str, Any],
    warnings: list[str],
) -> None:
    baseline = state.get("baseline", {}).get("channels", {})
    if not isinstance(baseline, dict) or not baseline:
        return
    current = api.get(f"/guilds/{guild_id}/channels")
    if not isinstance(current, list):
        warnings.append("Could not restore baseline channel positions: invalid channel snapshot.")
        return
    current_ids = {sid(channel.get("id")) for channel in current}
    payload: list[dict[str, Any]] = []
    for channel_id, item in baseline.items():
        if channel_id not in current_ids or not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {
            "id": channel_id,
            "position": int(item.get("position", 0)),
        }
        if int(item.get("type", -1)) != 4:
            entry["parent_id"] = item.get("parent_id")
            entry["lock_permissions"] = False
        payload.append(entry)
    if payload:
        api.patch(f"/guilds/{guild_id}/channels", payload)


def _rollback_code(template_code: str, guild_id: str) -> str:
    return approval_code("ROLLBACK", template_code, guild_id)
