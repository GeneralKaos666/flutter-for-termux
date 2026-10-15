"""Contract tests for the build pipeline.

All Flutter/Dart/NDK/Android expectations are derived from ``build.toml`` at
test time, so these tests survive a version bump without edits. On a Flutter
release bump (nightly ``autorelease.yml`` rewrites ``build.toml`` and syncs the
repo), the flutter-version-dependent tests below self-adjust; the patch/hunk
contract tests are intentionally tag-agnostic and must keep passing. If a
patch contract intentionally changes, update the assertion deliberately rather
than pinning a version literal.
"""

import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

import build
import package


def _load_ci_script(name):
    path = Path(__file__).parent / 'scripts' / 'ci' / name
    spec = importlib.util.spec_from_file_location(name.removesuffix('.py'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bash_function(script, name):
    """Extract a ``bash`` function definition from an installer script.

    Installer logic is exercised by pulling the function out and running it in a
    real ``bash`` (same approach as
    ``test_plugin_utils_patch_replaces_body_to_avoid_unreachable_smart_cast``),
    which keeps the shipped shell the source of truth instead of re-implementing
    it here.
    """
    func = re.search(rf'^{name}\(\) \{{.*?^\}}', script, re.M | re.DOTALL)
    if func is None:
        raise AssertionError(f'{name}() not found')
    return func.group(0)


def _run_bash(func, call, *args):
    return subprocess.run(
        ['bash', '-c', f'set -e\n{func}\n{call}', 'bash', *args],
        capture_output=True,
        text=True,
    )


class _FakeHttpResponse:
    """Minimal urllib response stand-in for the release verifier tests."""

    def __init__(self, payload):
        self._payload = payload

    def read(self, *args, **kwargs):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


_DEB_BYTES = b'flutter-termux-test-deb-payload'
_DEB_SHA = hashlib.sha256(_DEB_BYTES).hexdigest()
_DEB_SIZE = len(_DEB_BYTES)
# Minute-aligned epoch so a fixture .deb's tar timestamps round-trip through the
# module's timezone inference deterministically.
_DEB_MTIME = 1699999980


def _build_tar_gz(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for member_name, content in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = _DEB_MTIME
            info.uname = 'root'
            info.gname = 'root'
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _build_ar_archive(members):
    """Assemble a POSIX ``ar`` archive (the outer container of a .deb)."""
    out = bytearray(b'!<arch>\n')
    for member_name, data in members:
        header = (
            f'{member_name + "/":<16}'
            f'{0:<12}'
            f'{0:<6}'
            f'{0:<6}'
            f'{0o100644:<8o}'
            f'{len(data):<10}'
            '`\n'
        )
        assert len(header) == 60, len(header)
        out += header.encode('ascii')
        out += data
        if len(data) % 2:
            out += b'\n'
    return bytes(out)


def _build_deb(data_members):
    """Build a minimal, structurally valid .deb (ar + control.tar.gz + data.tar.gz)."""
    return _build_ar_archive([
        ('debian-binary', b'2.0\n'),
        ('control.tar.gz', _build_tar_gz([('control', b'Package: fixture\nVersion: 1.0\n')])),
        ('data.tar.gz', _build_tar_gz(data_members)),
    ])


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.android_ndk = os.environ.get('ANDROID_NDK')
        os.environ['ANDROID_NDK'] = '/tmp/android-ndk'
        self.instance = build.Build()

    def tearDown(self):
        if self.android_ndk is None:
            os.environ.pop('ANDROID_NDK', None)
        else:
            os.environ['ANDROID_NDK'] = self.android_ndk

    @patch('build.require_tools')
    @patch('build.subprocess.run')
    def test_build_requests_packaged_targets(self, run, _require_tools):
        self.instance.build(arch='arm64', mode='debug', jobs=42)

        cmd = run.call_args.args[0]

        self.assertIn('flutter', cmd)
        self.assertIn('flutter/build/archives:artifacts', cmd)
        self.assertIn('flutter/build/archives:dart_sdk_archive', cmd)
        self.assertIn('flutter/build/archives:flutter_patched_sdk', cmd)
        self.assertIn('flutter/shell/platform/linux:flutter_gtk', cmd)
        self.assertIn('flutter/tools/font_subset', cmd)
        self.assertIn('-j42', cmd)

    @patch('build.subprocess.run')
    def test_configure_uses_configured_api_and_termux_stub_includes(self, run):
        self.instance.api = 29

        self.instance.configure(arch='arm64', mode='debug')

        cmd = run.call_args.args[0]
        gn_args = [cmd[index + 1] for index, value in enumerate(cmd[:-1]) if value == '--gn-args']
        stubs = build.termux_stubs_dir()
        vulkan = f'-I{build.ndk_vulkan_include(self.instance.toolchain)}'

        self.assertIn('termux_api_level=29', gn_args)
        self.assertIn('extra_ldflags=["-lEGL", "-lGLESv2", "-llog"]', gn_args)
        self.assertNotIn('dart_support_perfetto=false', gn_args)
        self.assertIn(
            f'extra_cflags=["{vulkan}", "-I{stubs}", "-D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__"]',
            gn_args,
        )
        self.assertIn(
            f'extra_cflags_cc=["{vulkan}", "-I{stubs}", "-D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__", "-Wno-newline-eof"]',
            gn_args,
        )

    def test_default_build_modes_cover_packaged_variants(self):
        with open(self.instance.conf, 'rb') as f:
            runtime = tomllib.load(f)['build'].get('runtime')
        self.assertEqual(self.instance.mode, runtime or ['debug'])

    def test_output_uses_package_version_suffix(self):
        pkg_rel = str(self.instance.pkg_rel or '').strip()
        expected_version = f'{self.instance.tag}-{pkg_rel}' if pkg_rel else self.instance.tag
        output = Path(self.instance.output('arm64'))
        self.assertEqual(output.name, f'flutter_{expected_version}_aarch64.deb')
        # The deb must land under release/, not the repo root.
        self.assertEqual(output.parent.name, 'release')

    def test_metadata_bundles_release_config(self):
        with open(self.instance.conf, 'rb') as f:
            cfg = tomllib.load(f)
        meta = self.instance.metadata()
        self.assertEqual(meta['compile_sdk'], cfg['android']['compile_sdk'])
        self.assertEqual(meta['target_sdk'], cfg['android']['target_sdk'])
        self.assertEqual(meta['dart_version'], cfg['flutter'].get('dart_version') or '')
        self.assertEqual(meta['framework_revision'], cfg['flutter'].get('framework_revision') or '')
        self.assertEqual(meta['framework_commit_date'], cfg['flutter'].get('framework_commit_date') or '')
        self.assertEqual(meta['devtools_version'], cfg['flutter'].get('devtools_version') or '')
        self.assertEqual(meta['ndk_version'], cfg['ndk'].get('version') or '')
        self.assertEqual(meta['package_version'], self.instance.package_version)
        expected_keys = {
            'dart_version',
            'framework_revision',
            'framework_commit_date',
            'devtools_version',
            'ndk_version',
            'compile_sdk',
            'target_sdk',
            'package_version',
        }
        self.assertEqual(set(meta), expected_keys)

    def _conf_with_release_path(self, tmp, line):
        """Copy build.toml into ``tmp`` with the ``[package] path`` line rewritten.

        ``line`` is inserted verbatim (e.g. ``"path = '.'"``), or ``''`` drops
        the key entirely. Everything else is untouched, so only the release-path
        lookup differs from the shipped config.
        """
        lines = []
        in_package = False
        for raw in (Path(__file__).parent / 'build.toml').read_text(encoding='utf-8').splitlines():
            if raw.startswith('['):
                in_package = raw.strip() == '[package]'
            elif in_package and raw.startswith('path ='):
                if line:
                    lines.append(line)
                continue
            lines.append(raw)
        conf = Path(tmp) / 'build.toml'
        conf.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return conf

    def test_release_path_at_repo_root_is_rejected(self):
        """``path = '.'`` (the old default) would drop the deb into the source
        tree, so constructing the Build must fail loudly."""
        with tempfile.TemporaryDirectory() as tmp:
            conf = self._conf_with_release_path(tmp, "path = '.'")
            with self.assertRaises(ValueError) as ctx:
                build.Build(conf=str(conf))
        self.assertIn('bad release path', str(ctx.exception))

    def test_missing_release_path_is_rejected(self):
        """A missing or empty ``[package] path`` must raise ValueError, not the
        TypeError a later ``path/release`` division would produce."""
        for line in ('', "path = ''"):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                conf = self._conf_with_release_path(tmp, line)
                with self.assertRaises(ValueError) as ctx:
                    build.Build(conf=str(conf))
                self.assertIn('bad release path', str(ctx.exception))

    def test_dart_patches_are_kept_in_sync(self):
        patch_dir = os.path.join(os.path.dirname(__file__), 'patches')
        dart_path = os.path.join(patch_dir, 'dart.patch')
        new_path = os.path.join(patch_dir, 'dart.new.patch')
        # dart.new.patch is a symlink to dart.patch (approved); accept both
        # a real symlink and a plain duplicate for Windows-checkout compat.
        if os.path.islink(new_path):
            self.assertEqual(os.readlink(new_path), 'dart.patch')
        with open(dart_path, encoding='utf-8') as f:
            dart_patch = f.read()
        with open(new_path, encoding='utf-8') as f:
            dart_new_patch = f.read()

        self.assertEqual(dart_patch, dart_new_patch)
        self.assertIn(
            '+  ProcessResult result = Process.runSync("/data/data/com.termux/files/usr/bin/sh", [',
            dart_patch,
        )
        self.assertIn('+#if defined(DART_HOST_OS_ANDROID) && defined(__TERMUX__)', dart_patch)
        self.assertIn("+      return '/data/data/com.termux/files/usr/bin/sh';", dart_patch)

    def test_engine_patch_contains_termux_build_fixes(self):
        patch_file = os.path.join(os.path.dirname(__file__), 'patches', 'engine.patch')
        with open(patch_file, encoding='utf-8') as f:
            patch_contents = f.read()

        self.assertIn('"-Wno-unknown-warning-option"', patch_contents)
        self.assertIn('"-llog"', patch_contents)
        self.assertIn('+config("sdk") {', patch_contents)
        self.assertIn('+  if (current_toolchain == "//build/toolchain/termux:${current_cpu}") {', patch_contents)
        self.assertIn('+    cflags = []', patch_contents)
        self.assertIn('+      "-Wl,-rpath=/data/data/com.termux/files/usr/lib",', patch_contents)
        self.assertIn('+      "-llog",', patch_contents)
        self.assertIn('+    configs = [ "//build/config/linux:sdk" ]', patch_contents)
        self.assertIn(
            '+} else if (current_toolchain == default_toolchain && is_termux && custom_sysroot != "") {',
            patch_contents,
        )
        self.assertIn('#if defined(__TERMUX__)', patch_contents)
        self.assertIn('diff --git a/engine/src/flutter/shell/platform/linux/fl_view_accessible.cc', patch_contents)
        self.assertIn('+    if (defined(invoker.libs)) {', patch_contents)
        self.assertIn('+      libs += invoker.libs', patch_contents)
        self.assertIn('+      libs = [ "vk_swiftshader" ]', patch_contents)
        fl_view_accessible_hunk = re.search(
            r"\+\+\+ b/engine/src/flutter/shell/platform/linux/fl_view_accessible\.cc\n"
            r"@@ -\d+,\d+ \+\d+,\d+ @@\n"
            r"(?P<body>.*?)(?:\ndiff --git |\Z)",
            patch_contents,
            re.DOTALL,
        )
        self.assertIsNotNone(fl_view_accessible_hunk)
        fl_view_accessible_hunk_body = fl_view_accessible_hunk.group('body')
        self.assertIn('+#if defined(__TERMUX__)', fl_view_accessible_hunk_body)
        self.assertIn('+#include <atk/atk.h>', fl_view_accessible_hunk_body)
        self.assertIn('+#else', fl_view_accessible_hunk_body)
        self.assertIn(' extern "C" {', fl_view_accessible_hunk_body)
        self.assertIn('+#endif', fl_view_accessible_hunk_body)

        # Matches the added-file hunk for termux BUILD.gn and captures:
        # 1) declared added-line count in "@@ -0,0 +1,N @@" and 2) hunk body.
        termux_build_gn_hunk_pattern = (
            r"\+\+\+ b/engine/src/build/config/termux/BUILD\.gn\n"
            r"@@ -0,0 \+1,(\d+) @@\n"
            r"(?P<body>.*?)(?:\ndiff --git |\Z)"
        )
        hunk = re.search(
            termux_build_gn_hunk_pattern,
            patch_contents,
            re.DOTALL,
        )
        self.assertIsNotNone(hunk)
        hunk_line_count = int(hunk.group(1))
        added_lines = sum(1 for line in hunk.group('body').splitlines() if line.startswith('+'))
        self.assertEqual(added_lines, hunk_line_count)

    def test_plugin_utils_patch_replaces_body_to_avoid_unreachable_smart_cast(self):
        """Regression for #26's APK-build failure (Flutter 3.47.7).

        ``patch_plugin_utils`` used to inject a bare ``return`` above the first
        statement of ``forceNdkDownload``, leaving the rest of the function
        unreachable. Kotlin does not smart-cast in unreachable code, so the
        generated ``androidComponents.finalizeDsl { ... }`` failed to compile
        with "Only safe (?.) or non-null asserted (!!.) calls are allowed on a
        nullable receiver of type 'AndroidComponentsExtension<*, *, *>?'".
        The patch must now replace the whole body with a single return: same
        runtime behaviour (NDK is preinstalled on Termux), no dead code.
        """
        script = (
            Path(__file__).parent / 'scripts' / 'install' / 'post_install.sh'
        ).read_text()
        func = re.search(r'^patch_plugin_utils\(\) \{.*?^\}', script, re.M | re.DOTALL)
        self.assertIsNotNone(func)

        fixture = (
            'package com.flutter.gradle\n'
            '\n'
            '    @JvmStatic\n'
            '    @JvmName("forceNdkDownload")\n'
            '    internal fun forceNdkDownload(\n'
            '        gradleProject: Project,\n'
            '        flutterSdkRootPath: String\n'
            '    ) {\n'
            '        val gradleProjectAndroidExtension = getLegacyAndroidExtension(gradleProject)\n'
            '        val forcingNotRequired: Boolean =\n'
            '            gradleProjectAndroidExtension.externalNativeBuild.cmake.path != null\n'
            '        if (forcingNotRequired) {\n'
            '            return\n'
            '        }\n'
            '        val androidComponents =\n'
            '            gradleProject.extensions.findByType(AndroidComponentsExtension::class.java)\n'
            '        if (androidComponents == null) {\n'
            '            return\n'
            '        }\n'
            '        androidComponents.finalizeDsl { _ -> return@finalizeDsl }\n'
            '    }\n'
        )

        def run_patch(target: Path) -> None:
            subprocess.run(
                ['bash', '-c', f'set -e\n{func.group(0)}\npatch_plugin_utils "$1"', 'bash', str(target)],
                check=True,
            )

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'FlutterPluginUtils.kt.tmp'
            target.write_text(fixture)
            run_patch(target)
            patched = target.read_text()

            # signature and parameters survive so callers still link
            self.assertIn('internal fun forceNdkDownload(', patched)
            self.assertIn('gradleProject: Project,', patched)
            self.assertIn('flutterSdkRootPath: String', patched)
            # body is a single return; no unreachable smart-cast code remains
            self.assertIn('return // Termux: NDK provisioning disabled', patched)
            self.assertNotIn('val forcingNotRequired', patched)
            self.assertNotIn('androidComponents.finalizeDsl', patched)

            # idempotent: re-running the patch is a no-op
            run_patch(target)
            self.assertEqual(patched, target.read_text())


class NdkValidationTest(unittest.TestCase):
    """Regression for #29: an NDK that cannot configure a native build must not
    be accepted as healthy.

    A partially extracted NDK keeps ``toolchains/llvm`` but loses
    ``build/cmake/android.toolchain.cmake``. AGP only needs ``source.properties``,
    so minting that metadata onto an incomplete tree made the installer report
    "NDK installed" forever, and the toolchain file never came back. The reporter
    hit exactly that: after the metadata repair, the build failed with
    ``Could not find toolchain file: .../build/cmake/android.toolchain.cmake``.
    """

    def setUp(self):
        root = Path(__file__).parent
        self.post_install = (root / 'scripts' / 'install' / 'post_install.sh').read_text()
        self.installer = (root / 'install_flutter_complete.sh').read_text()

    @staticmethod
    def _make_ndk(root, *, toolchain, source_properties):
        ndk = root / 'ndk' / '29.0.14206865'
        (ndk / 'toolchains' / 'llvm').mkdir(parents=True)
        if toolchain:
            cmake = ndk / 'build' / 'cmake'
            cmake.mkdir(parents=True)
            (cmake / 'android.toolchain.cmake').write_text('# toolchain\n')
        if source_properties:
            (ndk / 'source.properties').write_text('Pkg.Revision = 29.0.14206865\n')
        return ndk

    def _repair(self, ndk):
        return _run_bash(
            _bash_function(self.post_install, 'repair_ndk_metadata'),
            'repair_ndk_metadata "$1"',
            str(ndk),
        )

    def test_repair_removes_ndk_missing_cmake_toolchain(self):
        """The #29 state: metadata already minted, toolchain file gone."""
        with tempfile.TemporaryDirectory() as tmp:
            ndk = self._make_ndk(Path(tmp), toolchain=False, source_properties=True)
            result = self._repair(ndk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ndk.exists())

    def test_repair_removes_ndk_missing_cmake_toolchain_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            ndk = self._make_ndk(Path(tmp), toolchain=False, source_properties=False)
            result = self._repair(ndk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ndk.exists())

    def test_repair_removes_stub_without_llvm(self):
        """Pre-existing behaviour kept: an empty stub is removed, not repaired."""
        with tempfile.TemporaryDirectory() as tmp:
            ndk = Path(tmp) / 'ndk' / '29.0.14206865'
            ndk.mkdir(parents=True)
            result = self._repair(ndk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ndk.exists())

    def test_repair_mints_metadata_for_complete_ndk(self):
        """Behaviour kept for a usable NDK: missing metadata is regenerated."""
        with tempfile.TemporaryDirectory() as tmp:
            ndk = self._make_ndk(Path(tmp), toolchain=True, source_properties=False)
            result = self._repair(ndk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                'Pkg.Revision = 29.0.14206865',
                (ndk / 'source.properties').read_text(),
            )

    def test_ndk_is_valid_requires_cmake_toolchain(self):
        valid = _bash_function(self.installer, 'ndk_is_valid')
        with tempfile.TemporaryDirectory() as tmp:
            incomplete = self._make_ndk(
                Path(tmp) / 'a', toolchain=False, source_properties=True
            )
            complete = self._make_ndk(
                Path(tmp) / 'b', toolchain=True, source_properties=True
            )
            self.assertNotEqual(
                _run_bash(valid, 'ndk_is_valid "$1"', str(incomplete)).returncode, 0
            )
            self.assertEqual(
                _run_bash(valid, 'ndk_is_valid "$1"', str(complete)).returncode, 0
            )


class NdkClangDriverTest(unittest.TestCase):
    """Regression for #29: ``setup_ndk_clang_wrappers`` must leave the NDK in a
    state CMake can actually use.

    Two independent bugs are fixed here:

    * The generic ``clang``/``clang++`` names were overwritten with a shell
      wrapper that added ``-L"$SYSROOT/usr/lib/<triple>"``. Only the static
      archives live in that directory (``libc.a``, ``libm.a``, ...); the shared
      stubs (``libc.so``, ...) are in the per-API subdirectories. Because that
      ``-L`` is searched before the API directory clang derives from
      ``--sysroot``, ``-lc`` resolved to the static ``libc.a`` and every native
      link failed with ``undefined symbol: pthread_atfork`` ("The C compiler ...
      is broken" / ``[CXX1429]``). A native CMake plugin (``jni``, pulled in by
      ``path_provider_android``) then could not build.
    * The host directory was assumed to be ``linux-x86_64``. The CMake toolchain
      derives ``ANDROID_HOST_TAG`` itself and it differs across repacks
      (``linux-aarch64`` for the current r29 tarball on Android, ``linux-arm64``
      for the earlier aarch64 repack), so a name that does not exist could be
      selected.
    """

    # real host dir -> host-tag alias shipped by that repack
    LAYOUTS = {
        'tarball-r29': ('linux-x86_64', 'linux-aarch64'),
        'older-arm64': ('linux-arm64', 'linux-x86_64'),
    }
    HOST_TAGS = ('linux-aarch64', 'linux-arm64', 'linux-x86_64')

    def setUp(self):
        root = Path(__file__).parent
        self.post_install = (root / 'scripts' / 'install' / 'post_install.sh').read_text()

    @classmethod
    def _make_ndk(cls, root, *, driver='clang-21', layout='tarball-r29',
                  stale_wrappers=False):
        real_name, alias = cls.LAYOUTS[layout]
        ndk = root / 'ndk' / '29.0.14206865'
        prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
        real = prebuilt / real_name
        (real / 'bin').mkdir(parents=True)
        driver_path = real / 'bin' / driver
        driver_path.write_text('#!/bin/sh\n# real driver stand-in\n')
        driver_path.chmod(0o755)
        # The repack's own alias for its host directory (and the sysroot it
        # serves), which must survive the installer.
        if alias != real_name:
            (prebuilt / alias).symlink_to(real_name)
        (real / 'sysroot').mkdir()
        (real / 'sysroot' / '.keep').write_text('')
        if stale_wrappers:
            # A pre-existing install: generic names are shell wrappers, and the
            # old installer left a bare `linux-aarch64` directory behind.
            where = [prebuilt / 'bin', real / 'bin', prebuilt / 'linux-aarch64' / 'bin']
            for d in where:
                d.mkdir(parents=True, exist_ok=True)
                for name in ('clang', 'clang++'):
                    wrapper = d / name
                    wrapper.write_text(
                        '#!/data/data/com.termux/files/usr/bin/sh\n'
                        'exec clang -L"$SYSROOT/usr/lib/aarch64-linux-android" "$@"\n'
                    )
                    wrapper.chmod(0o755)
        return ndk, driver_path

    def _configure(self, ndk, tmp):
        funcs = '\n'.join([
            _bash_function(self.post_install, 'backup_ndk_file'),
            _bash_function(self.post_install, 'setup_ndk_clang_wrappers'),
        ])
        call = (
            'export ANDROID_SDK="$2"; export BACKUP_DIR="$3"; export PREFIX="$4"; '
            'setup_ndk_clang_wrappers "$1"'
        )
        return _run_bash(
            funcs,
            call,
            str(ndk),
            str(ndk.parent),
            str(Path(tmp) / 'backups'),
            str(Path(tmp) / 'prefix'),
        )

    def _assert_usable(self, ndk, driver):
        """Every host tag CMake may pick must expose a real clang driver."""
        prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
        for tag in self.HOST_TAGS:
            for name in ('clang', 'clang++'):
                link = prebuilt / tag / 'bin' / name
                self.assertTrue(link.is_symlink(), f'{link} is not a symlink')
                self.assertEqual(link.resolve(), driver.resolve(), f'{link} not the driver')
                self.assertNotIn('-L"$SYSROOT', link.read_text())
        for name in ('clang', 'clang++'):
            link = prebuilt / 'bin' / name
            self.assertTrue(link.is_symlink(), f'{link} is not a symlink')
            self.assertEqual(link.resolve(), driver.resolve())
        self.assertTrue((prebuilt / 'sysroot').exists(), 'prebuilt/sysroot is broken')

    def test_tarball_layout_keeps_shipped_alias_and_sysroot(self):
        for stale in (False, True):
            with self.subTest(stale_wrappers=stale), tempfile.TemporaryDirectory() as tmp:
                ndk, driver = self._make_ndk(Path(tmp), stale_wrappers=stale)
                result = self._configure(ndk, tmp)
                self.assertEqual(result.returncode, 0, result.stderr)
                prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
                # The repack's linux-aarch64 -> linux-x86_64 alias must survive,
                # so the sysroot the toolchain resolves is not lost.
                self.assertTrue(os.path.islink(prebuilt / 'linux-aarch64'))
                self.assertEqual(os.readlink(prebuilt / 'linux-aarch64'), 'linux-x86_64')
                self.assertTrue((prebuilt / 'linux-aarch64' / 'sysroot').exists())
                self._assert_usable(ndk, driver)

    def test_older_layout_repairs_wrappers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ndk, driver = self._make_ndk(Path(tmp), layout='older-arm64',
                                         stale_wrappers=True)
            result = self._configure(ndk, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            # The stale linux-aarch64 directory becomes an alias to the real host.
            prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
            self.assertTrue(os.path.islink(prebuilt / 'linux-aarch64'))
            self.assertEqual(os.readlink(prebuilt / 'linux-aarch64'), 'linux-arm64')
            self._assert_usable(ndk, driver)

    def test_is_version_agnostic(self):
        """A different NDK release ships clang-18, clang-19, ... instead."""
        with tempfile.TemporaryDirectory() as tmp:
            ndk, driver = self._make_ndk(Path(tmp), driver='clang-18')
            result = self._configure(ndk, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self._assert_usable(ndk, driver)

    def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ndk, driver = self._make_ndk(Path(tmp))
            self.assertEqual(self._configure(ndk, tmp).returncode, 0)
            second = self._configure(ndk, tmp)
            self.assertEqual(second.returncode, 0, second.stderr)
            self._assert_usable(ndk, driver)


class PackageManifestTest(unittest.TestCase):
    def setUp(self):
        with open(Path(__file__).parent / 'build.toml', 'rb') as f:
            self.cfg = tomllib.load(f)

    def test_manifest_resource_generates_ship_manifest(self):
        flutter = self.cfg['flutter']
        android = self.cfg['android']
        ndk = self.cfg['ndk']

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            pkg = self._make_package(root)

            items = list(pkg.gen_resource('manifest'))

            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(
                str(item['out']), 'data/data/com.termux/files/usr/share/flutter/manifest.json'
            )
            self.assertEqual(item['mod'], 0o644)
            manifest = json.loads(item['src'].decode('utf-8'))
            self.assertEqual(
                manifest,
                {
                    'flutter_version': flutter['tag'],
                    'framework_revision': flutter['framework_revision'],
                    'framework_commit_date': flutter['framework_commit_date'],
                    'engine_revision': 'engine_revision_mock',
                    'dart_version': flutter['dart_version'],
                    'devtools_version': flutter['devtools_version'],
                    'ndk_version': ndk.get('version', ''),
                    'compile_sdk': android['compile_sdk'],
                    'target_sdk': android['target_sdk'],
                },
            )

    def test_post_install_resource_ships_config_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            pkg = self._make_package(root)

            items = list(pkg.gen_resource('post_install'))

        # The deb must ship exactly the documented on-device script, executable.
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(
            str(item['out']),
            'data/data/com.termux/files/usr/share/flutter/post_install.sh',
        )
        self.assertEqual(item['mod'], 0o755)
        expected = (
            Path(__file__).parent / 'scripts' / 'install' / 'post_install.sh'
        ).read_bytes()
        self.assertEqual(Path(item['src']).read_bytes(), expected)

    def test_package_exposes_render(self):
        from package import Package
        self.assertTrue(callable(getattr(Package, 'render', None)))

    def _make_package(self, root: Path):
        """Build a Package against a minimal temp flutter root.

        ``flutter_tag``/``engine_version`` are stubbed so no real checkout is
        needed, and the release output dir is created for you — callers need
        not ``mkdir`` it first."""
        flutter = self.cfg['flutter']
        android = self.cfg['android']
        ndk = self.cfg['ndk']
        (root / 'engine' / 'src' / 'out' / 'linux_release_arm64').mkdir(
            parents=True, exist_ok=True
        )
        with open(Path(__file__).parent / 'package.yaml', encoding='utf-8') as f:
            src = yaml.safe_load(f)
        with (
            patch('package.utils.flutter_tag', return_value=flutter['tag']),
            patch('package.utils.engine_version', return_value='engine_revision_mock'),
        ):
            return package.Package(
                root=str(root),
                arch='arm64',
                dart_version=flutter['dart_version'],
                framework_revision=flutter['framework_revision'],
                framework_commit_date=flutter['framework_commit_date'],
                devtools_version=flutter['devtools_version'],
                ndk_version=ndk.get('version', ''),
                compile_sdk=android['compile_sdk'],
                target_sdk=android['target_sdk'],
                **src,
            )

    def test_output_requires_an_existing_out_dir(self):
        """Preconditions that used to be `assert`s now raise ValueError so
        they survive `python -O`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            root.mkdir()
            with self.assertRaises(ValueError):
                package.Output(root, 'arm64')

    def test_package_rejects_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                package.Package(
                    root=str(Path(tmp, 'missing')),
                    arch='arm64',
                    control={},
                    resource={},
                )

    def test_sysroot_rejects_bad_path_and_source(self):
        import sysroot as sysroot_mod

        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = Path(tmp, 'not-a-dir')
            not_a_dir.write_text('x')
            with self.assertRaises(ValueError):
                sysroot_mod.Sysroot(path=str(not_a_dir))

            with self.assertRaises(ValueError):
                sysroot_mod.Sysroot(
                    path=str(Path(tmp, 'sysroot')),
                    broken={'repo': '', 'dist': 'd', 'pkgs': ['a']},
                )

    def test_resource_dispatcher_selects_identically(self):
        """One selector drives both the generator and the test paths: for the
        same input (str, list, falsy, mapping) they visit the same resources in
        the same order."""
        dispatcher = package.ResourceDispatcher(['a', 'b'])
        self.assertEqual(dispatcher.names('a'), ['a'])
        self.assertEqual(dispatcher.names(['b', 'a']), ['b', 'a'])
        self.assertEqual(dispatcher.names(None), ['a', 'b'])
        self.assertEqual(dispatcher.names([]), [])
        with self.assertRaises(ValueError):
            dispatcher.names({'x': 1})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            pkg = self._make_package(root)

            gen_calls, test_calls = [], []

            def fake_gen(self_, name):
                gen_calls.append(name)
                return []

            def fake_test(self_, name, dest_dir=None):
                test_calls.append(name)
                return None

            for selector in ('manifest', ['post_install', 'manifest'], None, []):
                del gen_calls[:]
                del test_calls[:]
                with (
                    patch.object(package.Package, 'gen_resource_internal', fake_gen),
                    patch.object(package.Package, 'test_resource_internal', fake_test),
                ):
                    list(pkg.gen_resource(selector))
                    list(pkg.test_resource(selector))

                self.assertEqual(gen_calls, test_calls)
                self.assertEqual(
                    gen_calls,
                    package.ResourceDispatcher(pkg.resource).names(selector),
                )

            with self.assertRaises(ValueError):
                list(pkg.gen_resource({'x': 1}))

            # Equal inputs must produce equal output: selecting by name, by a
            # one-element list, or repeating the call yields the same items.
            # ``artifacts`` carries a ``define:`` block, so repeating it also
            # proves generation never mutates the loaded YAML.
            for name in ('manifest', 'post_install', 'artifacts'):
                by_name = list(pkg.gen_resource(name))
                self.assertEqual(by_name, list(pkg.gen_resource([name])))
                self.assertEqual(by_name, list(pkg.gen_resource(name)))

    def test_package_ships_flutter_project_config(self):
        """README and termux_smoke.sh probe $PREFIX/share/flutter/
        flutter_project_config.sh, so the deb must ship that exact script
        (executable) or the documented configure flow is absent after install."""
        with open(Path(__file__).parent / 'package.yaml', encoding='utf-8') as f:
            src = yaml.safe_load(f)

        res = src['resource']
        self.assertIn('flutter_project_config', res)
        self.assertEqual(
            res['flutter_project_config']['source'],
            'scripts/install/flutter_project_config.sh',
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            pkg = self._make_package(root)

            items = list(pkg.gen_resource('flutter_project_config'))

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(
            str(item['out']),
            'data/data/com.termux/files/usr/share/flutter/flutter_project_config.sh',
        )
        self.assertEqual(item['mod'], 0o755)
        expected = (
            Path(__file__).parent / 'scripts' / 'install' / 'flutter_project_config.sh'
        ).read_bytes()
        self.assertEqual(Path(item['src']).read_bytes(), expected)

    def test_tar_ships_ancestor_dirs_for_file_outputs(self):
        """dpkg does not create missing parent dirs, so the data tar must
        carry explicit directory entries for share/flutter (issue #26)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            pkg = self._make_package(root)

            items = list(pkg.gen_resource(['manifest', 'post_install']))
            data_tar = Path(tmp, 'data.tar.xz')
            package.tar(data_tar, items)

            with tarfile.open(data_tar) as tar:
                members = tar.getmembers()

        names = [m.name for m in members]
        parents = (
            'data/data/com.termux/files/usr/share',
            'data/data/com.termux/files/usr/share/flutter',
        )
        for leaf in (
            'data/data/com.termux/files/usr/share/flutter/manifest.json',
            'data/data/com.termux/files/usr/share/flutter/post_install.sh',
        ):
            self.assertIn(leaf, names)
            leaf_index = names.index(leaf)
            for parent in parents:
                self.assertIn(parent, names, f'missing dir entry {parent} for {leaf}')
                self.assertTrue(members[names.index(parent)].isdir())
                self.assertLess(
                    names.index(parent),
                    leaf_index,
                    f'{parent} must precede {leaf}',
                )

    def test_control_version_uses_package_revision(self):
        flutter = self.cfg['flutter']
        package_cfg = self.cfg.get('package', {})
        pkg_rel = str(package_cfg.get('pkg_rel') or '').strip()
        package_version = f"{flutter['tag']}-{pkg_rel}" if pkg_rel else flutter['tag']

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, 'flutter')
            release_out = root / 'engine' / 'src' / 'out' / 'linux_release_arm64'
            release_out.mkdir(parents=True)

            with open(Path(__file__).parent / 'package.yaml', encoding='utf-8') as f:
                src = yaml.safe_load(f)

            with (
                patch('package.utils.flutter_tag', return_value=flutter['tag']),
                patch('package.utils.engine_version', return_value='engine_revision_mock'),
            ):
                pkg = package.Package(
                    root=str(root),
                    arch='arm64',
                    package_version=package_version,
                    **src,
                )

            control = pkg.gen_control()['src'].decode('utf-8')
            self.assertIn(f'Version: {package_version}', control)


class SafeEvalTest(unittest.TestCase):
    def test_legit_expressions_resolve(self):
        from package import safe_eval

        class Out:
            any = '/tmp/any'
            debug = '/tmp/debug'

        g = {'version': 'abc123', 'output': Out(), 'distro': '/opt/flutter'}
        self.assertEqual(safe_eval('"plain"', g), 'plain')
        self.assertEqual(safe_eval('f\'https://x/{version}\'', g), 'https://x/abc123')
        self.assertEqual(safe_eval('output.any', g), '/tmp/any')
        self.assertEqual(safe_eval('f\'{distro}/bin/cache\'', g), '/opt/flutter/bin/cache')

    def test_malicious_expressions_rejected(self):
        from package import safe_eval

        g = {'version': 'abc123'}
        for expr in (
            "__import__('os').system('id')",
            "open('/etc/passwd').read()",
            "(lambda: 1)()",
            "[x for x in range(3)]",
            "version.__class__",
        ):
            with self.assertRaises(ValueError, msg=expr):
                safe_eval(expr, g)


class RecordDecoratorTest(unittest.TestCase):
    def test_record_reraises_instead_of_exit(self):
        import utils

        @utils.recordm
        def boom():
            raise RuntimeError('kaput')

        with self.assertRaises(RuntimeError):
            boom()

    def test_engine_version_strips_trailing_newline(self):
        import utils

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'bin' / 'internal').mkdir(parents=True)
            (root / 'bin' / 'internal' / 'engine.version').write_text('b8800d0f\n')
            self.assertEqual(utils.engine_version(str(root)), 'b8800d0f')


class ReleaseNotesTest(unittest.TestCase):
    def setUp(self):
        self.extract = _load_ci_script('extract_release_notes.py')
        self.upstream = _load_ci_script('import_upstream_notes.py')

    def test_prefers_exact_tag_section(self):
        text = (
            '# Changelog\n\n'
            '## [Unreleased]\n\n- wip\n\n'
            '## [3.47.6-termux] - 2026-10-04\n\n- tagged notes\n'
        )
        self.assertEqual(self.extract.extract_notes('3.47.6', text), '- tagged notes')

    def test_plain_tag_section_beats_unreleased(self):
        text = '## [Unreleased]\n\n- wip\n\n## [3.47.6]\n\n- plain\n'
        self.assertEqual(self.extract.extract_notes('3.47.6', text), '- plain')

    def test_falls_back_to_unreleased(self):
        text = '## [Unreleased]\n\n- wip\n\n## [3.44.9-termux] - 2026-07-04\n\n- old\n'
        self.assertEqual(self.extract.extract_notes('3.47.6', text), '- wip')

    def test_empty_when_nothing_applies(self):
        text = '## [3.44.9-termux] - 2026-07-04\n\n- old\n'
        self.assertEqual(self.extract.extract_notes('3.47.6', text), '')

    def test_upstream_pointer_keyed_by_series(self):
        text = '## [Unreleased]\n\n- wip\n'
        new_text, outcome = self.upstream.import_pointer(
            '3.47.7', text, notes_available=True, today='2026-10-04'
        )
        self.assertEqual(outcome, 'added')
        self.assertIn('### Upstream Flutter 3.47', new_text)
        self.assertIn('release-notes-3.47.0', new_text)
        # A patch bump in the same series must not duplicate the entry.
        _, again = self.upstream.import_pointer(
            '3.47.7', new_text, notes_available=True, today='2026-10-04'
        )
        self.assertEqual(again, 'already-present')

    def test_upstream_pointer_records_unpublished_page(self):
        text = '## [Unreleased]\n\n- wip\n'
        new_text, outcome = self.upstream.import_pointer(
            '3.48.0', text, notes_available=False, today='2026-10-04'
        )
        self.assertEqual(outcome, 'added')
        self.assertIn('page not yet published', new_text)

    def test_real_changelog_resolves_current_tag(self):
        changelog = Path(__file__).parent / 'docs' / 'releases' / 'CHANGELOG.md'
        text = changelog.read_text(encoding='utf-8')
        with open(Path(__file__).parent / 'build.toml', 'rb') as f:
            tag = tomllib.load(f)['flutter']['tag']
        # Whatever history is current must yield something usable as a body.
        self.assertTrue(self.extract.extract_notes(tag, text))


class ReleasePinSyncTest(unittest.TestCase):
    def setUp(self):
        self.sync = _load_ci_script('sync_release_pins.py')

    def _tree(self, tmp, files):
        root = Path(tmp)
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return root

    def test_syncs_all_pin_formats(self):
        sha = 'd' * 64
        with tempfile.TemporaryDirectory() as tmp:
            zeros = '0' * 64
            root = self._tree(tmp, {
                'scripts/install/versions_common.sh': 'export EXPECTED_SHA256="' + zeros + '"\n',
                'scripts/install/lib_common.sh': 'export EXPECTED_SHA256="${EXPECTED_SHA256:-${FLUTTER_DEB_SHA256:-' + zeros + '}}"',
                'scripts/test/gh_e2e_test.sh': 'export EXPECTED_SHA256=${EXPECTED_SHA256:-${FLUTTER_DEB_SHA256:-' + zeros + '}}',
                'scripts/device/run_termux_smoke.ps1': '[string]$ExpectedSha256 = "' + zeros + '",',
                'docs/guides/BUILD_GUIDE.md': '| SHA256 | `' + zeros + '`\n\n617,009,288 bytes (about 588 MiB)\n',
            })
            changed = self.sync.sync_pins(root, sha, 610124104, '3.47.6', '1')
            self.assertEqual(len(changed), 5)
            self.assertIn('d' * 64, (root / 'scripts/install/versions_common.sh').read_text())
            guide = (root / 'docs/guides/BUILD_GUIDE.md').read_text()
            self.assertIn('610,124,104 bytes (about 582 MiB)', guide)
            # Second run is a no-op.
            self.assertEqual(self.sync.sync_pins(root, sha, 610124104, '3.47.6', '1'), [])

    def test_leaves_other_hashes_alone(self):
        other = 'a' * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {
                'scripts/install/versions_common.sh': (
                    'export EXPECTED_SHA256="' + '0' * 64 + '"\n'
                    'export ANDROID_SDK_EXPECTED_SHA256="' + other + '"\n'
                ),
            })
            self.sync.sync_pins(root, 'd' * 64, 610124104, '3.47.6', '1')
            text = (root / 'scripts/install/versions_common.sh').read_text()
            self.assertIn(other, text)

    def test_rejects_malformed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                self.sync.sync_pins(root, 'not-a-hash', 610124104, '3.47.6', '1')
            with self.assertRaises(SystemExit):
                self.sync.sync_pins(root, 'd' * 64, 0, '3.47.6', '1')


class ReleaseVerificationTest(unittest.TestCase):
    """Contract tests for scripts/ci/verify_release_asset.py strict verification.

    A ``.deb`` build publishes five required companions (checksum, size,
    inventory, build metadata, build evidence), while ``device_smoke_evidence.json``
    is only produced by the separate, manual ``device-smoke.yml`` workflow. Its
    absence must therefore not downgrade verification to the single-asset path,
    and when it is present it must still be validated fail-closed.
    """

    NAME = 'flutter_1.2.3-5_aarch64.deb'
    COMMIT = 'c' * 40
    TREE = 'd' * 40
    INVENTORY_TEXT = '\n'.join(
        f'-rw-r--r-- root/root {1000 + i} 2026-01-01 00:00 ./usr/lib/libflutter{i:02d}.so'
        for i in range(12)
    ) + '\n'

    def setUp(self):
        self.verify = _load_ci_script('verify_release_asset.py')
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.addCleanup(os.chdir, self._cwd)
        # Companion names are derived by production, never copied here.
        self.required_companions = self.verify.required_aux_asset_names(self.NAME)

    def _declared_manifest(self, sha=_DEB_SHA, size=_DEB_SIZE):
        return (
            '[flutter]\n'
            "tag = '1.2.3'\n"
            f"sha256 = '{sha}'\n"
            f'size = {size}\n'
            '[package]\n'
            "pkg_rel = '5'\n"
        )

    def _real_manifest_without_hash(self):
        """Production manifest shape with any sha256/size stripped.

        Rebuilt from the shipped build.toml so the tag, asset name, and package
        revision cannot drift from production. The hash/size keys (absent in
        production today) are removed defensively so this fixture always drives
        the manifest-silent strict path.
        """
        config = tomllib.loads((Path(__file__).parent / 'build.toml').read_text(encoding='utf-8'))
        flutter = {k: v for k, v in config['flutter'].items() if k not in ('sha256', 'size')}
        pkg_rel = config['package']['pkg_rel']
        lines = ['[flutter]']
        lines += [f'{key} = {json.dumps(value)}' for key, value in flutter.items()]
        lines += ['[package]', f'pkg_rel = {json.dumps(pkg_rel)}']
        return '\n'.join(lines) + '\n'

    def _real_deb(self):
        """Build a real fixture .deb and return ``(deb_bytes, matching_inventory)``.

        The inventory is generated by the module's own ``extract_deb_member_paths``
        so both the pass and mismatch cross-check cases exercise the real parser
        instead of a mocked return value.
        """
        deb_bytes = _build_deb([
            (f'./usr/lib/libflutter{i:02d}.so', b'x' * (1000 + i)) for i in range(12)
        ])
        deb_path = Path(self._tmp.name) / 'fixture.deb'
        deb_path.write_bytes(deb_bytes)
        entries = self.verify.extract_deb_member_paths(str(deb_path))
        return deb_bytes, '\n'.join(entries) + '\n'

    def _resolve_digest(self, api_digest, deb_sha):
        if api_digest is None or api_digest == 'absent':
            return ''
        if api_digest == 'match':
            return f'sha256:{deb_sha}'
        if api_digest == 'mismatch':
            return 'sha256:' + '0' * 64
        if isinstance(api_digest, str) and ':' in api_digest:
            return api_digest.lower()
        raise ValueError(
            f"unrecognized api_digest shorthand {api_digest!r}; pass None, 'match', "
            "'mismatch', or a literal '<algo>:<hex>' digest string"
        )

    def _run_verifier(self, *, include_smoke, smoke_status='passed', manifest_text=None,
                      api_digest='match', companion_sha=None, metadata_sha=None,
                      deb_bytes=None, download_bytes=None, inventory_text=None,
                      patch_extractor=True):
        manifest_text = self._declared_manifest() if manifest_text is None else manifest_text
        deb_bytes = _DEB_BYTES if deb_bytes is None else deb_bytes
        download_bytes = deb_bytes if download_bytes is None else download_bytes
        deb_sha = hashlib.sha256(deb_bytes).hexdigest()
        deb_size = len(deb_bytes)
        companion_sha = deb_sha if companion_sha is None else companion_sha
        metadata_sha = deb_sha if metadata_sha is None else metadata_sha
        inventory_text = self.INVENTORY_TEXT if inventory_text is None else inventory_text

        config = tomllib.loads(manifest_text)
        _, version, name = self.verify.resolve_expected_asset(
            config.get('flutter', {}), config.get('package', {})
        )

        Path('build.toml').write_text(manifest_text, encoding='utf-8')

        companion_names = list(self.verify.required_aux_asset_names(name))
        if include_smoke:
            companion_names.extend(self.verify.optional_aux_asset_names())
        digest = self._resolve_digest(api_digest, deb_sha)
        assets = [
            {
                'name': asset_name,
                'browser_download_url': f'https://example.test/{asset_name}',
                'size': deb_size if asset_name == name else 1,
                'digest': digest if asset_name == name else '',
            }
            for asset_name in [name, *companion_names]
        ]

        inventory_entries = self.verify.parse_inventory_entries(inventory_text)
        payloads = {
            '/releases/tags/': json.dumps({'assets': assets}).encode(),
            '.sha256': (companion_sha + '\n').encode(),
            '.size.txt': str(deb_size).encode(),
            'inventory.txt': inventory_text.encode(),
            'build_metadata.json': json.dumps({
                'version': version,
                'arch': 'aarch64',
                'run_id': 42,
                'build_number': 42,
                'source_commit': self.COMMIT,
                'tree_sha': self.TREE,
                'sha256': metadata_sha,
                'size_bytes': deb_size,
                'build_duration_seconds': 12.5,
            }).encode(),
            'build_evidence.json': json.dumps({
                'type': 'build_evidence',
                'version': version,
                'arch': 'aarch64',
                'run_id': 42,
                'build_number': 42,
                'source_commit': self.COMMIT,
                'tree_sha': self.TREE,
                'deb_sha256': deb_sha,
                'deb_size_bytes': deb_size,
                'inventory_file_count': len(inventory_entries),
                'build_duration_seconds': 12.5,
            }).encode(),
            'device_smoke_evidence.json': json.dumps({
                'status': smoke_status,
                'mode_a_status': 'passed',
                'mode_b_status': 'passed',
                'artifact_source_commit': self.COMMIT,
                'verifier_commit': self.COMMIT,
                'build_run_id': 42,
                'artifacts': {
                    'deb_sha256': deb_sha,
                    'deb_size': deb_size,
                    'apk_sha256': 'a' * 64,
                    'apk_size': 1024,
                    'aab_sha256': 'b' * 64,
                    'aab_size': 2048,
                },
            }).encode(),
        }

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as archive:
            archive.writestr('flutter_aarch64.deb', deb_bytes)
        zip_bytes = zip_buffer.getvalue()

        def fake_urlopen(req, *args, **kwargs):
            url = getattr(req, 'full_url', str(req))
            if '/git/commits/' in url:
                return _FakeHttpResponse(json.dumps({'tree': {'sha': self.TREE}}).encode())
            if '/compare/' in url:
                return _FakeHttpResponse(json.dumps({
                    'behind_by': 0, 'ahead_by': 0, 'status': 'identical', 'files': [],
                }).encode())
            if url.endswith('/zip'):
                return _FakeHttpResponse(zip_bytes)
            if url.endswith('/artifacts'):
                return _FakeHttpResponse(json.dumps({
                    'artifacts': [{'name': name, 'id': 7, 'expired': False}],
                }).encode())
            if '/actions/runs/' in url:
                return _FakeHttpResponse(json.dumps({
                    'head_sha': self.COMMIT,
                    'conclusion': 'success',
                    'path': '.github/workflows/build-deb.yml',
                    'run_number': 42,
                }).encode())
            for marker, payload in payloads.items():
                if marker in url:
                    return _FakeHttpResponse(payload)
            raise AssertionError(f'unexpected url: {url}')

        def fake_urlretrieve(url, path):
            Path(path).write_bytes(download_bytes)
            return str(path), None

        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'RUNNER_TEMP': self._tmp.name}, clear=True))
            stack.enter_context(patch.object(self.verify.urllib.request, 'urlopen', fake_urlopen))
            stack.enter_context(patch.object(self.verify.urllib.request, 'urlretrieve', fake_urlretrieve))
            if patch_extractor:
                stack.enter_context(patch.object(
                    self.verify,
                    'extract_deb_member_paths',
                    return_value=inventory_entries,
                ))
            stack.enter_context(redirect_stdout(out))
            try:
                self.verify.main()
            except SystemExit as exc:
                code = exc.code if exc.code is not None else 0
            else:
                code = 0
        return code, out.getvalue()

    def test_build_companions_alone_reach_strict_verification(self):
        code, out = self._run_verifier(include_smoke=False)

        self.assertEqual(code, 0)
        self.assertNotIn('single-asset', out)
        for name in self.required_companions:
            self.assertIn(f'Found companion asset: {name}', out)
        self.assertIn('device_smoke_evidence.json absent', out)

    def test_harness_uses_production_asset_and_companion_derivation(self):
        # Guard against the harness re-implementing production naming: the
        # exported helpers must derive the declared fixture's names.
        _, version, name = self.verify.resolve_expected_asset({'tag': '1.2.3'}, {'pkg_rel': '5'})
        self.assertEqual(version, '1.2.3-5')
        self.assertEqual(name, self.NAME)
        self.assertEqual(
            self.verify.required_aux_asset_names(name),
            (f'{self.NAME}.sha256', f'{self.NAME}.size.txt', 'inventory.txt',
             'build_metadata.json', 'build_evidence.json'),
        )
        self.assertEqual(self.verify.optional_aux_asset_names(), ('device_smoke_evidence.json',))

    def test_smoke_evidence_is_validated_when_present(self):
        code, out = self._run_verifier(include_smoke=True)

        self.assertEqual(code, 0)
        self.assertIn('Verified device_smoke_evidence.json full schema', out)

    def test_failing_smoke_evidence_is_rejected_when_present(self):
        code, out = self._run_verifier(include_smoke=True, smoke_status='failed')

        self.assertEqual(code, 1)
        self.assertIn("device_smoke_evidence.json status is not 'passed'", out)

    def test_manifest_without_sha256_cross_checks_present_sources(self):
        # Production build.toml declares no sha256/size, and GitHub may omit the
        # asset digest; the required companion .sha256 and build_metadata.json
        # must still agree and the strict branch must not crash.
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            api_digest='absent',
        )

        self.assertEqual(code, 0)
        self.assertNotIn('single-asset', out)
        self.assertIn('Manifest declares no sha256; cross-checking', out)
        self.assertIn('platform attestation unavailable', out)
        self.assertNotIn('GitHub digest mismatch', out)

    def test_manifest_without_sha256_accepts_matching_api_digest(self):
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            api_digest='match',
        )

        self.assertEqual(code, 0)
        self.assertIn('Hash sources agree (companion .sha256, build_metadata.json, GitHub digest)', out)

    def test_manifest_without_sha256_rejects_mismatched_companion_hash(self):
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            companion_sha='0' * 64,
            metadata_sha=_DEB_SHA,
        )

        self.assertEqual(code, 1)
        self.assertIn('Hash source mismatch! companion .sha256', out)

    def test_manifest_without_sha256_rejects_mismatched_api_digest(self):
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            api_digest='mismatch',
        )

        self.assertEqual(code, 1)
        self.assertIn('Hash source mismatch! GitHub digest', out)

    def test_manifest_without_sha256_ignores_non_sha256_digest(self):
        # A present-but-non-sha256 digest must be ignored explicitly, not
        # reported as absent, and must not fail verification on its own.
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            api_digest='md5:' + '0' * 32,
        )

        self.assertEqual(code, 0)
        self.assertIn('is not a sha256 digest and will be ignored', out)
        self.assertNotIn('GitHub digest absent', out)

    def test_manifest_without_sha256_rejects_malformed_companion_hash(self):
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            companion_sha='not-a-valid-sha256',
        )

        self.assertEqual(code, 1)
        self.assertIn('is not a valid 64-char sha256', out)

    def test_manifest_without_sha256_rejects_malformed_metadata_hash(self):
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            metadata_sha='zz',
        )

        self.assertEqual(code, 1)
        self.assertIn("build_metadata.json sha256 'zz' is not a valid 64-char hex hash", out)

    def test_manifest_without_sha256_rejects_failed_smoke_evidence(self):
        code, out = self._run_verifier(
            include_smoke=True,
            smoke_status='failed',
            manifest_text=self._real_manifest_without_hash(),
        )

        self.assertEqual(code, 1)
        self.assertIn("device_smoke_evidence.json status is not 'passed'", out)

    def test_manifest_without_sha256_resolved_hash_gates_final_download(self):
        # Correct companion/metadata hashes but tampered download bytes must fail
        # the final local SHA256 check against the resolved hash.
        corrupt = bytearray(_DEB_BYTES)
        corrupt[0] ^= 0xFF
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            download_bytes=bytes(corrupt),
        )

        self.assertEqual(code, 1)
        self.assertIn('Local SHA256 mismatch', out)

    def test_harness_rejects_unknown_api_digest_shorthand(self):
        with self.assertRaisesRegex(ValueError, 'unrecognized api_digest'):
            self._run_verifier(include_smoke=False, api_digest='bogus')

    def test_real_deb_inventory_cross_check_passes(self):
        deb_bytes, inventory_text = self._real_deb()
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            deb_bytes=deb_bytes,
            inventory_text=inventory_text,
            patch_extractor=False,
        )

        self.assertEqual(code, 0)
        self.assertIn('Inventory perfectly matches package contents', out)

    def test_real_deb_inventory_cross_check_rejects_mismatch(self):
        deb_bytes, inventory_text = self._real_deb()
        # A format-valid but semantically wrong inventory entry must be rejected.
        tampered = inventory_text.replace('libflutter00.so', 'libflutter99.so', 1)
        code, out = self._run_verifier(
            include_smoke=False,
            manifest_text=self._real_manifest_without_hash(),
            deb_bytes=deb_bytes,
            inventory_text=tampered,
            patch_extractor=False,
        )

        self.assertEqual(code, 1)
        self.assertIn('Semantic mismatch between inventory.txt', out)


class SysrootLockTest(unittest.TestCase):
    def test_write_lock_schema(self):
        import sysroot

        metas = [{
            'name': 'glib',
            'version': '2.0',
            'url': 'https://example.com/glib.deb',
            'sha256': '0' * 64,
            'size': 123,
            'archive_path': 'pool/glib.deb',
            'repo': 'https://example.com/',
            'dist': 'stable',
        }]
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp, 'sysroot.lock.json')
            sysroot._write_lock(lock, 'aarch64', metas, 'deadbeef')
            data = json.loads(lock.read_text(encoding='utf-8'))
            for key in ('aarch64', 'arm64'):
                self.assertIn(key, data)
                entry = data[key]
                for req in ('arch', 'created_at', 'tree_hash', 'packages'):
                    self.assertIn(req, entry)
                pkg = entry['packages']['glib']
                for field in ('name', 'version', 'url', 'sha256', 'size', 'archive_path', 'repo', 'dist'):
                    self.assertIn(field, pkg)


if __name__ == '__main__':
    unittest.main()
