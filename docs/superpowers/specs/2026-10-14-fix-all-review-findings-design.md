# Design Spec: Fix All Review Findings

**Date:** 2026-10-14 (review of HEAD `1051633`)
**Status:** approved

## Problem & Goal

A whole-repo review of `flutter-for-termux` found 46 issues across four surfaces: Python core (build/packaging), shell/device scripts, CI/config, and documentation. One further instance of the snapshot-honesty defect (row 47) was found during implementation. All existing gates pass (`ruff`, `pytest` 34 tests, `check_repo`, `check_version_drift`, `generate_versions --check`, `bash -n`, `shellcheck --severity=error`), so none of these are caught today.

**Goal:** resolve all 46 findings, keeping every gate green, split into reviewable PRs. Shipping-artifact changes bump `pkg_rel 4 → 5` once.

## Constraints

- Every PR must pass: `ruff check build.py package.py sysroot.py utils.py test_build.py scripts/ci/`, `python -m py_compile …`, `pytest test_build.py`, `bash -n`, `shellcheck --severity=error`, `python scripts/ci/check_repo.py`, `python scripts/ci/check_version_drift.py`, `python scripts/ci/generate_versions.py --check`, `git diff --check`.
- Version drift is enforced repo-wide; `pkg_rel` bump must propagate via `check_version_drift.py --fix`.
- No new deb SHA256 is pinned (unknown until the next build).
- `main` is protected: PR + linear history + required checks ("Repository sanity", "Verify release metadata").
- Conventional Commits; one branch per PR.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Doc vs code contradictions | Decided per finding (see table) | Neither side is uniformly authoritative |
| NDK backup/restore duplication | Keep both copies; add cross-reference + CI drift check | Full extraction would change the deb's packaged file set or make `post_install.sh` depend on a fetched helper — both riskier than a drift guard |
| `openjdk-21` | Docs only — no `Depends`/`Recommends` change | The one-command installer already installs `openjdk-21` (`install_termux_flutter.sh:46`, overridable via `JAVA_PACKAGE`); a hard dep would force ~200 MB on desktop-only users |
| Deb output path | Set `build.toml [package] path = 'release'` | Matches the docs and `.gitignore:release/`; `output()` already uses `self.release` and CI is path-agnostic |
| Release hashes | `check_version_drift.py --fix` only; no hash pinning | The new hash cannot exist before the build |
| PR partition | Four thematic PRs | Shipped-artifact changes must be validated by one build; behavior-preserving refactors stay separate so a bisect stays meaningful |

## PR Partition

1. **PR1 `fix(shipping)`** — runtime defects, packaging, `pkg_rel 4→5`.
2. **PR2 `docs(contract)`** — all doc↔code contradictions (docs-only).
3. **PR3 `refactor(python)`** — Python smells, behavior-preserving.
4. **PR4 `chore(ci)`** — shell smells + CI/config hardening.

## Per-Finding Plan

