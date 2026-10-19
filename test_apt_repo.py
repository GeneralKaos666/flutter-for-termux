"""Contract tests for the apt repository script renderer and stub maintainer scripts."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def _run(name, args, prefix, env=None):
    """Run the rendered ``name`` script with PREFIX/TMPDIR inside ``prefix``'s tempdir."""
    script = Path(prefix.parent) / f"{name}.rendered"
    script.write_text(_render()[name], encoding="utf-8")
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


def _seed(prefix):
    """Seed owned groups plus stale/foreign files a postrm must sort out."""
    for group in SPEC_GROUPS:
        path = prefix / group
        if group in FILE_GROUPS:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"owned: {group}\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "marker").write_text(f"owned: {group}\n", encoding="utf-8")
    (prefix / "opt" / ".flutter.old.flutter").write_text("stale\n", encoding="utf-8")
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
