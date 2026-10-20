# Documentation

This directory keeps project documentation out of the repository root while
leaving only the entry-point files there (`README.md` and agent/tooling
instructions).

New here? Install Flutter via the signed apt repository using the
[quick start](../README.md#quick-start) in the root README, then use the
guides below for everything else.

## Operations

- [CI/CD and device lab](CI_CD.md) — GitHub Actions workflow map, runner setup, and local equivalents.

## Guides

- [Install guide](guides/INSTALL_GUIDE.md) — Termux install flow, runtime prerequisites, first projects, and troubleshooting.
- [Build guide](guides/BUILD_GUIDE.md) — end-to-end build commands, troubleshooting, and packaging notes.
- [Build process](guides/BUILD_PROCESS.md) — build internals: pipeline stages, patches, GN toolchain, sysroot, testing, and release process.
- [Upgrade guide](guides/UPGRADE_GUIDE.md) — checklist for moving to a new Flutter release.

## Releases

- [Changelog](releases/CHANGELOG.md) — version history and notable fixes.
- [Release notes](releases/RELEASE_NOTES.md) — notes used for the current GitHub release body.
