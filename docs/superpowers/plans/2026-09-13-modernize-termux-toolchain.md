# Modernize Termux Toolchain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the entire flutter-for-termux stack up to the modern (Sept 2026) Termux ecosystem — aapt2 16.0.0.4 with compileSdk/targetSdk 36, current host deps and CI actions, retired stale packages — with `build.toml` as the single source of truth.

**Architecture:** `build.toml` flows into `package.yaml` → `manifest.json` → on-device scripts, replacing scattered hardcoded constants. On-device toolchain versions are pinned in one `scripts/install/versions_common.sh`. The aapt2/compileSdk experiment gates whether the default is 36/35/34.

**Tech Stack:** Python 3.12 (build host, Fire/aiohttp/loguru/GitPython), Bash (Termux), YAML config, GitHub Actions, NDK r29 / Dart 3.13.3 / Flutter 3.47.4.

**Spec:** `docs/superpowers/specs/2026-09-13-modernize-termux-toolchain-design.md`

## Global Constraints
- All version literals trace to `build.toml`; scripts reference the manifest or `versions_common.sh` — no new divergent hardcodes.
- Flutter **3.47.4**, Dart **3.13.3**, NDK **29.0.14206865** (r30 `30.0.16248370` road-mapped, blocked on lzhiyong/termux-ndk).
- compileSdk/targetSdk default **36**, fallback ladder 36→35→34 per Stage-B experiment outcome.
- Termux aapt2 baseline **16.0.0.4-2**, `7zip 26.03`, single JDK **openjdk-21**.
- Keep ARM64-only; keep R8/resource-shrinking disabled (out of scope).
- Version-drift checks (`check_repo.py`, `check_version_drift.py`) keep passing after every task.

---

### Task 0: Write spec + plan docs

**Files:**
- Create: `docs/superpowers/specs/2026-09-13-modernize-termux-toolchain-design.md`
- Create: `docs/superpowers/plans/2026-09-13-modernize-termux-toolchain.md`

- [ ] **Step 1: Write the spec doc** (the approved design message) and commit
- [ ] **Step 2: Write this plan doc** and commit

Verify: `git diff --check && git status`
Commit: `docs: add termux modernization spec and plan`

### Task 1: `build.toml` — single source of truth

**Files:**
- Modify: `build.toml`

- [ ] **Step 1: Add `version` under `[ndk]`**

```toml
[ndk]
api = 26
version = '29.0.14206865' # >= r29
```

- [ ] **Step 2: Add `[android]` section**

```toml
[android]
compile_sdk = 36   # winner of Stage-B ladder (36)/35/(34); TERMUX_COMPILE_SDK overrides at runtime
target_sdk  = 36
```

- [ ] **Step 3: Add `[installer]` section**

```toml
[installer]
java     = 'openjdk-21'
zip_tool = '7zip'   # 26.03 (was p7zip)
```

- [ ] **Step 4: Verify and commit**

Verify: `python3 build.py config` parses; `python3 -c "import tomllib;print(tomllib.load(open('build.toml','rb'))['android'])"`
Commit: `build: make toolchain versions configurable in build.toml`

### Task 2: Plumb manifest fields through `package.py`/`package.yaml`

**Files:**
- Modify: `package.yaml`
- Modify: `package.py`
- Modify: `test_build.py`

- [ ] **Step 1: Read `package.py` manifest resolution + `package.yaml` manifest resource**
- [ ] **Step 2: Add `$ndk_version`, `$compile_sdk`, `$target_sdk` template vars in `package.py`** (read from `build.toml`, same path as `$tag`)
- [ ] **Step 3: Add manifest fields to `package.yaml`**

```yaml
"ndk_version": "$ndk_version"
"compile_sdk": "$compile_sdk"
"target_sdk":  "$target_sdk"
```

- [ ] **Step 4: Update the manifest test in `test_build.py`** to read expected values from repo `build.toml` and assert the new fields
- [ ] **Step 5: Verify and commit**

Verify: `pytest test_build.py -v`
Commit: `feat: carry ndk/android versions through package manifest`

### Task 3: Decouple drift linters from hardcoded versions

**Files:**
- Modify: `scripts/ci/check_repo.py`
- Modify: `scripts/ci/check_version_drift.py`