| # | Finding (ref) | PR | Action | Side |
|---|---|---|---|---|
| 1 | `utils.engine_version()` unstripped newline corrupts `manifest.json` (`utils.py:48-49`, `package.yaml:167`); breaks `post_install.sh:794` | PR1 | `.strip()` the read | Code |
| 2 | `.flutter_settings` rewrite can emit a double comma (`post_install.sh:1323-1327`) | PR1 | Rewrite via `python3` JSON load/dump | Code |
| 3 | Snapshot success banner printed after checksum/unzip failure (`install_flutter_complete.sh:668-677`) | PR1 | Print success only when the snapshot file exists | Code |
| 47 | Same defect, unfixed, in the script that actually runs during `dpkg -i` (`scripts/install/post_install.sh:976-997` — `unzip … \|\| true` then an unconditional success banner, no checksum at all). Found during implementation | PR1 | Gate the banner on extraction success | Code |
| 4 | `read -p` prompt ignores `--yes` / non-interactive stdin (`install_flutter_complete.sh:416`) | PR1 | Skip when `OPT_YES`/`NON_INTERACTIVE`/non-TTY | Code |
| 5 | `flutter_project_config.sh` referenced by README + `termux_smoke.sh:220` but never packaged | PR1 | Add `package.yaml` resource → `$prefix/share/flutter/flutter_project_config.sh` (0755) | Code |
| 6 | `flutter_project_config.sh` shebang `#!/usr/bin/env bash` unsuitable on Termux | PR1 | `#!/data/data/com.termux/files/usr/bin/bash` | Code |
| 7 | Deb output dir vs docs | PR1 | `path = 'release'` | Code |
| 8 | `pkg_rel`/version references | PR1 | Bump to 5 + `--fix` + CHANGELOG | Code+Docs |
| 9 | `package.yaml` JDK claim in `BUILD_GUIDE.md:384-386` is false | PR2 | Fix the doc (installer handles Java) | Docs |
| 10 | `android_release_arm64/` dirs don't exist (`BUILD_GUIDE.md:213,564`, `RELEASE_NOTES.md:76`, `CHANGELOG.md:114`) | PR2 | Replace with the three `linux_*_arm64/` dirs | Docs |
| 11 | `CHANGELOG.md:28` claims `*_ZH.md` twins that don't exist | PR2 | Remove the claim | Docs |
| 12 | `BUILD_PROCESS.md:277` says `Version: $tag` | PR2 | `$package_version` | Docs |
| 13 | `BUILD_PROCESS.md:194` stale debug-mode guidance | PR2 | Reflect release-only | Docs |
| 14 | Smoke marker list incomplete (`CI_CD.md:215-228`) | PR2 | List all 16 markers | Docs |
| 15 | device-smoke inputs differ from docs (`CI_CD.md:182-187`) | PR2 | Document `artifact_run_id`/`artifact_name`/`expected_sha256` | Docs |
| 16 | Publish contents wrong (`CI_CD.md:127-128`) | PR2 | List the 6 published files | Docs |
| 17 | Release-check triggers incomplete (`CI_CD.md:45`) | PR2 | Add `pull_request` | Docs |
| 18 | Review policy vs ruleset (approvals 0) | PR2/PR4 | Document resolved-threads-only; fix `AGENTS.md:88` | Docs |
| 19 | Generated `split-select` stub uses `#!/bin/sh` (`post_install.sh:1175`), violating AGENTS.md's generated-wrapper shebang rule | PR4 | Use the Termux prefix shebang | Code |
| 20 | `Package.__format__` hijacks the format protocol (`package.py:313`) | PR3 | Rename to `render`; update 6 call sites | Code |
| 21 | Data clump of 7 metadata kwargs (`build.py:250-257`) | PR3 | Add `Build.metadata()` | Code |
| 22 | Duplicated dispatch in `gen_resource`/`test_resource` (`package.py:325-335,379-389`) | PR3 | Extract shared selector + `_resolve_defines` | Code |
| 23 | `assert` as runtime validation (`package.py:288,295`, `sysroot.py:203,227`) | PR3 | Explicit `raise` | Code |
| 24 | `package` name overloaded (`build.py:68,105-106`) | PR3 | Rename the conf-path binding | Code |
| 25 | Stale TODOs (`build.py:78,91,268`, `utils.py:44`) | PR3 | Delete | Code |
| 26 | `require_tools` only guards the no-arg pipeline (`build.py:270`) | PR3 | Move into the steps that need it | Code |
| 27 | Misleading walker names (`package.py:115,127,194,202`) | PR3 | Rename | Code |
| 28 | `add_dir` monkeypatches `tar.__cache__` (`package.py:179-180`) | PR3 | Thread a cache set | Code |
| 29 | Strict release verification unreachable (`verify_release_asset.py:411`) | PR4 | `device_smoke_evidence.json` → optional-when-present | Code |
| 30 | `generate_versions.py` never run in CI; 4 divergent version rewriters | PR4 | Add `--check` to `ci.yml`; narrow `sync_release_pins.py:116-129` | Code |
| 31 | Issue templates not drift-scanned (`bug_report.yml:38`) | PR4 | Add to `check_version_drift.py` | Code |
| 32 | Smoke APT marker trivially satisfied (`termux_smoke.sh:164-165`) | PR4 | Drop the masking `|| true` | Code |
| 33 | Dead guard in `post_install.sh:271` | PR4 | Fix/remove | Code |
| 34 | Over-broad idempotency tokens (`post_install.sh:372,386`) | PR4 | Tighten | Code |
| 35 | Duplicated NDK helpers (`install_flutter_complete.sh:43-149` vs `post_install.sh:197-239`) | PR4 | Keep + cross-reference + `check_repo` drift assertion | Code |
| 36 | Dead variables (`install_flutter_complete.sh:382,1022-1027`, `doctor:8`) | PR4 | Remove | Code |
| 37 | `test_gen_snapshot.sh:2-9` hardening gaps | PR4 | `set -euo pipefail`, quote, check `cp` | Code |
| 38 | Inconsistent shebangs across `scripts/install/*.sh` | PR4 | Standardize by role | Code |
| 39 | Metadata writers diverge (`build-deb.yml:157-186`) | PR4 | Write ints like `build.yml` | Code |
| 40 | `AGENTS.md` in `.gitignore:160` | PR4 | Remove the line | Code |
| 41 | Unpinned actions / `curl \| bash` actionlint (`ci.yml:74`, `build.yml:343,128`) | PR4 | Pin SHAs/versions | Code |
| 42 | Workflow-level `contents: write` (`build.yml:19-20`); unused `actions: write` (`autorelease.yml:10`) | PR4 | Scope/remove | Code |
| 43 | Fragile sed tag parse (`build-deb.yml:103`) | PR4 | Use `python3 -c` tomllib | Code |
| 44 | Pin-sync push not best-effort (`build.yml:390-391`) | PR4 | `\|\| true` | Code |
| 45 | `validate.yml:27-34` missing `setup-python` | PR4 | Add it | Code |
| 46 | `release-check.yml` PR mode is a no-op gate | PR2/PR4 | Document (`LIGHTWEIGHT_CHECK=1` = manifest syntax) | Docs |

