#!/data/data/com.termux/files/usr/bin/sh
# Debian preinst for the Termux Flutter package.
#
# Two upgrades need cleanup before dpkg unpacks the new payload:
#
#  1. Releases up to 3.50.0-3 shipped the framework's full git history
#     (~490 MiB). New releases ship no .git; post_install.sh creates the small
#     synthetic repository `flutter` needs. dpkg never deletes files a new
#     version omits, so the old history has to be removed here.
#  2. Releases up to 3.50.0-3 packaged build-only trees (engine/, dev/, docs/,
#     examples/, SDK test suites, agent metadata) that are now excluded from
#     package.yaml. They would otherwise linger on upgraded installs.
#
# A user-owned checkout is never destroyed: .git is only removed when it is the
# upstream clone this package used to ship.

set -u

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
ROOT="${FLUTTER_ROOT:-$PREFIX/opt/flutter}"

case "${1:-}" in
abort-*) exit 0 ;;
esac

[ -d "$ROOT" ] || exit 0

log() { echo "flutter: $*"; }

purge_legacy_git() {
	[ -d "$ROOT/.git" ] || return 0
	# Synthetic repos are the current on-device form and are a few KiB; leave
	# them for post_install.sh to verify and repair.
	[ -f "$ROOT/.git/termux_synthetic" ] && return 0
	if ! command -v git >/dev/null 2>&1; then
		log "git unavailable; leaving $ROOT/.git untouched"
		return 0
	fi
	origin="$(git -C "$ROOT" config --get remote.origin.url 2>/dev/null || true)"
	case "$origin" in
	*github.com/flutter/flutter*)
		rm -rf "$ROOT/.git"
		log "removed legacy framework git history (reclaimed ~490 MiB)"
		;;
	*)
		log "keeping user-owned git checkout at $ROOT/.git (origin: ${origin:-none})"
		;;
	esac
}

purge_legacy_trees() {
	for dir in engine dev docs examples .github .agents .claude .gemini .vscode agent-artifacts; do
		if [ -e "$ROOT/$dir" ]; then
			rm -rf "${ROOT:?}/$dir"
			log "removed stale packaged tree: $dir/"
		fi
	done
	for dir in "$ROOT"/packages/*/test "$ROOT"/packages/*/test_driver; do
		[ -e "$dir" ] || continue
		rm -rf "${dir:?}"
		log "removed stale packaged tree: ${dir#"$ROOT"/}"
	done
}

purge_legacy_git
purge_legacy_trees

exit 0
