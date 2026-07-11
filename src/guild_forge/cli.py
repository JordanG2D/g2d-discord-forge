from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .api import DiscordAPIError
from .config import Settings
from .engine import (
    ForgeError,
    apply,
    create_plan,
    generate_invite_url,
    inspect_template,
    rollback,
    verify,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guild-forge",
        description=(
            "Apply a Discord server template to an existing server without deleting preexisting content."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON where supported.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "inspect",
        help="Fetch the public source template and save its raw JSON snapshot.",
    )
    subparsers.add_parser(
        "invite-url",
        help="Generate the temporary-Administrator bot invite URL.",
    )
    subparsers.add_parser(
        "plan",
        help="Diff the template against the target server; makes no Discord changes.",
    )

    apply_parser = subparsers.add_parser(
        "apply",
        help="Create/update Forge-owned roles and channels after an explicit plan approval.",
    )
    apply_parser.add_argument(
        "--confirm",
        required=True,
        help="Exact APPLY approval code printed by the plan command.",
    )

    subparsers.add_parser(
        "verify",
        help="Check the live server against Forge state and the current source template.",
    )

    rollback_parser = subparsers.add_parser(
        "rollback",
        help="Delete only Forge-owned resources and restore pre-Forge channel placement.",
    )
    rollback_parser.add_argument(
        "--confirm",
        required=True,
        help="Exact ROLLBACK approval code printed by the plan command.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        if args.command == "inspect":
            result = inspect_template(settings)
            if args.json:
                _print_json(_json_safe(result))
            else:
                _print_inspect(result)
            return 0

        if args.command == "invite-url":
            url = generate_invite_url(settings)
            if args.json:
                _print_json({"invite_url": url})
            else:
                print(url)
            return 0

        if args.command == "plan":
            plan = create_plan(settings)
            if args.json:
                _print_json(plan)
            else:
                _print_plan(plan, settings.plan_file)
            return 2 if plan.get("conflicts") else 0

        if args.command == "apply":
            result = apply(settings, confirmation=args.confirm)
            if args.json:
                _print_json(_json_safe(result))
            else:
                _print_apply(result)
            return 0

        if args.command == "verify":
            result = verify(settings)
            if args.json:
                _print_json(_json_safe(result))
            else:
                _print_verify(result)
            return 0 if result["ok"] else 2

        if args.command == "rollback":
            result = rollback(settings, confirmation=args.confirm)
            if args.json:
                _print_json(_json_safe(result))
            else:
                _print_rollback(result)
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except (ValueError, ForgeError, DiscordAPIError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


def _print_inspect(result: dict[str, Any]) -> None:
    summary = result["summary"]
    print("Discord template inspected successfully")
    print(f"  Template:      {summary.get('template_name') or '(unnamed)'}")
    print(f"  Source server: {summary.get('source_server_name') or '(unnamed)'}")
    print(f"  Code:          {summary['code']}")
    print(f"  Updated:       {summary.get('updated_at')}")
    print(f"  Roles:         {summary['roles']}")
    print(f"  Channels:      {summary['channels']}")
    for channel_type, count in summary["channels_by_type"].items():
        print(f"    {channel_type:<14} {count}")
    print(f"  Raw snapshot:  {result['output']}")


def _print_plan(plan: dict[str, Any], plan_file: Path) -> None:
    summary = plan["summary"]
    template = plan["template"]
    target = plan["target"]
    print("Discord Forge plan")
    print(f"  Template: {template.get('name') or template.get('source_guild_name')} ({template['code']})")
    print(f"  Target:   {target.get('name')} ({target['id']})")
    print(
        f"  Mode:     preserve extras; roles=template block below bot; "
        f"channels={plan['settings']['placement']}"
    )
    print()
    print("Roles")
    print(f"  create={summary['roles_create']}  reuse={summary['roles_reuse']}  managed={summary['roles_managed']}")
    for entry in plan["roles"]:
        marker = _action_marker(entry["action"])
        target_suffix = f" -> {entry['target_id']}" if entry.get("target_id") else ""
        print(f"  {marker} {entry['name']}{target_suffix} [{entry['reason']}]")
    print()
    print("Channels")
    print(
        f"  create={summary['channels_create']}  reuse={summary['channels_reuse']}  "
        f"managed={summary['channels_managed']}"
    )
    for entry in plan["channels"]:
        marker = _action_marker(entry["action"])
        target_suffix = f" -> {entry['target_id']}" if entry.get("target_id") else ""
        print(
            f"  {marker} {entry['name']} ({entry['type_name']}){target_suffix} [{entry['reason']}]"
        )
    print()
    print(
        f"Preserved target-only resources: {summary['extra_roles_preserved']} role(s), "
        f"{summary['extra_channels_preserved']} channel(s)"
    )

    if plan["warnings"]:
        print("\nWarnings")
        for warning in plan["warnings"]:
            print(f"  - {warning}")
    if plan["conflicts"]:
        print("\nBLOCKING CONFLICTS")
        for conflict in plan["conflicts"]:
            print(f"  - {conflict}")
        print("\nNo changes were made.")
    else:
        print("\nNo blocking conflicts. No changes were made by plan.")
        print("Apply approval code:")
        print(f"  {plan['approval']['apply']}")
        print("Rollback approval code:")
        print(f"  {plan['approval']['rollback']}")
    print(f"\nFull plan: {plan_file}")


def _print_apply(result: dict[str, Any]) -> None:
    run = result["run"]
    print("Discord Forge apply succeeded")
    print(f"  Created roles:     {len(run['created_roles'])}")
    print(f"  Updated roles:     {len(run['updated_roles'])}")
    print(f"  Created channels:  {len(run['created_channels'])}")
    print(f"  Updated channels:  {len(run['updated_channels'])}")
    print(f"  State:             {result['state_file']}")
    if run["warnings"]:
        print("\nWarnings")
        for warning in _dedupe(run["warnings"]):
            print(f"  - {warning}")
    print("\nRun `guild-forge verify`, then remove Administrator from the bot role.")


def _print_verify(result: dict[str, Any]) -> None:
    if result["ok"]:
        print(f"VERIFY PASS: {result['target']['name']} matches Forge-owned template state.")
    else:
        print(f"VERIFY FAIL: {len(result['drift'])} drift item(s) found.")
        for item in result["drift"]:
            print(f"  - {item}")
    if result["warnings"]:
        print("\nNon-blocking adopted-resource warnings")
        for warning in _dedupe(result["warnings"]):
            print(f"  - {warning}")
    print(f"State: {result['state_file']}")


def _print_rollback(result: dict[str, Any]) -> None:
    print("Discord Forge rollback succeeded")
    print(f"  Deleted Forge channels: {len(result['deleted_channels'])}")
    print(f"  Deleted Forge roles:    {len(result['deleted_roles'])}")
    print(f"  Archived state:         {result['archived_state']}")
    if result["warnings"]:
        print("\nWarnings")
        for warning in _dedupe(result["warnings"]):
            print(f"  - {warning}")


def _action_marker(action: str) -> str:
    return {
        "map": "=",
        "reuse": "=",
        "managed": "~",
        "create": "+",
        "conflict": "!",
    }.get(action, "?")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