*Note:* the review produced 46 findings; one doc↔code finding (device-smoke inputs) surfaced on both the Standards and Spec axes and is recorded once here (row 15). The implementation plan enumerates the concrete tasks: `docs/superpowers/plans/2026-10-15-fix-all-review-findings.md`.

## Testing Strategy

- **Python:** every behavioral fix gets a `pytest` case in `test_build.py` (engine-version strip, `flutter_project_config` resource, output under `release/`, `Package.render`, `Build.metadata`, strict-verifier companion logic). The existing 34 tests must stay green.
- **Shell:** targeted assertions in `termux_smoke.sh` / `gh_e2e_test.sh` where checkable; device-only verification (Windows + ADB `run_termux_smoke.ps1`) is listed in the PR description.
- **Docs:** validated by `check_repo.py` and `git diff --check`.

## Verification & Rollout

Each PR lands green on the full gate set before the next begins. Order: PR1 → PR2 → PR3 → PR4. `pkg_rel` is bumped once, in PR1; the release pipeline fills the artifact hashes on the next build.

## Risks

- `Package.__format__` → `render` is behavior-preserving only if every call site is updated; all six are inside `package.py`.
- The strict-verification change alters behavior only when all companions exist; the common single-asset path is unchanged.
- Shebang changes are latent until a script is executed directly; most callers invoke `bash <script>`.
- **Release gate:** PR1's changes alter the shipped `manifest.json` and the deb's file list, so it must be validated by a real build before the next release ships.
