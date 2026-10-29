# Documentation

Project documentation lives here. The repository root keeps only `README.md`
and the agent/tooling instructions.

New here? Install Flutter from the signed apt repository using the
[quick start](../README.md#quick-start), then come back to the guides below.

## Operations

- [CI/CD and device lab](CI_CD.md) — GitHub Actions workflows, runner setup, and the same checks run locally.

## Guides

- [Install guide](guides/INSTALL_GUIDE.md) — Termux install flow, first projects, and troubleshooting.
- [Build guide](guides/BUILD_GUIDE.md) — building the deb from source, step by step.
- [Build process](guides/BUILD_PROCESS.md) — build internals: pipeline stages, patches, GN toolchain, sysroot, testing, and release process.
- [Upgrade guide](guides/UPGRADE_GUIDE.md) — checklist for moving to a new Flutter release.
- [AAPT2 analysis](guides/AAPT2_RELEASE_BUILD_BUG_ANALYSIS.md) — the resource-stripping bug behind the on-device build workaround.

## Releases

- [Changelog](releases/CHANGELOG.md) — version history and notable fixes.
- [Release notes](releases/RELEASE_NOTES.md) — notes used for the current GitHub release body.
