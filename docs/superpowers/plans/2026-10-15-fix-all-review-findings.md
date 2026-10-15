# Fix All Review Findings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 46 review findings across the Python core, shell/device, CI/config, and docs surfaces, keeping every repo gate green, split into four reviewable PRs.

**Architecture:** Four independently reversible PRs in dependency order. PR1 changes what ships (runtime fixes, packaging, `pkg_rel` bump); PR2 is docs-only and cannot break the build; PR3 is behavior-preserving Python refactoring; PR4 hardens shell and CI. Each PR lands green on the full gate set before the next starts.

**Tech Stack:** Python 3.12 (Fire CLI, pytest, ruff), bash (Termux device + CI), GitHub Actions, dpkg packaging.

**Spec:** `docs/superpowers/specs/2026-10-14-fix-all-review-findings-design.md`

## Global Constraints

- Green gates per PR (exact commands): `ruff check build.py package.py sysroot.py utils.py test_build.py scripts/ci/`; `python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/*.py`; `pytest test_build.py`; `bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh`; `shellcheck --severity=error install_flutter_complete.sh <scripts/**/*.sh with a bash/sh shebang>`; `python scripts/ci/check_repo.py`; `python scripts/ci/check_version_drift.py`; `python scripts/ci/generate_versions.py --check`; `git diff --check`.
- Version drift is enforced repo-wide. PR1 bumps `build.toml [package] pkg_rel` from `4` to `5`; every `3.47.7-4` / `flutter_3.47.7-4_aarch64.deb` / `PKG_REL=4` reference must match afterwards. Do **not** pin a new deb SHA256 (unknown until the next build).
- **Exception — asset-pinned values.** Anything that pins a *published* deb deliberately keeps the previous revision until the post-publish `sync_release_pins.py` commit (`6dc3911`) refreshes it, and `check_version_drift.py` deliberately does not scan them. This covers the `Size`/`SHA256` doc rows, `EXPECTED_SHA256`, and `scripts/device/run_termux_smoke.ps1`'s `$DebUrl` / `$ExpectedSha256` defaults (`sync_release_pins.py:37,80` own those; `device-smoke.yml` passes `-DebPath`, so the `$DebUrl` default is unused in CI). Do **not** "fix" them — a bump here would point at an asset that does not exist yet. Verified empirically: with `pkg_rel = '5'` these still say `-4` and `check_version_drift.py` still passes.
- PR1 sets `build.toml [package] path = 'release'`.
- openjdk is resolved **docs-only** — no `Depends`/`Recommends` change in `package.yaml`.
- NDK backup/restore duplication is kept; it gains cross-reference comments plus a CI drift check.
- Conventional Commits; one branch per PR.

## Review Focus

