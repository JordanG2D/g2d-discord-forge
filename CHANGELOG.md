# Changelog

## [1.0.0] - 2026-07-11

### Added

- Live template inspection
- Dry-run planning and conflict detection
- Explicit apply and rollback confirmation codes
- Unambiguous adoption of existing roles and channels
- Permission-overwrite role ID translation
- Crash-safe state checkpoints
- Verification and Forge-owned rollback
- Docker and native Python execution
- JSON output mode
- MIT license and public documentation
- GitHub Actions tests on Python 3.11 and 3.12

### Fixed

- Resolve the bot’s numeric user ID before guild-member lookup
- Restore template role hierarchy beneath the Forge bot
- Block integration-managed role collisions
- Update final channel positions without batching multiple parent changes
- Cap voice bitrate to destination-server limits
- Resume partial imports from recorded state
