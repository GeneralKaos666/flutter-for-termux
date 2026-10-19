"""Contract tests for the apt repository script renderer and stub maintainer scripts."""

import functools
import hashlib
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path

# scripts/release/ is not a package, so put it on sys.path and import by name.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "release"))

from apt_repo import load_config, render_scripts  # noqa: E402

SPEC_GROUPS = ["bin/flutter", "etc/profile.d/flutter.sh", "opt/flutter", "share/flutter"]
FILE_GROUPS = frozenset({"bin/flutter", "etc/profile.d/flutter.sh"})
_BASH_SHEBANG = "#!/data/data/com.termux/files/usr/bin/bash"


def _render(owned=SPEC_GROUPS):
    return render_scripts(load_config()["version"], owned)


def _run(name, args, prefix, env=None, text=None):
    """Run the rendered ``name`` script with PREFIX/TMPDIR inside ``prefix``'s tempdir."""
    script = Path(prefix.parent) / f"{name}.rendered"
    script.write_text(_render()[name] if text is None else text, encoding="utf-8")
    run_env = {**os.environ, "PREFIX": str(prefix), "TMPDIR": str(prefix.parent)}
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=run_env,
    )


def _tree(root):
    """Relative paths of every file and directory under ``root`` (dotfiles included)."""
    entries = set()
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in [*dirnames, *filenames]:
            entries.add(str((base / name).relative_to(root)))
    return entries


def _seed_prefix(prefix, marker="old"):
    """Create the four owned groups (marker content ``marker``) + a stale snapshot.

    The stale ``$PREFIX/opt/.flutter.old.flutter`` is exactly what a killed run
    leaves behind; ``postinst`` must dispose of it and ``postrm`` must not
    touch a foreign ``.flutter.old.*`` in a shared directory.
    """
    for group in SPEC_GROUPS:
        path = prefix / group
        if group in FILE_GROUPS:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{marker}\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "marker").write_text(f"{marker}\n", encoding="utf-8")
    (prefix / "opt" / ".flutter.old.flutter").write_text("stale\n", encoding="utf-8")


def _seed(prefix):
    """Seed owned groups plus stale/foreign files a postrm must sort out."""
    _seed_prefix(prefix, marker="owned")
    (prefix / "bin" / "other-tool").write_text("foreign\n", encoding="utf-8")
    (prefix / "etc" / "profile.d" / "other.sh").write_text("foreign\n", encoding="utf-8")
    (prefix / "bin" / ".flutter.old.other").write_text("foreign\n", encoding="utf-8")


def _shim_dir(root, arch):
    """Create a PATH-prependable dir whose ``uname`` always prints ``arch``."""
    shim = Path(root) / "shim"
    shim.mkdir(exist_ok=True)
    uname = shim / "uname"
    uname.write_text(f"{_BASH_SHEBANG}\necho {arch}\n", encoding="utf-8")
    uname.chmod(0o755)
    return shim


def _marker(prefix, group):
    """Content the payload (or the seed) wrote for ``group``."""
    path = Path(prefix) / group
    if group in FILE_GROUPS:
        return path.read_text(encoding="utf-8")
    return (path / "marker").read_text(encoding="utf-8")


def _snapshots(prefix):
    """Relative paths of every ``.flutter.old.*`` snapshot under ``prefix``."""
    return {entry for entry in _tree(prefix) if ".flutter.old." in entry}


