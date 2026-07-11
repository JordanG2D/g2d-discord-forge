# G2D Discord Forge

A deterministic Discord template provisioner that applies a Discord server template to an **existing server** without deleting its current members, messages, roles, or channels.

Discord’s native template flow creates a new server. G2D Discord Forge fills the missing operational gap by reading a live template, comparing it with an existing target server, and safely provisioning the template structure into that server.

Built by **Gen 2 Dynamics** and released under the MIT License.

## Lifecycle

```text
inspect -> plan -> apply -> verify -> optional rollback
```

Forge can create roles, categories, text channels, voice channels, permission overwrites, and role mappings while preserving preexisting content. It reuses only exact, unambiguous matches, writes crash-safe state, and rolls back only Forge-owned resources.

Forge does not copy message history, members, third-party bots, bot configuration, secrets, integrations, webhooks, or server ownership.

## Important template warning

A Discord template depends on its source server. Keep the source server and template alive until `inspect`, `plan`, `apply`, and `verify` are complete. Deleting the source server or template can produce `Discord code 10057: Unknown server template`. This does not damage the destination server, but Forge can no longer retrieve the live template.

## Security

Never commit `.env`, `DISCORD_BOT_TOKEN`, or real deployment JSON under `state/`. If a bot token is exposed, reset it immediately in the Discord Developer Portal.

## Requirements

- Docker with Docker Compose, or Python 3.11+
- A Discord application and bot token
- A live Discord template URL or raw template code
- The numeric ID of an existing target Discord server
- Permission to manage that server

## Docker installation

```bash
git clone https://github.com/JordanG2D/g2d-discord-forge.git
cd g2d-discord-forge
cp .env.example .env
nano .env
docker compose build
```

Required `.env` values:

```dotenv
DISCORD_BOT_TOKEN=your_private_bot_token
TARGET_GUILD_ID=your_existing_server_id
DISCORD_TEMPLATE=https://discord.new/your_template_code
```

## Native Python installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
set -a
source .env
set +a
```

## Usage

Generate the temporary-Administrator invite URL:

```bash
docker compose run --rm forge invite-url
```

Invite Forge to the existing target server and move its role above every role it must manage. Administrator does not bypass Discord role hierarchy.

Inspect the template:

```bash
docker compose run --rm forge inspect
```

Create a no-change plan:

```bash
docker compose run --rm forge plan
```

Apply with the exact approval code printed by the latest plan:

```bash
docker compose run --rm forge apply --confirm APPLY-XXXX-...
```

Verify:

```bash
docker compose run --rm forge verify
```

After verification, remove Administrator from Forge or remove the bot entirely.

Rollback with the exact rollback code printed by `plan`:

```bash
docker compose run --rm forge rollback --confirm ROLLBACK-XXXX-...
```

Rollback removes only resources recorded as Forge-owned.

## Machine-readable output

Place `--json` before the subcommand:

```bash
docker compose run --rm forge --json plan
docker compose run --rm forge --json verify
```

## Configuration

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `DISCORD_BOT_TOKEN` | Yes | None | Private Discord bot token |
| `TARGET_GUILD_ID` | Yes | None | Existing destination server ID |
| `DISCORD_TEMPLATE` | Yes | None | Template URL or raw template code |
| `DISCORD_APPLICATION_ID` | No | Derived | Discord application ID |
| `FORGE_PLACEMENT` | No | `top` | Imported channel placement |
| `FORGE_ADOPT_EXISTING` | No | `true` | Reuse exact unambiguous matches |
| `FORGE_REQUIRE_ADMIN` | No | `true` | Require temporary Administrator |
| `FORGE_STATE_DIR` | No | `./state` | Runtime state directory |
| `FORGE_REQUEST_TIMEOUT` | No | `30` | API timeout in seconds |
| `FORGE_AUDIT_REASON` | No | Project default | Discord audit-log reason |

## State and recovery

Forge writes deployment records under `state/`; runtime JSON files are ignored by Git. If an apply is interrupted, keep `state/`, rerun `plan`, review the current diff, and retry with the newly printed confirmation code.

The bot appearing offline after a command is normal. Forge is a one-shot CLI process, not a continuously running Discord bot.

## Testing

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

The test suite uses a fake Discord API and does not contact Discord.

## License

MIT License. Copyright © 2026 Gen 2 Dynamics.