- [ ] **Step 1: Read both linters fully**
- [ ] **Step 2: `check_repo.py`: load `build.toml` as values source; delete literal version dict + `'3.47.4'` fallback default; assert `install_flutter_complete.sh`/`versions_common.sh` match `[ndk] version`**
- [ ] **Step 3: Replace patch-state phrase pins with tag-agnostic markers (the `// Termux` grep markers the patches already use)**
- [ ] **Step 4: `check_version_drift.py`: remove `3.12.0`-specific guards → generic scan over build.toml values**
- [ ] **Step 5: Add checks for new build.toml fields in scripts + post_install.sh**
- [ ] **Step 6: Verify and commit**

Verify: `python scripts/ci/check_repo.py && python scripts/ci/check_version_drift.py`
Commit: `refactor: derive drift checks from build.toml`

### Task 4: `post_install.sh` — manifest-driven, fail-closed

**Files:**
- Modify: `scripts/install/post_install.sh`

- [ ] **Step 1: Read post_install.sh in full (esp. `:588`, `:666-673`, `:856`, `:977-993`, `:1253-1257`)**
- [ ] **Step 2: Replace stale engine-revision fallback `77e2e94772b6…` with `bin/internal/engine.version`; hard error if missing**
- [ ] **Step 3: `__ANDROID_API__` in api-level.h: derive from installed `$ANDROID_SDK/platforms/android-*` (max) instead of hardcoded `35`**
- [ ] **Step 4: Make compile_sdk/target_sdk/ndk_version read from manifest (env override `TERMUX_COMPILE_SDK`), used by the FlutterExtension/gradle patches and `aapt2` override checks**
- [ ] **Step 5: Verify and commit**

Verify: `bash -n scripts/install/post_install.sh && python scripts/ci/check_repo.py`
Commit: `feat: post_install reads manifest, removes stale engine fallback`

### Task 5: Stage-B aapt2 / compileSdk experiment scaffolding

**Files:**
- Modify: `scripts/install/post_install.sh`
- Modify: `scripts/install/flutter_project_config.sh`
- Modify: `scripts/test/gh_e2e_test.sh`
- Modify: `scripts/device/termux_smoke.sh`

- [ ] **Step 1: `post_install.sh`: drop `platform-34-ext7` download step; keep android-36 install; make compile_sdk patch value configurable**
- [ ] **Step 2: `flutter_project_config.sh`: parameterize `= 34` rewrites via `$COMPILE_SDK`/`$TARGET_SDK` (from env/versions file, default per Stage-B winner)**
- [ ] **Step 3: Add `apt-mark hold aapt2` hardening (opt-out `TERMUX_NO_HOLD_AAPT2=1`)**
- [ ] **Step 4: Device gate (manual OR device-smoke) — record result; update `build.toml [android]` to winner** (see spec Stage-B ladder)
- [ ] **Step 5: Verify and commit**

Verify: `bash -n` on the four scripts; `python scripts/ci/check_repo.py`
Commit: `feat: lift compileSdk to <winner> with fail-closed default`

### Task 6: On-device toolchain versions (`versions_common.sh` + installers)

**Files:**
- Create: `scripts/install/versions_common.sh`
- Modify: `install_flutter_complete.sh`
- Modify: `scripts/install/install.sh`
- Modify: `scripts/install/install_termux_flutter.sh`
- Modify: `scripts/install/lib_common.sh`
- Modify: `scripts/device/termux_smoke.sh`
- Modify: `scripts/install/flutter_termux_doctor.sh`
- Modify: `scripts/test/gh_e2e_test.sh`

- [ ] **Step 1: Create `versions_common.sh`** (NDK version/URL/download, JDK, 7zip, compile_sdk/target_sdk, cmdline-tools id)
- [ ] **Step 2: Source it from all installers; remove hardcoded NDK/compileSdk constants from `install_flutter_complete.sh`, `install_termux_flutter.sh`, `lib_common.sh`**
- [ ] **Step 3: `install_flutter_complete.sh`: `p7zip`→`7zip`; drop forced `openjdk-17`; make `configure_ndk_clang` clang-version dynamic (readlink-detect)**
- [ ] **Step 4: `termux_smoke.sh`: prefer `7zip`, drop `p7zip` fallback; fix stale version comments**
- [ ] **Step 5: `flutter_termux_doctor.sh`: version constants from `versions_common.sh`; single JDK**
- [ ] **Step 6: Verify and commit**

Verify: `bash -n install_flutter_complete.sh scripts/install/*.sh scripts/device/termux_smoke.sh scripts/test/gh_e2e_test.sh scripts/install/flutter_termux_doctor.sh; python scripts/ci/check_repo.py`
Commit: `feat: centralize installer versions; modern packages`

### Task 7: Host deps + python harness cleanup

**Files:**
- Modify: `requirements.txt`
- Modify: `pytest.ini`

- [ ] **Step 1: `requirements.txt`**

