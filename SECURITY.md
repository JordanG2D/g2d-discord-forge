# Security Policy

Do not publish bot tokens, real server IDs, private channel structures, `.env`, or live deployment files in a public issue.

Use GitHub private vulnerability reporting when available and replace real Discord identifiers with synthetic values. If a token is exposed, reset it immediately in the Discord Developer Portal.

Files under `state/` can contain server, role, and channel IDs. Runtime JSON is excluded by `.gitignore` and should not be published.
