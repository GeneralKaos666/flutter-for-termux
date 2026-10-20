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

import yaml

# scripts/release/ is not a package, so put it on sys.path and import by name.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "release"))

import apt_repo  # noqa: E402
from apt_repo import load_config, render_scripts  # noqa: E402

SPEC_GROUPS = ["bin/flutter", "etc/profile.d/flutter.sh", "opt/flutter", "share/flutter"]
FILE_GROUPS = frozenset({"bin/flutter", "etc/profile.d/flutter.sh"})

# The product templates (scripts/release/stub/*) keep their Termux shebang on
# purpose -- they only ever run on Termux. The *test doubles* written below are
# executed directly by the rendered maintainer scripts, so on a non-Termux host
# (CI) the kernel must still find the interpreter. Resolve bash from PATH and
# fall back to the Termux absolute path only when PATH has none, so a Termux
# checkout without bash on PATH behaves exactly as before.
_TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash"
_BASH_SHEBANG = f"#!{shutil.which('bash') or _TERMUX_BASH}"


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
    canonical Termux ``$PREFIX/<group>`` layout, and the tests shift the same
    shape under their throwaway ``PREFIX`` (which is why the helper takes the
    prefix, not a fixed path).

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


class HarnessPortabilityTest(unittest.TestCase):
    """The test doubles' shebang must resolve on whatever host runs pytest."""

    def test_shim_interpreter_is_host_portable(self):
        interpreter = Path(_BASH_SHEBANG[2:])
        self.assertTrue(interpreter.is_file(), f"no interpreter at {interpreter}")
        self.assertTrue(os.access(interpreter, os.X_OK), f"{interpreter} is not executable")

        with tempfile.TemporaryDirectory() as td:
            shim = _write_shim(td, "harness-probe", "echo harness-ok")
            result = subprocess.run([str(shim)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "harness-ok")


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


_PACKAGE_YAML = Path("package.yaml")


def package_yaml_text() -> str:
    """Text of the payload's ``package.yaml`` — the stub's contract source."""
    return _PACKAGE_YAML.read_text(encoding="utf-8")


class StubControlTest(unittest.TestCase):
    """The stub's control contract and its owned-path groups (spec sections 4, 9)."""

    def test_package_yaml_names_current_maintainer(self):
        control = yaml.safe_load(package_yaml_text())["control"]
        assert control["Maintainer"] == "GeneralKaos666 <xkiller1177@gmail.com>"

    def test_parse_owned_paths_returns_the_four_spec_groups(self):
        assert apt_repo.parse_owned_paths(_PACKAGE_YAML) == SPEC_GROUPS

    def test_parse_owned_paths_never_yields_toplevel_directories(self):
        # a group that is a bare first component would make postrm delete, e.g.,
        # all of $PREFIX/etc — forbid it at the contract level
        assert all(len(Path(p).parts) >= 2 for p in apt_repo.parse_owned_paths(_PACKAGE_YAML))

    def test_render_control_matches_stub_contract(self):
        cfg = load_config()
        text = apt_repo.render_control(cfg["version"], _PACKAGE_YAML)
        assert "Version: " + cfg["version"] in text
        assert "Architecture: all" in text
        assert "Maintainer: GeneralKaos666 <xkiller1177@gmail.com>" in text
        assert "Pre-Depends: x11-repo" in text
        assert (
            "Depends: git, which, gtk3, xorgproto, ninja, cmake, clang, "
            "pkg-config, openjdk-21, wget, curl, unzip, 7zip, binutils, tar, "
            "xz-utils"
        ) in text

    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
    def test_stub_command_builds_control_only_arch_all_deb(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "work"
            version = load_config()["version"]
            result = subprocess.run(
                [sys.executable, "scripts/release/apt_repo.py", "stub", "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            deb = out / f"flutter_{version}_all.deb"
            self.assertTrue(
                deb.is_file(), f"missing {deb}: {sorted(p.name for p in out.iterdir())}"
            )
            info = subprocess.run(
                ["dpkg-deb", "-I", str(deb)], capture_output=True, text=True, check=True
            ).stdout
            for line in (
                "Package: flutter",
                f"Version: {version}",
                "Architecture: all",
                "Maintainer: GeneralKaos666 <xkiller1177@gmail.com>",
                "Pre-Depends: x11-repo",
            ):
                self.assertIn(line, info)
            # No data payload: the stub is control-only, so nothing owns opt/flutter.
            listing = subprocess.run(
                ["dpkg-deb", "-c", str(deb)], capture_output=True, text=True
            ).stdout
            self.assertNotIn("opt/flutter", listing)
            # The maintainer scripts ride along in the control member.
            extracted = Path(td) / "control"
            subprocess.run(
                ["dpkg-deb", "-e", str(deb), str(extracted)],
                capture_output=True,
                text=True,
                check=True,
            )
            for name in ("preinst", "postinst", "postrm"):
                script = extracted / name
                self.assertTrue(script.is_file(), f"{name} missing from control")
                self.assertTrue(script.stat().st_mode & 0o111, f"{name} is not executable")
            # The stage directory is cleaned up on the success path.
            self.assertFalse((out / ".stub-root").exists())

    @unittest.skipUnless(shutil.which("dpkg"), "dpkg not installed")
    def test_our_version_outranks_unrevisioned_tag(self):
        cfg = load_config()
        self.assertTrue(cfg["pkg_rel"], "build.toml must carry a package revision")
        result = subprocess.run(
            ["dpkg", "--compare-versions", cfg["version"], "gt", cfg["tag"]],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"{cfg['version']} does not outrank {cfg['tag']}")


def _primary_fingerprints(colons_output):
    """Primary-key fingerprints from ``gpg --with-colons`` output.

    Records arrive as a ``sec:``/``pub:`` line followed by its own ``fpr:`` line
    and then one per subkey; only the ``fpr:`` directly after the primary record
    is the key's fingerprint.
    """
    fingerprints, expecting = [], False
    for line in colons_output.splitlines():
        kind = line.split(":", 1)[0]
        if kind in ("sec", "pub"):
            expecting = True
        elif kind == "fpr" and expecting:
            fingerprints.append(line.split(":")[9])
            expecting = False
    return fingerprints


def _gpg_test_key(home: Path) -> str:
    """Create a throwaway empty-passphrase ed25519 signing key in ``home``.

    Returns the new key's fingerprint. ``home`` is a disposable ``GNUPGHOME``, so
    the user's real keyring is never touched.
    """
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "flutter-for-termux apt repo (test) <apt-repo@example.invalid>",
            "ed25519",
            "sign",
            "never",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return _primary_fingerprints(listing)[0]


def _release_fields(text):
    """Split a ``Release`` file into header fields and its ``SHA256`` block.

    ``{path: (digest, size)}`` — the block's entries are the indented lines.
    """
    fields, hashes = {}, {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith(" "):
            digest, size, relative = line.split()
            hashes[relative] = (digest, size)
        else:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields, hashes


def _control_member(deb: Path, into: Path) -> Path:
    """Extract ``deb``'s control member into ``into`` and return the directory."""
    subprocess.run(
        ["dpkg-deb", "-e", str(deb), str(into)],
        capture_output=True,
        text=True,
        check=True,
    )
    return into


class StubOverrideTest(unittest.TestCase):
    """The ``pkg_rel`` override and the ``build_toml`` seam ``build_stub`` closes.

    Both must be pinned by an input that cannot come from the repo config, or a
    ``render_control``/``render_scripts`` that ignored the passed values and read
    the repo-root ``build.toml`` itself would pass unnoticed.
    """

    def test_render_control_pins_the_passed_version(self):
        # 9.9.9-7 cannot come from build.toml, so ignoring the parameter fails.
        text = apt_repo.render_control("9.9.9-7", _PACKAGE_YAML)
        self.assertIn("Version: 9.9.9-7", text)
        self.assertNotIn(load_config()["version"], text)

    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
    def test_build_stub_uses_the_pkg_rel_override(self):
        tag = load_config()["tag"]
        with tempfile.TemporaryDirectory() as td:
            deb = apt_repo.build_stub(out_dir=Path(td) / "work", pkg_rel="9")
            self.assertEqual(deb.name, f"flutter_{tag}-9_all.deb")
            info = subprocess.run(
                ["dpkg-deb", "-I", str(deb)], capture_output=True, text=True, check=True
            ).stdout
            self.assertIn(f"Version: {tag}-9", info)

    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
    def test_stub_command_pkg_rel_override_reaches_control(self):
        # Also a regression guard: Fire parses a numeric --pkg-rel as int.
        tag = load_config()["tag"]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "work"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/release/apt_repo.py",
                    "stub",
                    "--out",
                    str(out),
                    "--pkg-rel",
                    "9",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deb = out / f"flutter_{tag}-9_all.deb"
            self.assertTrue(deb.is_file(), result.stdout + result.stderr)
            info = subprocess.run(
                ["dpkg-deb", "-I", str(deb)], capture_output=True, text=True, check=True
            ).stdout
            self.assertIn(f"Version: {tag}-9", info)

    @unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
    def test_build_stub_forwards_build_toml_to_the_maintainer_scripts(self):
        # A fixture config: the control version and the postinst URL tag must
        # both come from it, not from the repo-root build.toml.
        fixture_tag, fixture_rel = "9.9.9", "4"
        with tempfile.TemporaryDirectory() as td:
            build_toml = Path(td) / "build.toml"
            build_toml.write_text(
                f"[flutter]\ntag = '{fixture_tag}'\n\n"
                "[installer]\njava = 'openjdk-21'\nzip_tool = '7zip'\n\n"
                f"[package]\npkg_rel = '{fixture_rel}'\n",
                encoding="utf-8",
            )
            deb = apt_repo.build_stub(out_dir=Path(td) / "work", build_toml=build_toml)
            self.assertEqual(deb.name, f"flutter_{fixture_tag}-{fixture_rel}_all.deb")

            control = _control_member(deb, Path(td) / "control")
            self.assertIn(
                f"Version: {fixture_tag}-{fixture_rel}",
                (control / "control").read_text(encoding="utf-8"),
            )
            postinst = (control / "postinst").read_text(encoding="utf-8")
            self.assertIn(f'VERSION="{fixture_tag}-{fixture_rel}"', postinst)
            self.assertIn(f"releases/download/{fixture_tag}", postinst)
            for name in ("preinst", "postinst", "postrm"):
                self.assertIsNone(
                    re.search(r"@[A-Z_]+@", (control / name).read_text(encoding="utf-8")),
                    f"{name} kept an unrendered placeholder",
                )


# The exact 16-item union the stub's Depends must carry into the Packages index.
_STUB_DEPENDS = (
    "git, which, gtk3, xorgproto, ninja, cmake, clang, pkg-config, "
    "openjdk-21, wget, curl, unzip, 7zip, binutils, tar, xz-utils"
)
# Release lists index files relative to dists/<suite>/ (the component root).
_PINNED_INDEX_FILES = (
    "main/Contents-aarch64",
    "main/Contents-aarch64.xz",
    "main/binary-aarch64/Packages",
    "main/binary-aarch64/Packages.xz",
)


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb not installed")
@unittest.skipUnless(shutil.which("gpg"), "gpg not installed")
@unittest.skipUnless(shutil.which("xz"), "xz not installed")
class RepoTest(unittest.TestCase):
    """The ``repo`` generator: indexes, clearsigned Release, key and site (§§3, 5).

    One throwaway empty-passphrase signing key in a disposable ``GNUPGHOME`` and
    one indexed stub deb serve every test in the class; ``GNUPGHOME`` is reset
    afterwards so the user's real keyrings are never read or written.
    """

    @classmethod
    def _build_site(cls) -> Path:
        """Build one stub deb and index it with the throwaway key; returns the site root."""
        cls.deb = apt_repo.build_stub(out_dir=cls.tmp / "work")
        return Path(apt_repo.build_repo(cls.deb, out_dir=cls.tmp / "site", passphrase=""))

    @classmethod
    def _restore_gnupghome(cls):
        if shutil.which("gpgconf"):
            subprocess.run(
                ["gpgconf", "--kill", "gpg-agent"],
                env={**os.environ, "GNUPGHOME": str(cls.gnupghome)},
                capture_output=True,
            )
        if cls._previous_gnupghome is None:
            os.environ.pop("GNUPGHOME", None)
        else:
            os.environ["GNUPGHOME"] = cls._previous_gnupghome

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.tmp = Path(cls._tmp.name)
        cls.gnupghome = cls.tmp / "gnupghome"
        cls.gnupghome.mkdir(mode=0o700)
        cls._previous_gnupghome = os.environ.get("GNUPGHOME")
        os.environ["GNUPGHOME"] = str(cls.gnupghome)
        cls.addClassCleanup(cls._restore_gnupghome)
        cls.fingerprint = _gpg_test_key(cls.gnupghome)
        cls.config = load_config()
        cls.site = cls._build_site()

    def _dists(self) -> Path:
        return self.site / "dists" / "termux"

    def _binary(self) -> Path:
        return self._dists() / "main" / "binary-aarch64"

    def _published_deb(self) -> Path:
        return self._binary() / f"flutter_{self.config['version']}_all.deb"

    def test_packages_index_stub_fields(self):
        packages = (self._binary() / "Packages").read_text(encoding="utf-8")
        deb = self._published_deb()
        self.assertTrue(
            deb.is_file(),
            f"stub deb not published: {sorted(p.name for p in self._binary().iterdir())}",
        )
        for needle in (
            "Package: flutter",
            "Version: " + self.config["version"],
            "Architecture: all",
            "Maintainer: GeneralKaos666 <xkiller1177@gmail.com>",
            "Pre-Depends: x11-repo",
            "Depends: " + _STUB_DEPENDS,
            f"Filename: dists/termux/main/binary-aarch64/{deb.name}",
            f"Size: {deb.stat().st_size}",
            "SHA256: " + hashlib.sha256(deb.read_bytes()).hexdigest(),
        ):
            self.assertIn(needle, packages)
        # A blank line terminates the paragraph so apt can find the next entry.
        self.assertTrue(packages.endswith("\n\n"), repr(packages[-20:]))

    def test_release_sha256_block_covers_every_index_file(self):
        fields, hashes = _release_fields((self._dists() / "Release").read_text(encoding="utf-8"))

        self.assertEqual(fields["Codename"], "termux")
        self.assertEqual(fields["Suite"], "termux")
        self.assertEqual(fields["Components"], "main")
        self.assertEqual(fields["Architectures"], "aarch64")
        self.assertEqual(fields["Version"], "1")
        self.assertEqual(fields["Description"], "flutter-for-termux apt repository")
        self.assertTrue(fields["Date"], "Release carries no Date")

        self.assertEqual(set(hashes), set(_PINNED_INDEX_FILES))
        for relative, (digest, size) in hashes.items():
            data = (self._dists() / relative).read_bytes()
            self.assertEqual(digest, hashlib.sha256(data).hexdigest(), relative)
            self.assertEqual(size, str(len(data)), relative)

    def test_inrelease_is_a_valid_clearsign_of_release(self):
        inrelease = (self._dists() / "InRelease").read_text(encoding="utf-8")
        self.assertIn("-----BEGIN PGP SIGNED MESSAGE-----", inrelease)
        self.assertIn("Codename: termux", inrelease)

        verify_home = self.tmp / "verify-home"
        verify_home.mkdir(mode=0o700)
        env = {**os.environ, "GNUPGHOME": str(verify_home)}
        subprocess.run(
            ["gpg", "--batch", "--import", str(self.site / "key.gpg")],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["gpg", "--batch", "--verify", str(self._dists() / "InRelease")],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Good signature", result.stderr)

    def test_key_gpg_is_armored_export_of_signing_key(self):
        text = (self.site / "key.gpg").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----"), text[:60])

        show_home = self.tmp / "show-home"
        show_home.mkdir(mode=0o700)
        result = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", str(self.site / "key.gpg")],
            env={**os.environ, "GNUPGHOME": str(show_home)},
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(self.fingerprint, _primary_fingerprints(result.stdout))

    def test_index_html_contains_quick_start_and_no_version(self):
        page = (self.site / "index.html").read_text(encoding="utf-8")
        for needle in (
            "flutter-for-termux apt repository",
            # The canonical snippet must be runnable cold: install gpg, create
            # the keyrings dir, then add the key and repository.
            "pkg install -y gnupg",
            'mkdir -p "$PREFIX/etc/apt/keyrings"',
            "pkg install flutter",
            "key.gpg",
            "termux main",
            "https://github.com/GeneralKaos666/flutter-for-termux#quick-start",
        ):
            self.assertIn(needle, page)
        # Version-free by construction: autorelease.yml must never have to sed it.
        self.assertNotIn(self.config["tag"], page)
        self.assertNotIn(self.config["version"], page)

    def test_repo_cli_autodiscovers_the_stub_deb(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "apt-repo-work"
            work.mkdir()
            shutil.copyfile(self.deb, work / self.deb.name)
            out = Path(td) / "site"
            env = {
                **os.environ,
                "GNUPGHOME": str(self.gnupghome),
                "APT_REPO_SIGNING_PASSPHRASE": "",
            }
            # An absolute script path: the cwd is the throwaway work directory.
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(apt_repo.__file__).resolve()),
                    "repo",
                    "--out",
                    str(out),
                    "--key",
                    self.fingerprint,
                ],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            found = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
            expected = {
                "index.html",
                "key.gpg",
                "dists/termux/InRelease",
                "dists/termux/Release",
                "dists/termux/main/Contents-aarch64",
                "dists/termux/main/Contents-aarch64.xz",
                "dists/termux/main/binary-aarch64/Packages",
                "dists/termux/main/binary-aarch64/Packages.xz",
                f"dists/termux/main/binary-aarch64/{self.deb.name}",
            }
            self.assertEqual(found, expected)


class CiContractTest(unittest.TestCase):
    def test_ci_and_repo_config_cover_apt_repo_tooling(self):
        ci = Path(".github/workflows/ci.yml").read_text()
        assert "pytest test_build.py test_apt_repo.py" in ci
        assert "ruff check build.py package.py sysroot.py utils.py test_build.py scripts/ci/ scripts/release/ test_apt_repo.py" in ci
        assert 'bash -n scripts/release/stub/preinst scripts/release/stub/postinst scripts/release/stub/postrm' in ci
        assert "shellcheck --severity=error scripts/release/stub/preinst" in ci
        ini = Path("pytest.ini").read_text()
        assert "testpaths = test_build.py test_apt_repo.py" in ini
        check_repo = Path("scripts/ci/check_repo.py").read_text()
        assert '"test_apt_repo.py"' in check_repo
        gitignore = Path(".gitignore").read_text()
        assert "/apt-repo-work/" in gitignore and "/repo/" in gitignore


class PagesWorkflowTest(unittest.TestCase):
    def test_apt_repo_workflow_contract(self):
        wf = yaml.safe_load(Path(".github/workflows/apt-repo.yml").read_text())
        on = wf.get("on") or wf.get(True)      # YAML 1.1 parses bare `on` as True
        assert set(on) == {"workflow_run", "workflow_dispatch"}   # Review Focus 1:
        assert "release" not in on             # GITHUB_TOKEN suppresses release events
        assert on["workflow_run"] == {"workflows": ["build"], "types": ["completed"],
                                      "branches": ["main"]}
        build, deploy = wf["jobs"]["build"], wf["jobs"]["deploy"]
        steps = [s.get("uses") or s.get("name", "") for s in build["steps"]]
        joined = str(build["steps"])
        assert "Release guard" in joined                       # skips absent releases
        assert "apt_repo.py stub" in joined and "apt_repo.py repo" in joined
        assert any(u.startswith("actions/upload-pages-artifact@v5") for u in steps)
        assert build["steps"][-1]["with"]["path"] == "repo"
        assert deploy["needs"] == "build"
        assert "needs.build.outputs.publish" in str(deploy.get("if", ""))
        assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
        assert deploy["environment"]["name"] == "github-pages"
        assert any(u.startswith("actions/deploy-pages@v5")
                   for u in [s.get("uses", "") for s in deploy["steps"]])
        # Read the raw text for this one: repr() of the parsed steps quotes every
        # scalar, so the `key: value` pairing is only visible in the source GitHub
        # actually reads (the file is asserted verbatim elsewhere in the contract).
        assert ("APT_REPO_SIGNING_KEY: ${{ secrets.APT_REPO_SIGNING_KEY }}"
                in Path(".github/workflows/apt-repo.yml").read_text())


class DocsContractTest(unittest.TestCase):
    """The documented user-facing flow: apt first, installer deprecated (§6)."""

    def test_readme_quick_start_is_apt_first_and_version_free(self):
        readme = Path("README.md").read_text()
        # `## Manual install` is the transparency/fallback path, so it terminates
        # the quick start and is the sentinel for "still in the recommended flow".
        quick = readme.split("## Quick start")[1].split("## Manual install")[0]
        for needle in (
            "https://GeneralKaos666.github.io/flutter-for-termux",
            "key.gpg",
            "sources.list.d/flutter.list",
            "pkg install flutter",
        ):
            assert needle in quick
        # Version-free by construction: check_version_drift.py never sees it.
        assert re.search(r"\d+\.\d+\.\d+", quick) is None
        # The deprecated one-shot installer is gone: no legacy curl fallback.
        assert "install_flutter_complete.sh" not in readme
        assert "curl -sL https://raw.githubusercontent.com" not in readme

    def test_guides_document_apt_flow(self):
        install = Path("docs/guides/INSTALL_GUIDE.md").read_text()
        assert install.index("pkg install flutter") < install.index("Method 2")
        upgrade = Path("docs/guides/UPGRADE_GUIDE.md").read_text()
        assert "apt upgrade flutter" in upgrade
        assert "apt --fix-broken install" in upgrade
        agents = Path("AGENTS.md").read_text()
        assert "scripts/release/apt_repo.py" in agents
        cicd = Path("docs/CI_CD.md").read_text()
        assert "apt-repo.yml" in cicd and "APT_REPO_SIGNING_KEY" in cicd
        assert "APT_REPO_SIGNING_PASSPHRASE" in cicd
        assert "fingerprint" in cicd.lower()  # Task 1's value lands here
