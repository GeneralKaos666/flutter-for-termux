#!/data/data/com.termux/files/usr/bin/sh
# Debian postinst for the Termux Flutter package.
#
# Runs the shipped post-install configuration automatically so a plain
# `apt install ./flutter_*.deb` leaves a working `flutter` command:
# post_install.sh patches flutter_tools, builds the synthetic git repository
# (the package ships no .git), materialises the NDK/build-tools wrappers and
# compiles flutter_tools.snapshot.
#
# dpkg still holds the apt/dpkg locks while this script runs, so
# post_install.sh is told to skip its own `apt`/`pkg install` calls
# (TERMUX_POSTINST=1). Anything it skips is picked up when the user re-runs:
#   bash $PREFIX/share/flutter/post_install.sh
#
# A failing post_install must never fail the package: dpkg would leave the
# package unconfigured. Failures are reported and logged; exit is always 0.

set -u

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PREFIX
export HOME="${HOME:-${PREFIX%/usr}/home}"
export PATH="$PREFIX/bin:/system/bin:$PATH"

SCRIPT="$PREFIX/share/flutter/post_install.sh"
LOG="$PREFIX/share/flutter/post_install.log"

case "${1:-}" in
configure) ;;
*) exit 0 ;;
esac

if [ "${TERMUX_SKIP_POSTINST:-false}" = "true" ]; then
	echo "flutter: post-install skipped (TERMUX_SKIP_POSTINST=true); run: bash $SCRIPT"
	exit 0
fi

if [ ! -f "$SCRIPT" ]; then
	echo "flutter: post-install script missing at $SCRIPT; package payload incomplete" >&2
	exit 0
fi

export TERMUX_POSTINST=true

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
{
	echo "== $(date -u '+%Y-%m-%dT%H:%M:%SZ') post_install.sh via dpkg postinst (from ${2:-fresh install}) =="
} >>"$LOG" 2>&1 || true

rc=0
if command -v timeout >/dev/null 2>&1; then
	timeout 1800 "$PREFIX/bin/bash" "$SCRIPT" >>"$LOG" 2>&1 || rc=$?
else
	"$PREFIX/bin/bash" "$SCRIPT" >>"$LOG" 2>&1 || rc=$?
fi

if [ "$rc" -ne 0 ]; then
	echo "flutter: post-install reported exit $rc (log: $LOG)" >&2
	echo "flutter: re-run it with: bash $SCRIPT" >&2
else
	echo "flutter: post-install configuration complete (log: $LOG)"
	"$SCRIPT" --status >>"$LOG" 2>&1 || true
fi

exit 0
