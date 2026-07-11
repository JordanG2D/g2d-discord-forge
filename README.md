# G2D Discord Forge

A deterministic Discord template provisioner that applies a Discord server template to an **existing server** without deleting its current members, messages, roles, or channels.

Discord’s native template flow creates a new server. G2D Discord Forge fills the missing operational gap by reading a live template, comparing it with an existing target server, and safely provisioning the template structure into that server.

Built by **Gen 2 Dynamics** and released under the MIT License.

## What it does

```text
inspect -> plan -> apply -> verify -> optional rollback
```

Forge can:

- Read a live Discord server template.
- Create template roles, categories, text channels, and voice channels.
- Translate template role IDs into destination-server role IDs.
- Rebuild channel permission overwrites.
- Preserve preexisting members, messages, roles, and channels.
- Reuse exact, unambiguous existing matches.
- Block ambiguous matches instead of guessing.
- Save crash-safe deployment state.
- Verify the live server against the imported structure.
- Roll back only resources recorded as Forge-owned.

Forge does **not** copy message history, members, bots, bot configuration, secrets, integrations, webhooks, or server ownership.

## Important template warning

A Discord template depends on its source server. Keep the source server and template alive until you complete:

```text
inspect
plan
apply
verify
```

Deleting the source server or template can produce:

```text
Discord code 10057: Unknown server template
```

That does not delete or damage the destination server, but Forge can no longer retrieve the live template.

## Security

Never commit or share:

- `.env`
- `DISCORD_BOT_TOKEN`
- Real deployment JSON under `state/`
- Screenshots or logs containing tokens

If a token is exposed, reset it immediately in the Discord Developer Portal.

## Requirements

- Docker with Docker Compose, or Python 3.11+
- A Discord application and bot token
- A live Discord template URL or raw template code
- The numeric ID of an existing target Discord server
- Permission to manage that server

## Discord setup

1. Create an application in the Discord Developer Portal.
2. Add a bot and copy its token.
3. Enable Developer Mode in Discord.
4. Right-click the target server and choose **Copy Server ID**.
5. Generate a server template from the source server and copy its URL.

Forge does not require privileged message-content or member intents.

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
git clone https://github.com/JordanG2D/g2d-discord-forge.git
cd g2d-discord-forge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
```

Load the environment before running the native CLI:

```bash
set -a
source .env
set +a
```

## Usage

### Generate the bot invite URL

```bash
docker compose run --rm forge invite-url
```

Open the URL and invite Forge to the **existing target server**.

The generated invite temporarily requests Administrator so Forge can reproduce arbitrary channel permission overwrites. Discord role hierarchy still applies, so move the Forge role above every role it must create, edit, adopt, or reorder.

### Inspect the template

```bash
docker compose run --rm forge inspect
```

This fetches and caches the template. It does not modify the target server.

### Generate a dry-run plan

```bash
docker compose run --rm forge plan
```

The plan reports proposed creates, reuse decisions, warnings, conflicts, and exact approval codes. It makes no Discord changes.

### Apply

Use the exact code printed by the latest plan:

```bash
docker compose run --rm forge apply --confirm APPLY-XXXX-...
```

Do not invent, shorten, or reuse a code from another server or template.

### Verify

```bash
docker compose run --rm forge verify
```

A successful result begins with:

```text
VERIFY PASS
```

After verification, remove Administrator from Forge or remove the bot entirely.

### Roll back

First run `plan` to obtain the current rollback code, then:

```bash
docker compose run --rm forge rollback --confirm ROLLBACK-XXXX-...
```

Rollback removes only resources recorded as Forge-owned.

## Machine-readable output

Place `--json` before the subcommand:

```bash
docker compose run --rm forge --json inspect
docker compose run --rm forge --json plan
docker compose run --rm forge --json verify
```

Native examples:

```bash
guild-forge --json plan
guild-forge --json verify
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

Forge writes deployment records under `state/`. Runtime JSON files are ignored by Git.

If an apply is interrupted:

1. Do not delete `state/`.
2. Run `docker compose run --rm forge plan`.
3. Review the new plan.
4. Retry using the exact current confirmation code.

The bot appearing offline after a command is normal. Forge is a one-shot CLI process, not a continuously running Discord bot.

## Common errors

### `50013: Missing Permissions`

Check that Forge temporarily has Administrator and that its role is above all roles it must manage. Administrator does not bypass role hierarchy.

### `10057: Unknown server template`

The template URL no longer resolves. Check whether the source server or template was deleted or the code was regenerated.

### Confirmation mismatch

Run `plan` again and copy the exact current code.

## Testing

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

The test suite uses a fake Discord API and does not contact Discord.

## License

MIT License.

Copyright © 2026 Gen 2 Dynamics.