def _write_shim(directory, name, body):
    """Write an executable ``name`` shim that runs ``body`` under the Termux bash."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / name
    shim.write_text(f"{_BASH_SHEBANG}\n{body}\n", encoding="utf-8")
    shim.chmod(0o755)
    return shim


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
def _make_payload(prefix, body="new", post_install_rc=0):
    """Package a payload deb whose data root mirrors ``prefix``.

    ``dpkg-deb`` stores data members relative to ``/``, so a group is packaged
    at ``prefix`` without its leading slash: on a device that is exactly the
    canonical ``data/data/com.termux/files/usr/<group>`` layout, and the tests
    shift the same shape under their throwaway ``PREFIX`` (which is why the
    helper takes the prefix, not a fixed path).

    Every group carries a ``body`` marker file and ``share/flutter`` also ships
    a ``post_install.sh`` that touches ``post_install.stamp`` and exits with
    ``post_install_rc`` (the rollback lever).
    """
    prefix = Path(prefix)
    build = prefix.parent / "payload-build"
    if build.exists():
        shutil.rmtree(build)
    (build / "DEBIAN").mkdir(parents=True)
    # dpkg-deb refuses a control directory the umask made private.
    (build / "DEBIAN").chmod(0o755)
    (build / "DEBIAN" / "control").write_text(
        "Package: flutter-payload\n"
        "Version: 0.0.1\n"
        "Architecture: all\n"
        "Maintainer: test <test@example.com>\n"
        "Description: postinst lifecycle test payload\n",
        encoding="utf-8",
    )
    data = build / str(prefix).lstrip("/")
    for group in SPEC_GROUPS:
        path = data / group
        if group in FILE_GROUPS:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{body}\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "marker").write_text(f"{body}\n", encoding="utf-8")
    (data / "share/flutter/post_install.sh").write_text(
        f'{_BASH_SHEBANG}\ntouch "$(dirname "$0")/post_install.stamp"\nexit {post_install_rc}\n',
        encoding="utf-8",
    )
    deb = prefix.parent / f"payload-{body}-{post_install_rc}.deb"
    subprocess.run(
        ["dpkg-deb", "--build", str(build), str(deb)],
        check=True,
        capture_output=True,
        text=True,
    )
    return deb


def _publish(server_dir, payload, *, version=None, sha="good", size="good"):
    """Publish ``payload`` as the release asset trio for ``version``.

    ``version`` defaults to the configured one; the release asset name is
    ``flutter_<version>_aarch64.deb``. ``sha="bad"`` / ``size="bad"`` publish a
    deliberately wrong entry so verification must abort.
    """
    if version is None:
        version = load_config()["version"]
    server_dir = Path(server_dir)
    server_dir.mkdir(parents=True, exist_ok=True)
    name = f"flutter_{version}_aarch64.deb"
    asset = server_dir / name
    shutil.copyfile(payload, asset)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    if sha == "bad":
        digest = "0" * 64
    (server_dir / f"{name}.sha256").write_text(f"{digest}  {name}\n", encoding="utf-8")
    measured = asset.stat().st_size if size == "good" else 1
    (server_dir / f"{name}.size.txt").write_text(f"{measured}\n", encoding="utf-8")


def _run_postinst(prefix, version, base, *, owned=SPEC_GROUPS, shims=None):
    """Render ``postinst`` for ``version`` and run ``postinst configure``.

    ``PREFIX``/``TMPDIR``/``FLUTTER_RELEASE_BASE`` point into the test's tempdir,
    and ``apt`` is always shimmed to a silent failure so the build-tool staging
    block can never touch the host package database.
    """
    bin_dir = Path(prefix.parent) / "harness-bin"
    for name, body in {"apt": "exit 1", **(shims or {})}.items():
        _write_shim(bin_dir, name, body)
    env = {
        "FLUTTER_RELEASE_BASE": base,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    return _run(
        "postinst",
        ["configure"],
        prefix,
        env=env,
        text=render_scripts(version, owned)["postinst"],
    )


class RendererTest(unittest.TestCase):
    def test_load_config_matches_build_toml(self):
        cfg = load_config()  # repo-root build.toml
        raw = tomllib.load(open("build.toml", "rb"))
        assert cfg["tag"] == raw["flutter"]["tag"]
        assert cfg["pkg_rel"] == raw["package"]["pkg_rel"].strip()
        expected = f'{cfg["tag"]}-{cfg["pkg_rel"]}' if cfg["pkg_rel"] else cfg["tag"]
        assert cfg["version"] == expected

    def test_render_scripts_substitutes_placeholders(self):
        for name, text in render_scripts("9.9.9-7", ["opt/flutter", "bin/flutter"]).items():
            assert "9.9.9-7" in text                      # @VERSION@ applied
            assert re.search(r"@[A-Z_]+@", text) is None  # no placeholder survives
            assert "OWNED_PATHS=(" in text                # array injected

    def test_rendered_scripts_pass_bash_n(self):
        for text in _render().values():
            subprocess.run(["bash", "-n"], input=text, text=True, capture_output=True, check=True)

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck not installed")
    def test_rendered_scripts_pass_shellcheck(self):
        for name, text in _render().items():
            result = subprocess.run(
                ["shellcheck", "--severity=error", "-s", "bash", "-"],
                input=text,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stdout}")


class PreinstTest(unittest.TestCase):
    # install / upgrade / aborted-upgrade / no argument at all
    _DPKG_ARGS = (["install"], ["upgrade"], ["aborted-upgrade"], [])

    def test_preinst_rejects_non_aarch64(self):
        with tempfile.TemporaryDirectory() as td:
            shim = _shim_dir(td, "x86_64")
            prefix = Path(td) / "prefix"
            prefix.mkdir()
            env = {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}
            for args in self._DPKG_ARGS:
                result = _run("preinst", args, prefix, env=env)
                self.assertEqual(result.returncode, 1, f"{args}: {result.stderr}")
                self.assertIn("aarch64", result.stdout + result.stderr)

    def test_preinst_accepts_aarch64(self):
        with tempfile.TemporaryDirectory() as td:
            shim = _shim_dir(td, "aarch64")
            prefix = Path(td) / "prefix"
            prefix.mkdir()
            env = {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}
            for args in self._DPKG_ARGS:
                result = _run("preinst", args, prefix, env=env)
                self.assertEqual(result.returncode, 0, f"{args}: {result.stderr}")


class PostrmTest(unittest.TestCase):
    def test_postrm_removes_owned_groups_on_remove_and_purge(self):
        foreign = {"bin/other-tool", "etc/profile.d/other.sh", "bin/.flutter.old.other"}
        for arg in ("remove", "purge"):
            with tempfile.TemporaryDirectory() as td:
                prefix = Path(td) / "prefix"
                _seed(prefix)
                result = _run("postrm", [arg], prefix)
                self.assertEqual(result.returncode, 0, result.stderr)
                after = _tree(prefix)
                for group in SPEC_GROUPS:
                    self.assertNotIn(group, after, f"{group} survived {arg}")
                # every flutter-owned snapshot gone; the foreign one still stands
                stale = {entry for entry in after if ".flutter.old." in entry}
                self.assertEqual(stale, {"bin/.flutter.old.other"}, f"snapshot on {arg}")
                self.assertTrue(foreign <= after, f"foreign files lost on {arg}: {foreign - after}")

    def test_postrm_is_noop_for_every_other_dpkg_argument(self):
        for arg in ("upgrade", "failed-upgrade", "abort-install", "abort-upgrade", "disappear"):
            with tempfile.TemporaryDirectory() as td:
                prefix = Path(td) / "prefix"
                _seed(prefix)
                before = _tree(prefix)
                result = _run("postrm", [arg], prefix)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(_tree(prefix), before, f"{arg} must be a no-op")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static file handler that keeps the test output free of request logs."""

    def log_message(self, *args):
        pass