1. `manifest.json` `engine_revision` must contain no trailing newline (it breaks `post_install.sh:794`'s grep and yields invalid JSON).
2. `~/.flutter_settings` must stay valid JSON after the rewrite (no doubled comma when the old key was last).
3. `install_flutter_complete.sh` must not print a success banner after a checksum or unzip failure.
4. `package.yaml` must declare the `flutter_project_config` resource, and build output must land under `release/`.
5. Strict release verification must treat `device_smoke_evidence.json` as optional-when-present so the 6-asset build path reaches the provenance/inventory checks.

---

## PR1 — `fix(shipping)`: runtime defects, packaging, version bump

### Task 1: Strip the engine version newline

**Files:**
- Modify: `utils.py:45-49`
- Test: `test_build.py` (new test method in the class that covers `utils`)

**Interfaces:**
- Consumes: nothing.
- Produces: `utils.engine_version(root: str) -> str` now returns the revision with surrounding whitespace removed (trailing `\n` in particular).

- [ ] **Step 1: Write the failing test**

```python
def test_engine_version_strips_trailing_newline(self):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / 'bin' / 'internal').mkdir(parents=True)
        (root / 'bin' / 'internal' / 'engine.version').write_text('b8800d0f\n')
        self.assertEqual(utils.engine_version(str(root)), 'b8800d0f')
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test_build.py -k engine_version_strips -v`
Expected: FAIL — actual value is `'b8800d0f\n'`.

- [ ] **Step 3: Implement in `utils.py`**

Change `return f.read()` to `return f.read().strip()`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest test_build.py -k engine_version_strips -v`
Expected: PASS.

- [ ] **Step 5: Confirm the downstream consumer still matches**

Run: `pytest test_build.py -q`
Expected: all pass (including the manifest-render test).

- [ ] **Step 6: Commit**

```bash
git add utils.py test_build.py
git commit -m "fix(package): strip trailing newline from engine version"
```

### Task 2: Stop corrupting `~/.flutter_settings`

**Files:**
- Modify: `scripts/install/post_install.sh:1319-1330`

**Interfaces:**
- Consumes: `$ANDROID_SDK`, `$HOME`.
- Produces: a `~/.flutter_settings` that is always valid JSON with the `android-sdk` key set.

- [ ] **Step 1: Write the failing check**

Add an assertion to `scripts/device/termux_smoke.sh` that parses the file after `post_install.sh` runs:

```bash
if [ -f "$HOME/.flutter_settings" ]; then
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$HOME/.flutter_settings" \
    && record_status FLUTTER_SETTINGS_STATUS 0 \
    || record_status FLUTTER_SETTINGS_STATUS 1
fi
```

- [ ] **Step 2: Run the smoke assertion against the current file (or a fixture copy) to see it fail**

Run: `bash -c 'printf "%s\n" "{" "  \"analytics\": false," "  \"android-sdk\": \"/x\"" "}" > /tmp/fs.json; grep -v "\"android-sdk\"" /tmp/fs.json | grep -v "^[[:space:]]*}$" > /tmp/fs.tmp; echo "  ,\"android-sdk\": \"/x\"" >> /tmp/fs.tmp; echo "}" >> /tmp/fs.tmp; python3 -c "import json;json.load(open(\"/tmp/fs.tmp\"))"'`
Expected: `json.decoder.JSONDecodeError` (double comma).

- [ ] **Step 3: Implement**

Replace the grep/append block with a `python3` rewrite that loads the JSON, sets `android-sdk`, and writes it back (falling back to `cat` when `python3` is unavailable). Keep the existing `else` branch behaviour.

- [ ] **Step 4: Verify**

Run the Step 2 fixture again with the new logic; expect `python3` to exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/install/post_install.sh scripts/device/termux_smoke.sh
git commit -m "fix(installer): write valid JSON to flutter_settings"
```

### Task 3: Report snapshot installs honestly

**Files:**
- Modify: `install_flutter_complete.sh:668-677`

**Interfaces:**
- Consumes: `$dart_zip`, `$SNAPSHOTS_DIR`, `verify_sha256`.
- Produces: the success banner prints only when the snapshot file exists after unzip.

- [ ] **Step 1: Write the failing check**

Add to `scripts/device/termux_smoke.sh` a guard that at least one snapshot exists when the installer claimed success (this is the assertion the fix must satisfy):

```bash
[ -f "$PREFIX/share/flutter/bin/cache/dart-sdk/bin/snapshots/dds_aot.dart.snapshot" ] \
  && record_status DART_SNAPSHOT_STATUS 0 || record_status DART_SNAPSHOT_STATUS 1
```

- [ ] **Step 2: Implement**

Only emit `echo "  ✓ Dart SDK snapshots installed"` when `unzip` succeeded and `$SNAPSHOTS_DIR/dds_aot.dart.snapshot` exists; otherwise print a warning that bundled snapshots are used.

- [ ] **Step 3: Verify**

Run: `bash -n install_flutter_complete.sh`
Expected: exit 0 (no syntax error).

- [ ] **Step 4: Commit**

```bash
git add install_flutter_complete.sh scripts/device/termux_smoke.sh
git commit -m "fix(installer): stop reporting snapshot success after failure"
```

### Task 4: Report snapshot installs honestly in the deb-shipped post-install

> Added during execution: Task 3's review found the same honesty defect, unfixed, in the
> script that actually runs during `dpkg -i`.

**Files:**
- Modify: `scripts/install/post_install.sh:976-997`

**Interfaces:**
- Consumes: `$SNAPSHOTS_DIR` (`$DART_SDK/bin/snapshots`), the downloaded `dart-sdk.zip`.
- Produces: the `✓ Dart SDK snapshots installed` banner only when the extracted key snapshot
  exists; a warning otherwise. The `else` ("already exist") branch already reports truthfully and
  must keep its message.

- [ ] **Step 1: Write the failing check**

`scripts/device/termux_smoke.sh` already records `DART_SNAPSHOT_STATUS` by probing
`$PREFIX/opt/flutter/bin/cache/dart-sdk/bin/snapshots/dds_aot.dart.snapshot` (Task 3). Reuse that
as the end-state assertion; for the failure path, extract the modified block into a harness and
feed it a truncated `dart-sdk.zip` to prove the banner is suppressed. Record the observation.

- [ ] **Step 2: Implement**

Gate the banner on the extraction result: print `✓ Dart SDK snapshots installed` only when
`unzip` succeeded **and** `$SNAPSHOTS_DIR/dds_aot.dart.snapshot` (or at least one entry under
`$SNAPSHOTS_DIR/*.snapshot`) exists; otherwise print a `⚠` warning stating the bundled snapshots
are used. Keep `rm -f dart-sdk.zip` on every path. Do not add a checksum check here — this script
has no `verify_sha256` helper and fetching one is out of scope; checking existence is the honest
minimum and matches what Task 3 does in the sibling script.

- [ ] **Step 3: Verify**

Run: `bash -n scripts/install/post_install.sh`; `shellcheck --severity=error scripts/install/post_install.sh`
Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/install/post_install.sh
git commit -m "fix(installer): gate deb post-install snapshot banner on extraction success"
```

### Task 5: Honour non-interactive mode in the prompt

**Files:**
- Modify: `install_flutter_complete.sh:416-421`
- Test: `scripts/test/gh_e2e_test.sh` (non-interactive invocation already present; extend if needed)

**Interfaces:**
- Consumes: `OPT_YES`, `OPT_NON_INTERACTIVE`, `NON_INTERACTIVE` (exported by `lib_common.sh`).
- Produces: with `--yes`/`--non-interactive` or a non-TTY stdin, the prompt is skipped.

- [ ] **Step 1: Write the failing check**

In `gh_e2e_test.sh`, the installer is invoked with `--yes`; add an assertion that the run does not block (a timeout wrapper):

```bash
timeout 600 bash install_flutter_complete.sh --yes --skip-smoke || fail "installer blocked or failed in non-interactive mode"
```

- [ ] **Step 2: Run to verify it fails or hangs**

Run: `timeout 20 bash install_flutter_complete.sh --yes --skip-smoke` (abort early; confirm it reaches the prompt).
Expected: blocks on `read` before the fix.

- [ ] **Step 3: Implement**

Guard the prompt: `if [ "${OPT_YES:-false}" != "true" ] && [ "${NON_INTERACTIVE:-false}" != "true" ] && [ -t 0 ]; then read -p ... ; fi`; default `REPLY=Y` when skipped.

- [ ] **Step 4: Verify**

Run: `bash -n install_flutter_complete.sh`
Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add install_flutter_complete.sh scripts/test/gh_e2e_test.sh
git commit -m "fix(installer): respect --yes and non-interactive stdin"
```

### Task 6: Ship `flutter_project_config.sh` in the deb

**Files:**
- Modify: `package.yaml` (add a resource block after `post_install`)
- Modify: `scripts/install/flutter_project_config.sh:1` (shebang)
- Test: `test_build.py` (packaging resource test)

**Interfaces:**
- Consumes: `$prefix` (defined at `package.yaml:6`).
- Produces: deb file at `$prefix/share/flutter/flutter_project_config.sh`, mode `0o755`.

- [ ] **Step 1: Write the failing test**

```python
def test_package_ships_flutter_project_config(self):
    cfg = yaml.safe_load(open('package.yaml'))
    res = cfg['resource']
    self.assertIn('flutter_project_config', res)
    self.assertEqual(
        res['flutter_project_config']['source'],
        'scripts/install/flutter_project_config.sh',
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test_build.py -k flutter_project_config -v`
Expected: FAIL — key missing.

- [ ] **Step 3: Implement**

Add to `package.yaml`:

```yaml
  flutter_project_config:
    source: scripts/install/flutter_project_config.sh
    output:
      - $prefix/share/flutter/flutter_project_config.sh
    mode: 0o755
```

Change the script's first line to `#!/data/data/com.termux/files/usr/bin/bash`.

- [ ] **Step 4: Verify**

Run: `pytest test_build.py -q` and `python scripts/ci/check_repo.py`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add package.yaml scripts/install/flutter_project_config.sh test_build.py
git commit -m "fix(package): ship flutter_project_config script in the deb"
```

### Task 7: Route deb output to `release/`

**Files:**
- Modify: `build.toml:94-96`
- Test: `test_build.py::test_output_uses_package_version_suffix`

**Interfaces:**
- Consumes: `build.toml [package] path`.
- Produces: `Build().output('arm64')` under `release/`.

> Amended during execution (gap G2): setting `path = 'release'` alone is **not** enough.
> `build.py`'s `output()` returns the deb filename only when `self.release` already exists as a
> directory (`if self.release.is_dir(): … else: return self.release`), and nothing creates it.
> `path = '.'` worked solely because the repo root always exists. With `release/` absent,
> `output()` returns the directory, so the test fails and `debuild` would target a file literally
> named `release`.

- [ ] **Step 1: Adjust the test**

Assert the parent directory of `output('arm64')` is `release`:

```python
self.assertEqual(Path(self.instance.output('arm64')).parent.name, 'release')
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test_build.py -k output_uses_package_version -v`
Expected: FAIL (parent is the repo root).

- [ ] **Step 3: Implement**

Set `path = 'release'` in `build.toml [package]` (`release/` is already gitignored), **and** make
`Build.output()` unconditionally return `self.release/<name>` instead of falling back to returning
`self.release` when the directory does not exist yet, creating the directory if needed
(`mkdir(parents=True, exist_ok=True)`). Keep the deb filename format unchanged. If you conclude a
different location is the right place to create the directory, say so in your report rather than
silently choosing.

- [ ] **Step 4: Verify**

Run: `pytest test_build.py -q`; `python scripts/ci/check_repo.py`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add build.toml test_build.py
git commit -m "fix(package): emit the deb under release/"
```

### Task 8: Bump `pkg_rel` to 5 and sync every version reference

**Files:**
- Modify: `build.toml` (`pkg_rel` + its trailing comment)
- Modify: `docs/releases/RELEASE_NOTES.md` (new `## Fixed in 3.47.7-5` section, mirroring the `-4` section)
- Modify: `docs/releases/CHANGELOG.md` (entries under `## [Unreleased]`)
- Modify: exactly the 12 files `check_version_drift.py --fix` rewrites (AGENTS.md, README.md, the 3 `docs/guides/*` files, RELEASE_NOTES.md, the 4 installer scripts, versions_common.sh, gh_e2e_test.sh). It does **not** touch `docs/CI_CD.md` or `.github/ISSUE_TEMPLATE/*`.

**Interfaces:**
- Consumes: the `pkg_rel` value.
- Produces: every `PKG_REL=<n>` / `flutter_3.47.7-<n>_aarch64.deb` / template string equals `5`.

- [ ] **Step 1: Bump and auto-fix**

Edit `build.toml`: `pkg_rel = '5'` and update its trailing comment. Then run `python scripts/ci/check_version_drift.py --fix`.

- [ ] **Step 2: Write the release docs**

Mirror precedent commit `282c729` (the `pkg_rel` 3→4 bump):

- `docs/releases/RELEASE_NOTES.md` — add a `## Fixed in 3.47.7-5` section immediately before `## Package`, describing this batch's shipping fixes (engine-version newline, valid `~/.flutter_settings` JSON, honest snapshot banners in both installers, non-interactive `--yes`, `flutter_project_config.sh` now shipped, deb emitted under `release/`), and stating it is a drop-in replacement over the previous revision.
- `docs/releases/CHANGELOG.md` — add the same items under the existing `## [Unreleased]` heading in its Keep-a-Changelog subsections (`### Fixed`, `### Added`, `### Changed` as appropriate). Do **not** invent a released `## [3.47.7-5]` heading; the release process cuts released sections from this changelog.
- Do **not** edit the `Size` / `SHA256` values. `build.toml` has no such keys, and the post-publish `sync_release_pins.py` commit fills them (precedent: `282c729` left them stale, `6dc3911` synced them).
- Do **not** edit `docs/CI_CD.md` or `.github/ISSUE_TEMPLATE/*` — the checker does not scan them; PR2 (Task 10) and PR4 (Task 19) own them.

- [ ] **Step 3: Verify drift is clean**

Run: `python scripts/ci/check_version_drift.py`; `python scripts/ci/generate_versions.py --check`; `python scripts/ci/check_repo.py`
Expected: all pass.

- [ ] **Step 4: Commit**

Confirm `git status --porcelain` lists only files you expect (no stray artifacts), then:

```bash
git add -A
git commit -m "chore(release): bump pkg_rel to 5 for the review-fix batch"
```

Give it a body like `282c729`'s: why the bump is needed (the fixes live in files that ship inside the deb), that references were synced with `check_version_drift.py --fix`, and that `CI_CD.md` / `EXPECTED_SHA256` are synced post-publish by `sync_release_pins.py`.

---

## PR2 — `docs(contract)`: align documentation with the code

### Task 9: Correct the build-output and dependency claims

**Files:**
- Modify: `docs/guides/BUILD_GUIDE.md:195,205,213,384-386,564`
- Modify: `docs/guides/BUILD_PROCESS.md:113,194,277,283,358`
- Modify: `docs/releases/RELEASE_NOTES.md:76`
- Modify: `docs/releases/CHANGELOG.md:28,114`

**Interfaces:** none (docs only).

- [ ] **Step 1: Fix the android/ output claims**

Replace every `android_release_arm64/` / `android_profile_arm64/` reference with the three real `linux_{release,debug,profile}_arm64/` dirs, and point the "verify gen_snapshot" step at `linux_release_arm64/clang_arm64/gen_snapshot`.

- [ ] **Step 2: Fix the openjdk claim**

Rewrite `BUILD_GUIDE.md:384-386` to state the one-command installer installs `openjdk-21` (overridable with `JAVA_PACKAGE`) and that a manual `apt install ./<deb>` needs Java present, since `package.yaml` does not declare it.

- [ ] **Step 3: Fix the remaining claims**

`BUILD_PROCESS.md:277` → `Version: $package_version`; `:194` → release-only; `:113,283,358` → `release/flutter_3.47.7-5_aarch64.deb`; `RELEASE_NOTES.md:76` → drop the android dirs; `CHANGELOG.md:28` → drop the `*_ZH.md` twin claim; `:114` → align the dir list.

- [ ] **Step 4: Verify**

Run: `python scripts/ci/check_repo.py`; `git diff --check`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/guides docs/releases
git commit -m "docs(guides): align build output and dependency claims with the code"
```

### Task 10: Correct the CI/CD documentation

**Files:**
- Modify: `docs/CI_CD.md:45,127-128,182-187,215-228,258`
- Modify: `AGENTS.md:88`

**Interfaces:** none (docs only).

- [ ] **Step 1: Fix the marker list**

Replace the 13-marker list with the 16 `run_termux_smoke.ps1:384-400` enforces (add `APT_REPAIR_STATUS`, `CONFIG_VERIFY_STATUS`, `BUILD_AAB_STATUS`, `AAB_COPY_STATUS`).

- [ ] **Step 2: Fix the device-smoke inputs and publish list**

Document `artifact_run_id` / `artifact_name` / `expected_sha256` (no `deb_url`), and list the 6 published assets. Add the `pull_request` trigger for the release check.

- [ ] **Step 3: Document the review policy and PR-check scope**

State that the ruleset requires resolved threads but **0** approvals, and that `release-check.yml` runs in `LIGHTWEIGHT_CHECK=1` mode on PRs (manifest syntax/structure only) while strict verification runs on release events. Adjust `AGENTS.md:88` ("PR + review + resolved threads") to match.

- [ ] **Step 4: Verify**

Run: `python scripts/ci/check_repo.py`; `git diff --check`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/CI_CD.md AGENTS.md
git commit -m "docs(ci): document review policy, device-smoke inputs, and markers"
```

---

## PR3 — `refactor(python)`: smells, behavior-preserving

### Task 11: Give the template renderer an honest name

**Files:**
- Modify: `package.py:313` and its internal callers (`322,363,368,371,399,400`)
- Test: `test_build.py`

**Interfaces:**
- Consumes: `Package`.
- Produces: `Package.render(template: str, **extra) -> str` replacing `__format__`.

- [ ] **Step 1: Write the failing test**

```python
def test_package_exposes_render(self):
    self.assertTrue(callable(getattr(Package, 'render', None)))
```

- [ ] **Step 2: Run to verify it fails** — `pytest test_build.py -k package_exposes_render -v` → FAIL.

- [ ] **Step 3: Implement** — rename `__format__` to `render`; update the six internal call sites; leave no `__format__`.

- [ ] **Step 4: Verify** — `pytest test_build.py -q` → pass.

- [ ] **Step 5: Commit** — `refactor(package): rename Package.__format__ to render`

### Task 12: Bundle the release metadata clump

**Files:**
- Modify: `build.py:250-257`
- Modify: `test_build.py:515-526,569-580,616-627`

**Interfaces:**
- Consumes: `[flutter]`/`[android]` config values.
- Produces: `Package(**Build.metadata())` — a single dict/typed payload named `metadata` carrying `dart_version`, `framework_revision`, `framework_commit_date`, `devtools_version`, `ndk_version`, `compile_sdk`, `target_sdk`, `package_version`.

- [ ] **Step 1: Write the failing test** — assert `Build().metadata()['compile_sdk'] == <value from build.toml>`.
- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** — add `Build.metadata()`; call `Package(**conf, **self.metadata())`; collapse the three test copies into a helper.
- [ ] **Step 4: Verify** — `pytest test_build.py -q` → pass.
- [ ] **Step 5: Commit** — `refactor(build): bundle release metadata into one payload`

### Task 13: De-duplicate the resource dispatch

**Files:**
- Modify: `package.py:325-335,349-350,379-389,397-398`

**Interfaces:**
- Produces: one shared selector used by both `gen_resource` and `test_resource`; one `_resolve_defines`.

- [ ] **Step 1: Write the failing test** — a construction test asserting both paths produce identical output for an equal input.
- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** — extract the shared `str|list|falsy|else` selector and the `define:` evaluation loop.
- [ ] **Step 4: Verify** — `pytest test_build.py -q`; `ruff check`.
- [ ] **Step 5: Commit** — `refactor(package): share resource dispatch between gen and test`

### Task 14: Replace asserts, fix TODOs, names, and cache state

**Files:**
- Modify: `package.py:115,127,179-180,194,202,288,295`
- Modify: `sysroot.py:203,227`
- Modify: `build.py:68,78,91,105-106,268,270`
- Modify: `utils.py:44`

- [ ] **Step 1: Write the failing tests** — assert `ValueError`/explicit exception type on the preconditions currently asserted (`package.py:288`, `sysroot.py:203`).
- [ ] **Step 2: Run to verify they fail** — FAIL (AssertionError instead).
- [ ] **Step 3: Implement** — asserts → `raise ValueError(...)`; rename `Build.package` conf-path binding and the walker's `isdir`/`explore`/`tar`; thread the cache set instead of `tar.__cache__`; delete the three stale TODOs; move the `require_tools` check into the steps that need it.
- [ ] **Step 4: Verify** — `pytest test_build.py -q`; `ruff check`.
- [ ] **Step 5: Commit** — `refactor: replace runtime asserts, fix names, and drop stale TODOs`

---

## PR4 — `chore(ci)`: shell and CI hardening

### Task 15: Make strict release verification reachable

**Files:**
- Modify: `scripts/ci/verify_release_asset.py:401-453`
- Test: `test_build.py` or a new `scripts/ci` test invoked by `ci.yml`

- [ ] **Step 1: Write the failing test** — with a fake asset map containing the 5 build companions but **no** `device_smoke_evidence.json`, assert the verifier reaches the strict branch (validate the 5), and with the smoke file present assert it is also validated.
- [ ] **Step 2: Run to verify it fails** — FAIL (falls into single-asset branch).
- [ ] **Step 3: Implement** — split `aux_assets` into required (5) and optional (`device_smoke_evidence.json`); require the former for `companion_available`, validate the latter only if present.
- [ ] **Step 4: Verify** — new test passes; `ruff check`.
- [ ] **Step 5: Commit** — `fix(ci): treat device smoke evidence as optional in release verification`

### Task 16: Wire the missing version check into CI

**Files:**
- Modify: `.github/workflows/ci.yml:98-102`
- Modify: `scripts/ci/check_version_drift.py` (add `.github/ISSUE_TEMPLATE/*.yml` scanning)
- Modify: `scripts/ci/sync_release_pins.py:116-129`

**Interfaces:**
- Produces: CI runs `generate_versions.py --check`; issue templates are drift-scanned; pin-sync no longer rewrites arbitrary 64-hex tokens.

- [ ] **Step 1: Write the failing test** — a `check_version_drift` unit case with a stale `placeholder: "3.47.7-3"` in a fixture template asserting failure.
- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** — add the template scan, add the CI step, narrow the pin-sync regex to the documented pin fields.
- [ ] **Step 4: Verify** — `python scripts/ci/check_version_drift.py`; `python scripts/ci/generate_versions.py --check`.
- [ ] **Step 5: Commit** — `chore(ci): gate version generation and narrow pin sync`

### Task 17: Fix the shell defects

**Files:**
- Modify: `scripts/device/termux_smoke.sh:164-165`
- Modify: `scripts/install/post_install.sh:271,372,386`
- Modify: `install_flutter_complete.sh:382,1022-1027`
- Modify: `scripts/install/flutter_termux_doctor.sh:8`
- Modify: `scripts/test/test_gen_snapshot.sh:2-9`
- Modify: shebangs across `scripts/install/*.sh`

- [ ] **Step 1: Write the failing check** — make the APT-repair status reflect the real exit code (drop the trailing `|| true`), and assert in `termux_smoke.sh` self-test that a forced `apt` failure records non-zero.
- [ ] **Step 2: Implement** — fix the APT marker, the dead `PLATFORM_ARM32` guard, the over-broad `platform.isAndroid` idempotency tokens, remove dead variables, harden `test_gen_snapshot.sh` (`set -euo pipefail`, quoted paths, checked `cp`), and standardize device/installer shebangs to `#!/data/data/com.termux/files/usr/bin/bash` (CI/host scripts keep `#!/bin/bash`).
- [ ] **Step 3: Verify** — `bash -n`; `shellcheck --severity=error`.
- [ ] **Step 4: Commit** — `fix(scripts): correct smoke status, guards, and shebangs`

### Task 18: Guard the duplicated NDK helpers

**Files:**
- Modify: `install_flutter_complete.sh:43-149` (comment)
- Modify: `scripts/install/post_install.sh:197-239` (comment)
- Modify: `scripts/ci/check_repo.py` (new assertion)

- [ ] **Step 1: Write the failing test** — a `check_repo` case that mutates one copy and asserts failure.
- [ ] **Step 2: Run to verify it fails** — FAIL (no check yet).
- [ ] **Step 3: Implement** — add cross-reference comments in both files and a `check_repo` assertion that the `backup_ndk_file`/`restore_ndk_backups` bodies match.
- [ ] **Step 4: Verify** — `python scripts/ci/check_repo.py`.
- [ ] **Step 5: Commit** — `chore(ci): detect drift in duplicated NDK helpers`

### Task 19: Remaining CI hygiene and hardening

**Files:**
- Modify: `.gitignore:160`
- Modify: `.github/workflows/build.yml:19-20,343,390-391`
- Modify: `.github/workflows/autorelease.yml:10`
- Modify: `.github/workflows/validate.yml:27-34`
- Modify: `.github/workflows/build-deb.yml:103,157-186`
- Modify: `.github/workflows/ci.yml:74`, `scripts/ci/check_repo.py:249`

- [ ] **Step 1: Implement** — remove `AGENTS.md` from `.gitignore`; scope `contents: write` to the publish job and drop the unused `actions: write`; pin `softprops/action-gh-release`, `mozilla-actions/sccache-action`, and the `actionlint` download to fixed SHAs/versions (and update `check_repo.py` if it mandates the exact `curl` string); make the pin-sync push `|| true`; add `actions/setup-python` to `validate.yml`; replace `build-deb.yml`'s sed tag parse with `python3 -c` tomllib; write `run_id`/`build_number` as ints in `build-deb.yml`.
- [ ] **Step 2: Verify** — `python scripts/ci/check_repo.py`; a workflow-lint pass where available; `git diff --check`.
- [ ] **Step 3: Commit** — `chore(ci): pin actions, scope permissions, and harden workflows`

---

## Self-review checklist (run before PR handoff)

- [ ] Every spec finding maps to a task (46 findings + 1 found during execution → Tasks 1-19).
- [ ] No task leaves an open choice that decides behavior.
- [ ] `Package.render`, `Build.metadata` names are consistent across tasks.
- [ ] Review Focus items 1-5 each have a test in the owning task.
- [ ] Plan is not a code transcript — bodies only where the algorithm is not determined.
