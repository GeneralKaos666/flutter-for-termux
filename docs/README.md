# Documentation

This directory keeps project documentation out of the repository root while
leaving only the entry-point files there (`README.md`, `README_ZH.md`, and
agent/tooling instructions).

English is the primary documentation language. Each guide has a Chinese twin
suffixed `_ZH` (e.g. [INSTALL_GUIDE_ZH.md](guides/INSTALL_GUIDE_ZH.md)) linked
from the language toggle at the top of each page.

## Operations

- [CI/CD and device lab](CI_CD.md) — GitHub Actions workflow map, runner setup, and local equivalents.

## Guides

- [Install guide](guides/INSTALL_GUIDE.md) — Termux install flow and runtime prerequisites. ([中文](guides/INSTALL_GUIDE_ZH.md))
- [Build guide](guides/BUILD_GUIDE.md) — end-to-end build commands, troubleshooting, and packaging notes. ([中文](guides/BUILD_GUIDE_ZH.md))
- [Build process](guides/BUILD_PROCESS.md) — historical build steps and process notes. ([中文](guides/BUILD_PROCESS_ZH.md))
- [Upgrade guide](guides/UPGRADE_GUIDE.md) — checklist for moving to a new Flutter release. ([中文](guides/UPGRADE_GUIDE_ZH.md))

## Releases

- [Changelog](releases/CHANGELOG.md) — version history and notable fixes.
- [Release notes](releases/RELEASE_NOTES.md) — notes used for the current GitHub release body.