```
aiohttp==3.14.3
fire==0.7.1
loguru==0.7.3
PyYAML==6.0.3
GitPython==3.1.62
requests==2.34.2
pytest==9.1.1
```

- [ ] **Step 2: `pytest.ini`: fix stale `testpaths = tests` → `testpaths = test_build.py`**
- [ ] **Step 3: Verify and commit**

Verify: `python -m pytest test_build.py -v` (reinstall only if env is wired)
Commit: `chore: bump host deps, pin pytest, fix pytest.ini`

### Task 8: CI/CD workflows

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/validate.yml`
- Modify: `.github/workflows/build-deb.yml`
- Modify: `.github/workflows/device-smoke.yml`
- Modify: `.github/workflows/release-check.yml`
- Delete: `.github/workflows/build.yml`
- Modify: `docs/CI_CD.md`

- [ ] **Step 1: Read all workflows; note current action versions and Python versions**
- [ ] **Step 2: Pin uniformly: `actions/checkout@v7.0.1`, `actions/setup-python@v7.0.0`, `actions/upload-artifact@v7.0.1`; unify Python to 3.12**
- [ ] **Step 3: Delete `build.yml`** (legacy: `newkdev/setup-depot-tools@v1.0.1` unmaintained, no NDK on GH-hosted runners); document removal in `docs/CI_CD.md`
- [ ] **Step 4: `build-deb.yml`: replace `android-ndk-r27d` fallback/error with r29 from config**
- [ ] **Step 5: `ci.yml` py_compile list: add `check_version_drift.py` + `verify_release_asset.py`**
- [ ] **Step 6: Verify and commit**

Verify: YAML parses (`python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in sys.argv[1:]]" .github/workflows/*.yml`); `git diff --check`
Commit: `ci: unify action versions, remove legacy build.yml`

### Task 9: Stabilize `test_build.py` for bumps

**Files:**
- Modify: `test_build.py`

- [ ] **Step 1: Read `test_build.py`; locate literal mocks** (`3.47.3`, engine `06a2e2a…`, framework `e8113bf4…`)
- [ ] **Step 2: Replace with values read from `build.toml` at test time** (resolved consistently for debug/release/profile)
- [ ] **Step 3: Keep patch/hunk contract tests; document the bump procedure in a docstring**
- [ ] **Step 4: Verify and commit**

Verify: `pytest test_build.py -v`
Commit: `test: derive expected versions from build.toml`

### Task 10: Docs & changelog

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/guides/INSTALL_GUIDE.md`
- Modify: `docs/guides/UPGRADE_GUIDE.md`
- Modify: `docs/guides/BUILD_GUIDE.md`
- Modify: `docs/guides/AAPT2_RELEASE_BUILD_BUG_ANALYSIS.md`
- Modify: `docs/releases/CHANGELOG.md`
- Modify: `docs/releases/RELEASE_NOTES.md`
- Modify: `docs/CI_CD.md`

- [ ] **Step 1: README + AGENTS: compileSdk 34→36, aapt2 2.19→16.0.0.4, 7zip, single JDK, config-driven versions**
- [ ] **Step 2: AAPT2 analysis: mark Mode-A compileSdk-34 pin retired per Stage-B winner**
- [ ] **Step 3: UPGRADE_GUIDE: drop "all three modes" (runtime=['debug']); update WSL paths; config-driven fields**
- [ ] **Step 4: CHANGELOG: mode-ordering contradiction correction (utils.py `('release','debug','profile')`) + entry**
- [ ] **Step 5: Verify and commit**

Verify: `python scripts/ci/check_repo.py && python scripts/ci/check_version_drift.py && git diff --check`
Commit: `docs: modernize toolchain documentation`

### Task 11: Full verification pass

- [ ] **Step 1: Run the AGENTS.md lightweight suite**

```bash
python -m py_compile build.py package.py sysroot.py utils.py scripts/ci/check_repo.py
pytest test_build.py -v
bash -n scripts/install/post_install.sh scripts/test/gh_e2e_test.sh scripts/device/termux_smoke.sh install_flutter_complete.sh
python scripts/ci/check_version_drift.py
python scripts/ci/check_repo.py
git diff --check
```

- [ ] **Step 2: Grep for strays**

```bash
rg -n 'p7zip|openjdk-17|clang-18|77e2e947|compileSdk ?= 34|targetSdk ?= 34' --glob '!sysroot/**' --glob '!flutter/**'
```

(Intentional callouts in docs may remain; each is reviewed.)

- [ ] **Step 3: Commit** `chore: final modernization verification`