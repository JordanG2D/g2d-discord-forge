# Security Policy

## Reporting a vulnerability

Do not publish bot tokens, real server IDs, private channel structures, `.env`, or live deployment files in a public issue.

Use GitHub private vulnerability reporting when enabled. Replace real Discord identifiers with synthetic values.

If a Discord bot token has been exposed, reset it immediately in the Discord Developer Portal.

## Sensitive data

Forge reads the bot token from `DISCORD_BOT_TOKEN`. Keep it in a local `.env`, secret manager, or protected runtime environment.

Files under `state/` can contain server, role, and channel IDs. Runtime JSON is excluded by `.gitignore` and should not be published.

Useful security reports include:

- Secret exposure
- Unauthorized resource mutation
- Rollback deleting non-Forge resources
- Permission-boundary bypass
- State ownership confusion
- Incorrect template-to-target ID translation