class PostinstLifecycleTest(unittest.TestCase):
    """Integration tests for the `postinst` payload lifecycle (spec section 4)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.prefix = self.tmp / "prefix"
        self.server_dir = self.tmp / "server"
        self.config = load_config()
        self.version = self.config["version"]

    def _serve(self, directory):
        """Serve ``directory`` on an ephemeral port; returns ``(server, base_url)``."""
        handler = functools.partial(_QuietHandler, directory=str(directory))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)

        def stop():
            server.shutdown()
            server.server_close()

        self.addCleanup(stop)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def _assert_no_work_leftovers(self):
        leftovers = sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("apt-stub."))
        self.assertEqual(leftovers, [], "postinst leaked its working directory")

    def test_fresh_install_swaps_payload_and_writes_marker(self):
        _seed_prefix(self.prefix)
        payload = _make_payload(self.prefix, body="new")
        _publish(self.server_dir, payload, version=self.version)
        _, base = self._serve(self.server_dir)

        result = _run_postinst(self.prefix, self.version, base)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for group in SPEC_GROUPS:
            self.assertEqual(_marker(self.prefix, group).strip(), "new", group)
        # `printf '%s\n'` appends a newline; the spec says the marker contains
        # exactly the version, so compare on the stripped content.
        marker = (self.prefix / "share/flutter/.stub-version").read_text(encoding="utf-8")
        self.assertEqual(marker.strip(), self.version)
        self.assertTrue((self.prefix / "share/flutter/post_install.stamp").exists())
        self.assertEqual(_snapshots(self.prefix), set())
        self._assert_no_work_leftovers()

    def test_verification_failure_leaves_existing_payload_untouched(self):
        for sha, size in (("bad", "good"), ("good", "bad")):
            with self.subTest(sha=sha, size=size):
                prefix = self.tmp / f"prefix-{sha}-{size}"
                server_dir = self.tmp / f"server-{sha}-{size}"
                _seed_prefix(prefix)
                payload = _make_payload(prefix, body="new")
                _publish(server_dir, payload, version=self.version, sha=sha, size=size)
                _, base = self._serve(server_dir)
                before = _tree(prefix)

                result = _run_postinst(prefix, self.version, base)

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                # Verify runs before anything is touched (spec step 4), so the
                # tree is bit-for-bit unchanged — including the planted stale
                # snapshot, which only the snapshot step would dispose of.
                self.assertEqual(_tree(prefix), before)
                for group in SPEC_GROUPS:
                    self.assertEqual(_marker(prefix, group).strip(), "old", group)
                self.assertFalse((prefix / "share/flutter/.stub-version").exists())
                self._assert_no_work_leftovers()

    def test_post_install_failure_rolls_back_snapshot(self):
        _seed_prefix(self.prefix)
        payload = _make_payload(self.prefix, body="new", post_install_rc=1)
        _publish(self.server_dir, payload, version=self.version)
        _, base = self._serve(self.server_dir)

        result = _run_postinst(self.prefix, self.version, base)

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        for group in SPEC_GROUPS:
            self.assertEqual(_marker(self.prefix, group).strip(), "old", group)
        self.assertEqual(_snapshots(self.prefix), set())
        self.assertFalse((self.prefix / "share/flutter/.stub-version").exists())
        self._assert_no_work_leftovers()

    def test_marker_makes_postinst_idempotent(self):
        _seed_prefix(self.prefix)
        payload = _make_payload(self.prefix, body="new")
        _publish(self.server_dir, payload, version=self.version)
        _, base = self._serve(self.server_dir)

        first = _run_postinst(self.prefix, self.version, base)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        stamp = self.prefix / "share/flutter/post_install.stamp"
        self.assertTrue(stamp.exists())

        stamp.unlink()
        # Nothing listens on port 1: a correct marker check returns before any
        # download is attempted.
        second = _run_postinst(self.prefix, self.version, "http://127.0.0.1:1")

        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(stamp.exists(), "post_install.sh did not re-run")
        self.assertEqual(_snapshots(self.prefix), set())
        self._assert_no_work_leftovers()

    def test_failed_upgrade_restores_v1_and_recovers(self):
        tag = self.config["tag"]
        v1 = self.version
        v2 = f"{tag}-999"
        _seed_prefix(self.prefix)
        _publish(self.server_dir, _make_payload(self.prefix, body="v1"), version=v1)
        _, base = self._serve(self.server_dir)
        first = _run_postinst(self.prefix, v1, base)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(_marker(self.prefix, "opt/flutter").strip(), "v1")

        upgrade = _make_payload(self.prefix, body="new")
        _publish(self.server_dir, upgrade, version=v2, sha="bad")
        failed = _run_postinst(self.prefix, v2, base)

        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        marker = (self.prefix / "share/flutter/.stub-version").read_text(encoding="utf-8")
        self.assertEqual(marker.strip(), v1)
        for group in SPEC_GROUPS:
            self.assertEqual(_marker(self.prefix, group).strip(), "v1", group)
        self.assertEqual(_snapshots(self.prefix), set())

        _publish(self.server_dir, upgrade, version=v2)
        recovered = _run_postinst(self.prefix, v2, base)

        self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        marker = (self.prefix / "share/flutter/.stub-version").read_text(encoding="utf-8")
        self.assertEqual(marker.strip(), v2)
        for group in SPEC_GROUPS:
            self.assertEqual(_marker(self.prefix, group).strip(), "new", group)
        self._assert_no_work_leftovers()

    def test_upgrade_disposes_foreign_payload(self):
        _seed_prefix(self.prefix)
        foreign = self.prefix / "opt/flutter/foreign_blob"
        foreign.write_text("foreign\n", encoding="utf-8")
        (self.prefix / "bin/flutter").write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
        payload = _make_payload(self.prefix, body="new")
        _publish(self.server_dir, payload, version=self.version)
        _, base = self._serve(self.server_dir)

        result = _run_postinst(self.prefix, self.version, base)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(foreign.exists(), "foreign payload survived the swap")
        self.assertEqual(_marker(self.prefix, "opt/flutter").strip(), "new")
        self.assertEqual(_snapshots(self.prefix), set())
        self._assert_no_work_leftovers()

    def test_preflight_requires_free_space(self):
        _seed_prefix(self.prefix)
        before = _tree(self.prefix)
        payload = _make_payload(self.prefix, body="new")
        _publish(self.server_dir, payload, version=self.version)
        _, base = self._serve(self.server_dir)
        df_body = (
            'printf "%s\\n" "Filesystem 1024-blocks Used Available Capacity Mounted on"'
            ' "shim 0 0 1024 0% /"'
        )

        result = _run_postinst(self.prefix, self.version, base, shims={"df": df_body})

        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("free space", (result.stdout + result.stderr).lower())
        self.assertEqual(_tree(self.prefix), before)
        self._assert_no_work_leftovers()
