# Contributing

Contributions are welcome.

## Setup

```bash
git clone https://github.com/JordanG2D/g2d-discord-forge.git
cd g2d-discord-forge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Tests

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

## Rules

- Never commit a Discord bot token or real `.env`.
- Never commit real deployment JSON from `state/`.
- Use synthetic Discord IDs and template codes in tests.
- Add tests for behavioral changes.
- Preserve dry-run planning before mutation.
- Preserve explicit confirmation codes.
- Preserve Forge-only rollback ownership.
- Do not weaken role hierarchy or permission checks merely to silence a test.

For live testing, use a disposable Discord server and a dedicated test bot.
