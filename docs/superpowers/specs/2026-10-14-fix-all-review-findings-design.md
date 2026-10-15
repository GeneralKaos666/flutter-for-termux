# Design Spec: Fix All Review Findings

Date: 2026-10-15 (context: findings from review of HEAD 1051633)

## 1) Problem statement
Fix all 46 review findings across 4 surfaces (Python core, shell/device, CI/config, docs), keeping all gates green: ruff, pytest, check_repo, check_version_drift, generate_versions --check, bash -n, shellcheck --severity=error. Split into reviewable PRs. Shipping changes bump pkg_rel 4→5 once.

## 2) PR partitioning
- PR1 (fix/shipping): runtime defects + packaging + version bump
- PR2 (docs/contract): all doc↔code contradictions (docs-only)
- PR3 (refactor/python): Python smells (behavior-preserving)
- PR4 (chore/ci): shell smells + CI/config hardening

## 3) Per-finding decisions
[Full table as approved.]

See also: decisions from follow-ups:
- NDK duplication: keep both copies, add cross-reference + CI drift check.
- openjdk in package.yaml: docs only (no Depends/Recommends change).
- output path: set build.toml package.path='release'.
- pkg_rel bump: use check_version_drift --fix; do not pin new deb hash yet.

## 4) Testing/verification strategy
- Python: add/update pytest; keep existing 34 passing.
- Shell: smoke assertions where feasible; device-only listed in PR descriptions.
- Gates: run the full set per PR.

## 5) Rollout/release
Order PR1→PR2→PR3→PR4. PR1 bumps pkg_rel once. Follow Conventional Commits.

## 6) Risks
See table notes.

