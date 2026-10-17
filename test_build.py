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
    def _make_ndk(root, *, toolchain, source_properties, driver='runs'):
        ndk = root / 'ndk' / '29.0.14206865'
        (ndk / 'toolchains' / 'llvm').mkdir(parents=True)
        if driver is not None:
            drivers = ndk / 'toolchains' / 'llvm' / 'prebuilt' / 'linux-x86_64' / 'bin'
            drivers.mkdir(parents=True)
            executable = drivers / 'clang-21'
            # 'runs' stands in for the aarch64 repack's native driver; 'dead' for
            # Google's x86_64 tool, which exists and is executable but cannot
            # exec() on this device (the reporter's ENOEXEC).
            executable.write_text(
                '#!/bin/sh\nexit 0\n' if driver == 'runs' else '#!/bin/sh\nexit 1\n'
            )
            executable.chmod(0o755)
        if toolchain:
            cmake = ndk / 'build' / 'cmake'
            cmake.mkdir(parents=True)
            (cmake / 'android.toolchain.cmake').write_text('# toolchain\n')
        if source_properties:
            (ndk / 'source.properties').write_text('Pkg.Revision = 29.0.14206865\n')
        return ndk

    def _repair(self, ndk):
        funcs = '\n'.join([
            _bash_function(self.post_install, 'ndk_clang_runs'),
            _bash_function(self.post_install, 'repair_ndk_metadata'),
        ])
        return _run_bash(funcs, 'repair_ndk_metadata "$1"', str(ndk))

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

    def test_repair_removes_ndk_with_unrunnable_driver(self):
        """Google's x86_64 NDK: toolchain and metadata present, driver cannot exec.

        Accepting it makes ``post_install.sh`` report "configured" and every
        native build then die with
        ``.../prebuilt/bin/clang: 1: Syntax error: ")" unexpected``.
        """
        with tempfile.TemporaryDirectory() as tmp:
            ndk = self._make_ndk(
                Path(tmp), toolchain=True, source_properties=True, driver='dead'
            )
            result = self._repair(ndk)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(ndk.exists())

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

    def test_ndk_is_valid_rejects_unrunnable_driver(self):
        """An x86_64 NDK must not look installed, so it is re-provisioned (#29)."""
        valid = _bash_function(self.installer, 'ndk_is_valid')
        with tempfile.TemporaryDirectory() as tmp:
            dead = self._make_ndk(
                Path(tmp), toolchain=True, source_properties=True, driver='dead'
            )
            self.assertNotEqual(
                _run_bash(valid, 'ndk_is_valid "$1"', str(dead)).returncode, 0
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
                  stale_wrappers=False, driver_runs=True):
        real_name, alias = cls.LAYOUTS[layout]
        ndk = root / 'ndk' / '29.0.14206865'
        prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
        real = prebuilt / real_name
        (real / 'bin').mkdir(parents=True)
        driver_path = real / 'bin' / driver
        driver_path.write_text(
            '#!/bin/sh\n# real driver stand-in\n'
            if driver_runs else '#!/bin/sh\nexit 1\n'
        )
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
            _bash_function(self.post_install, 'ndk_clang_runs'),
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

    def test_refuses_unrunnable_driver(self):
        """Google's x86_64 NDK: must not be "configured" into a broken compiler."""
        with tempfile.TemporaryDirectory() as tmp:
            ndk, driver = self._make_ndk(Path(tmp), driver_runs=False)
            result = self._configure(ndk, tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            prebuilt = ndk / 'toolchains' / 'llvm' / 'prebuilt'
            link = prebuilt / 'bin' / 'clang'
            self.assertFalse(
                link.exists() or os.path.islink(link),
                'linked a driver that cannot run',
            )
            self.assertTrue(driver.exists(), 'the dead driver itself is untouched')


class SdkCmakeHostToolsTest(unittest.TestCase):
    """The SDK's own CMake/Ninja packages ship x86_64-only host tools.

    AGP downloads them itself, so an aarch64 device ends up with an ELF it cannot
    exec (``.../cmake/<v>/bin/ninja: 1: ELF...: not found``). ``setup_sdk_cmake``
    repoints each package's tools at the Termux-native ones and pre-creates the
    default ``3.22.1`` package AGP asks for.
    """

    def setUp(self):
        root = Path(__file__).parent
        self.post_install = (root / 'scripts' / 'install' / 'post_install.sh').read_text()

    def _run(self, tmp, *, packages):
        sdk = Path(tmp) / 'sdk'
        prefix = Path(tmp) / 'prefix'
        (prefix / 'bin').mkdir(parents=True)
        for tool in ('cmake', 'ninja', 'cpack', 'ctest'):
            native = prefix / 'bin' / tool
            native.write_text(f'#!/bin/sh\necho "{tool} version 9"\n')
            native.chmod(0o755)
        for version, tools in packages.items():
            bindir = sdk / 'cmake' / version / 'bin'
            bindir.mkdir(parents=True)
            for tool in tools:
                dead = bindir / tool
                dead.write_text('#!/bin/sh\nexit 1\n')
                dead.chmod(0o755)
        funcs = '\n'.join([
            _bash_function(self.post_install, 'backup_ndk_file'),
            _bash_function(self.post_install, 'setup_sdk_cmake'),
        ])
        call = ('export ANDROID_SDK="$1"; export PREFIX="$2"; export BACKUP_DIR="$3"; '
                'setup_sdk_cmake')
        result = _run_bash(funcs, call, str(sdk), str(prefix), str(Path(tmp) / 'backups'))
        return result, sdk, prefix

    def test_repoints_dead_package_tools_at_termux(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, sdk, prefix = self._run(tmp, packages={'4.0.0': ('cmake', 'ninja')})
            self.assertEqual(result.returncode, 0, result.stderr)
            for tool in ('cmake', 'ninja'):
                link = sdk / 'cmake' / '4.0.0' / 'bin' / tool
                self.assertTrue(link.is_symlink(), f'{link} not repointed')
                self.assertEqual(link.resolve(), (prefix / 'bin' / tool).resolve())

    def test_provisions_default_cmake_3_22_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, sdk, prefix = self._run(tmp, packages={})
            self.assertEqual(result.returncode, 0, result.stderr)
            for tool in ('cmake', 'ninja'):
                link = sdk / 'cmake' / '3.22.1' / 'bin' / tool
                self.assertTrue(link.is_symlink(), f'{link} not provisioned')
                self.assertEqual(link.resolve(), (prefix / 'bin' / tool).resolve())


class InstallerRollbackBackupTest(unittest.TestCase):
    """Regression for #29 (comment 6235200540): the rollback-backup gate aborted
    the installer on every Termux device that had the Flutter deb installed.

    ``dpkg-repack`` is not packaged for Termux, and the old
    ``apt-get download flutter=<ver>`` fallback can never succeed because these
    .debs are published on GitHub Releases, not in an apt repo. The gate
    therefore always fired ``[ERROR] Cannot backup ...`` → ``exit 35`` at step
    2/7 — before a single download — while the EXIT trap printed a summary
    claiming ``"preflight": "success"``. The backup must degrade (republish
    the release asset for the installed version, then warn-and-continue)
    instead of aborting the install.
    """

    def setUp(self):
        root = Path(__file__).parent
        self.installer = (root / 'install_flutter_complete.sh').read_text()
        self.lib_common = (root / 'scripts' / 'install' / 'lib_common.sh').read_text()

    # --- behavioural: attempt_package_backup ---------------------------------

    @staticmethod
    def _stub(bindir, name, body):
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)

    def _run_backup(self, tmp, *, pkg='flutter', old_ver='3.47.7-5', repack=False,
                    apt_ok=False, wget_ok=True):
        """Run ``attempt_package_backup`` against PATH-stubbed backup tools."""
        root = Path(tmp)
        stubs = root / 'stubs'
        stubs.mkdir()
        dest = root / 'backup'
        dest.mkdir()
        log = root / 'calls.log'
        # dpkg-repack is always stubbed (never silently resolved from the host
        # PATH): succeeding when repack=True, otherwise failing like a host
        # where the tool exists but cannot rebuild the package.
        repack_body = f'#!/bin/sh\necho "repack $1" >> "{log}"\ntouch "${{1}}_repacked.deb"\n'
        if not repack:
            repack_body = '#!/bin/sh\necho "E: no such package" >&2\nexit 1\n'
        self._stub(stubs, 'dpkg-repack', repack_body)
        self._stub(
            stubs, 'apt-get',
            f'#!/bin/sh\necho "apt $*" >> "{log}"\n'
            + ('touch "${2%%=*}.deb"\n' if apt_ok else 'echo "E: not found" >&2\nexit 1\n'),
        )
        wget_body = (
            f'#!/bin/sh\necho "wget $*" >> "{log}"\n'
            'out=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  case "$1" in\n'
            '    -O) out="$2"; shift 2 ;;\n'
            '    -q|--show-progress) shift ;;\n'
            '    *) shift ;;\n'
            '  esac\n'
            'done\n'
        )
        if wget_ok:
            wget_body += 'printf deb > "$out"\n'
        else:
            # A flaky network leaves a partial file behind at the -O target.
            wget_body += 'printf partial > "$out"\nexit 1\n'
        self._stub(stubs, 'wget', wget_body)
        env = dict(os.environ)
        env['PATH'] = f'{stubs}:{env["PATH"]}'
        funcs = '\n'.join([
            'RED=\'\' GREEN=\'\' YELLOW=\'\' BLUE=\'\' CYAN=\'\' NC=\'\'',
            _bash_function(self.installer, 'attempt_package_backup'),
        ])
        call = 'attempt_package_backup "$1" "$2" "$3" "$4"'
        return subprocess.run(
            ['bash', '-c', f'set -euo pipefail\n{funcs}\n{call}',
             'bash', pkg, old_ver, str(dest), 'https://example.invalid/asset.deb'],
            capture_output=True, text=True, env=env,
        )

    @staticmethod
    def _tool_calls(log):
        if not log.exists():
            return []
        return [line for line in log.read_text().splitlines() if line]

    def test_dpkg_repack_success_short_circuits_other_tiers(self):
        """Tier 1 wins: neither apt-get nor wget may be consulted afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_backup(tmp, repack=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = Path(tmp) / 'backup'
            self.assertTrue((dest / 'flutter_repacked.deb').exists())
            calls = '\n'.join(self._tool_calls(Path(tmp) / 'calls.log'))
            self.assertNotIn('wget', calls)
            self.assertNotIn('apt', calls)

    def test_release_asset_download_used_when_repack_and_apt_unavailable(self):
        """The reporter's environment: no dpkg-repack, flutter absent from apt.

        The published release asset for the *installed* version is the only
        remaining backup source, and it must be fetched, not the gate.
        """
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_backup(tmp, wget_ok=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = Path(tmp) / 'backup'
            self.assertTrue((dest / 'flutter_3.47.7-5_aarch64.deb').exists())
            calls = self._tool_calls(Path(tmp) / 'calls.log')
            # The apt tier must be consulted (and fail) before the release asset.
            self.assertEqual(len(calls), 2, calls)
            self.assertTrue(calls[0].startswith('apt download flutter=3.47.7-5'), calls)
            self.assertTrue(calls[1].startswith('wget'), calls)
            self.assertIn('https://example.invalid/asset.deb', calls[1])

    def test_apt_tier_success_short_circuits_release_download(self):
        """Tier 2 wins when the package genuinely comes from an apt repo."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_backup(tmp, apt_ok=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            dest = Path(tmp) / 'backup'
            self.assertTrue((dest / 'flutter.deb').exists())
            calls = self._tool_calls(Path(tmp) / 'calls.log')
            self.assertFalse(any(c.startswith('wget') for c in calls))

    def test_all_tiers_failing_fails_without_leaving_partial_deb(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_backup(tmp, wget_ok=False)
            self.assertEqual(result.returncode, 1, result.stderr)
            dest = Path(tmp) / 'backup'
            self.assertEqual(list(dest.glob('*.deb')), [])

    def test_empty_installed_version_skips_release_download(self):
        """No version means no derivable release asset; fail without wget."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_backup(tmp, old_ver='')
            self.assertEqual(result.returncode, 1, result.stderr)
            calls = self._tool_calls(Path(tmp) / 'calls.log')
            self.assertFalse(any(c.startswith('wget') for c in calls))

    # --- contract: the gate degrades instead of aborting ----------------------

    def test_hard_gate_removed_in_favour_of_warn_and_continue(self):
        self.assertNotIn('Cannot backup existing Flutter package', self.installer)
        self.assertNotIn('Cannot backup existing Android SDK package', self.installer)
        self.assertNotRegex(self.installer, r'(?m)^\s*exit 35\s*$')
        self.assertIn('continuing WITHOUT rollback', self.installer)
        # Both packages route through the shared three-tier helper.
        self.assertRegex(self.installer, r'attempt_package_backup flutter ')
        self.assertRegex(self.installer, r'attempt_package_backup android-sdk ')
        # Successful backups are recorded too, so the summary is unambiguous.
        self.assertIn('record_stage backup success', self.installer)

    def test_release_urls_are_derived_from_the_installed_version(self):
        """No version literals: the fallback URL must be derived at run time."""
        self.assertIn(
            'releases/download/${FLUTTER_OLD_VER%-*}/flutter_${FLUTTER_OLD_VER}_aarch64.deb',
            self.installer,
        )
        self.assertIn(
            '${ANDROID_SDK_DEB_URL%/download/*}/download/${ANDROID_SDK_OLD_VER}'
            '/android-sdk_${ANDROID_SDK_OLD_VER}_aarch64.deb',
            self.installer,
        )

    def test_backup_attempts_surface_tool_errors(self):
        """The original failure was invisible: stderr of every backup tool was
        discarded, hiding ``E: Unable to locate package dpkg-repack``."""
        helper = _bash_function(self.installer, 'attempt_package_backup')
        # The tool invocations themselves carry no redirect (an ``ls`` tidy-up
        # redirect is fine; silencing the tools is what hid the real failure).
        self.assertIn('dpkg-repack "$pkg") || true', helper)
        self.assertIn('apt-get download "${pkg}=${old_ver}") || true', helper)
        self.assertNotIn('dpkg-repack "$pkg" 2>/dev/null', helper)
        self.assertNotIn('download "${pkg}=${old_ver}" 2>/dev/null', helper)
        # The dpkg-repack bootstrap must first prove the package exists somewhere.
        self.assertIn('apt-cache show dpkg-repack', self.installer)
        self.assertNotIn('pkg install -y dpkg-repack 2>/dev/null', self.installer)

    # --- contract: the JSON summary must not claim success on a failed run ----

    def _run_cleanup(self, tmp, *, failing):
        funcs = '\n'.join([
            'RED=\'\' GREEN=\'\' YELLOW=\'\' BLUE=\'\' CYAN=\'\' NC=\'\'',
            'declare -A STAGE_STATUS',
            _bash_function(self.lib_common, 'record_stage'),
            _bash_function(self.lib_common, 'print_summary'),
            _bash_function(self.installer, 'cleanup_and_exit'),
            'WORK_DIR="$1"',
            'record_stage preflight success',
            'trap cleanup_and_exit EXIT',
        ])
        probe = 'false' if failing else 'true'
        result = subprocess.run(
            ['bash', '-c', f'set -euo pipefail\n{funcs}\n{probe}', 'bash', str(Path(tmp) / 'work')],
            capture_output=True, text=True,
        )
        return result, result.stdout[result.stdout.find('{'):]

    def test_cleanup_summary_reports_failed_run(self):
        """The reporter saw ``{"preflight": "success"}`` printed right after the
        installer aborted; the summary must carry the run outcome."""
        with tempfile.TemporaryDirectory() as tmp:
            result, summary = self._run_cleanup(tmp, failing=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('"run": "failed"', summary)

    def test_cleanup_summary_reports_successful_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, summary = self._run_cleanup(tmp, failing=False)
            self.assertEqual(result.returncode, 0)
            self.assertIn('"run": "success"', summary)


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

    def test_doc_hash_sync_leaves_unrelated_hex_on_sha_lines(self):
        sha = 'd' * 64
        unrelated = 'a' * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {
                'docs/guides/BUILD_GUIDE.md': (
                    '| SHA256 | `' + '0' * 64 + '` |\n'
                    'The sysroot SHA256 digest ' + unrelated + ' is not a release pin.\n'
                    '# output matches ' + unrelated + ' but is prose.\n'
                ),
            })
            self.sync.sync_pins(root, sha, 610124104, '3.47.7', '5')
            guide = (root / 'docs/guides/BUILD_GUIDE.md').read_text()
            self.assertIn('| SHA256 | `' + sha + '` |', guide)
            self.assertEqual(guide.count(unrelated), 2)

    def test_doc_hash_sync_refreshes_every_documented_pin_field(self):
        sha = 'd' * 64
        zero = '0' * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, {
                'docs/guides/BUILD_GUIDE.md': '| SHA256 | `' + zero + '` |\n',
                'docs/guides/INSTALL_GUIDE.md': (
                    '| SHA256 | `' + zero + '` |\n'
                    '# Confirm the output matches: ' + zero + '\n'
                ),
                'docs/releases/RELEASE_NOTES.md': '| SHA256 | `' + zero + '` |\n',
                'docs/CI_CD.md': (
                    'expected_sha256: ' + zero + '\n'
                    '  -ExpectedSha256 "' + zero + '"\n'
                ),
            })
            changed = self.sync.sync_pins(root, sha, 610124104, '3.47.7', '5')
            self.assertEqual(changed, [
                'docs/CI_CD.md',
                'docs/guides/BUILD_GUIDE.md',
                'docs/guides/INSTALL_GUIDE.md',
                'docs/releases/RELEASE_NOTES.md',
            ])
            for rel in changed:
                self.assertNotIn(zero, (root / rel).read_text(encoding='utf-8'))

    def test_rejects_malformed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                self.sync.sync_pins(root, 'not-a-hash', 610124104, '3.47.6', '1')
            with self.assertRaises(SystemExit):
                self.sync.sync_pins(root, 'd' * 64, 0, '3.47.6', '1')

    def test_doc_hash_fields_still_match_the_shipped_docs(self):
        """Every whitelisted pin field must match a real line in the shipped doc.

        A doc reformat that moves or renames one of these lines would silently
        stop the pin from being refreshed, and no other gate notices: build.toml
        carries no ``sha256``, so check_version_drift.py skips its guide SHA
        comparison. This reads the real files on purpose, so a reformat fails.
        """
        root = Path(__file__).parent
        self.assertLessEqual(
            set(self.sync.DOC_HASH_FIELDS),
            set(self.sync.DOC_FILES),
            'DOC_HASH_FIELDS keys must all be pin-synced DOC_FILES',
        )
        for rel, fields in self.sync.DOC_HASH_FIELDS.items():
            with self.subTest(file=rel):
                self.assertTrue(fields, f'{rel}: no pin field declared')
                text = (root / rel).read_text(encoding='utf-8')
                for pattern, _template in fields:
                    self.assertRegex(text, pattern, f'{rel}: pin field pattern matched no line')


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

    def _run_verifier(self, *, include_smoke, run_path='.github/workflows/build-deb.yml', run_artifacts=None, smoke_status='passed', manifest_text=None,
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
                payload_artifacts = (
                    [{'name': name, 'id': 7, 'expired': False}]
                    if run_artifacts is None
                    else run_artifacts
                )
                return _FakeHttpResponse(json.dumps({
                    'artifacts': payload_artifacts,
                }).encode())
            if '/actions/runs/' in url:
                return _FakeHttpResponse(json.dumps({
                    'head_sha': self.COMMIT,
                    'conclusion': 'success',
                    'path': run_path,
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

    def test_publisher_workflows_constant_covers_both_builders(self):
        # Production derives the accepted publishers from this constant; pin it
        # so dropping build.yml (the hosted sole publisher) fails loudly.
        self.assertEqual(
            self.verify.RELEASE_PUBLISHER_WORKFLOWS,
            ('.github/workflows/build.yml', '.github/workflows/build-deb.yml'),
        )
        self.assertEqual(
            self.verify.ARTIFACT_CROSSCHECK_WORKFLOW, '.github/workflows/build-deb.yml'
        )

    def test_build_yml_publisher_passes_without_matching_artifact(self):
        # build.yml attaches assets via softprops/action-gh-release and uploads
        # no flutter-termux-* artifact, so strict verification must accept it and
        # skip the byte-for-byte artifact cross-check instead of failing.
        code, out = self._run_verifier(
            include_smoke=False,
            run_path='.github/workflows/build.yml',
            run_artifacts=[{'name': 'engine-build-logs', 'id': 9, 'expired': False}],
        )

        self.assertEqual(code, 0)
        self.assertIn('on .github/workflows/build.yml', out)
        self.assertIn('published without a matching run artifact', out)
        self.assertNotIn('do not match expected release package patterns', out)

    def test_build_yml_publisher_cross_checks_when_artifact_present(self):
        # If the hosted publisher happens to expose a matching run artifact, the
        # byte-for-byte cross-check still runs: the branch keys off the artifact,
        # not the workflow path.
        code, out = self._run_verifier(
            include_smoke=False,
            run_path='.github/workflows/build.yml',
        )

        self.assertEqual(code, 0)
        self.assertIn('cryptographically matches release deb', out)

    def test_unknown_publisher_workflow_is_rejected(self):
        code, out = self._run_verifier(
            include_smoke=False,
            run_path='.github/workflows/evil.yml',
        )

        self.assertEqual(code, 1)
        self.assertIn('workflow path mismatch', out)
        self.assertIn('.github/workflows/build.yml', out)
        self.assertIn('.github/workflows/build-deb.yml', out)

    def test_build_deb_publisher_requires_matching_artifact(self):
        # build-deb.yml always uploads the deb artifact, so a missing match there
        # stays a hard failure rather than being silently downgraded.
        code, out = self._run_verifier(
            include_smoke=False,
            run_path='.github/workflows/build-deb.yml',
            run_artifacts=[{'name': 'engine-build-logs', 'id': 9, 'expired': False}],
        )

        self.assertEqual(code, 1)
        self.assertIn('do not match expected release package patterns', out)

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


class CiWorkflowContractTest(unittest.TestCase):
    """The CI workflow must actually run the checks this plan wires in."""

    def test_ci_runs_generate_versions_check(self):
        path = Path(__file__).parent / '.github' / 'workflows' / 'ci.yml'
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        runs = [
            step.get('run', '')
            for job in data['jobs'].values()
            for step in job.get('steps', [])
        ]
        self.assertTrue(
            any('generate_versions.py --check' in run for run in runs),
            'ci.yml has no generate_versions.py --check step',
        )


class VersionDriftCheckTest(unittest.TestCase):
    """Unit cases for scripts/ci/check_version_drift.py issue-template scanning.

    The package-version field is identified semantically (``id``/``label``), so
    a bare semver in an unrelated field is neither reported nor rewritten.
    """

    # Minimal build.toml/build.py so run_checks() reaches the template scan:
    # everything else in the fixture tree is deliberately absent.
    _BUILD_TOML = (
        "[flutter]\n"
        "tag = '3.47.7'\n"
        "dart_version = '3.13.5'\n"
        "\n"
        "[package]\n"
        "pkg_rel = '5'\n"
    )
    _BUILD_PY = (
        "class Build:\n"
        "    def sync(self):\n"
        "        self.version = self.dart_version\n"
    )
    _TEMPLATE = Path('.github') / 'ISSUE_TEMPLATE' / 'bug_report.yml'

    def setUp(self):
        self.drift = _load_ci_script('check_version_drift.py')

    @staticmethod
    def _field(field_id, label, placeholder, quote='"'):
        return (
            '- type: input\n'
            f'  id: {field_id}\n'
            '  attributes:\n'
            f'    label: {label}\n'
            f'    placeholder: {quote}{placeholder}{quote}\n'
        )

    def _version_field(self, placeholder, quote='"'):
        return self._field('package_version', 'Flutter package version', placeholder, quote)

    def _dart_field(self, placeholder):
        return self._field('dart_version', 'Bundled Dart SDK', placeholder)

    def _tree(self, tmp, body, name='bug_report.yml', with_template=True):
        root = Path(tmp)
        (root / 'build.toml').write_text(self._BUILD_TOML, encoding='utf-8')
        (root / 'build.py').write_text(self._BUILD_PY, encoding='utf-8')
        if with_template:
            template = root / '.github' / 'ISSUE_TEMPLATE' / name
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_text('body:\n' + body, encoding='utf-8')
        return root

    def test_stale_package_version_placeholder_is_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3'))
            errors = self.drift.run_checks(root)
        self.assertEqual(errors, [
            '.github/ISSUE_TEMPLATE/bug_report.yml: Flutter package version '
            "placeholder mismatch: found '3.47.7-3', expected '3.47.7-5'"
        ])

    def test_different_tag_in_version_field_is_drift(self):
        """A whole-tag change, not only a ``pkg_rel`` bump, is caught."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.40.0-9'))
            errors = self.drift.run_checks(root)
        self.assertEqual(errors, [
            '.github/ISSUE_TEMPLATE/bug_report.yml: Flutter package version '
            "placeholder mismatch: found '3.40.0-9', expected '3.47.7-5'"
        ])

    def test_different_tag_in_version_field_is_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.40.0-9'))
            cfg = self.drift.load_build_config(root)
            changed = self.drift.apply_version_autofix(cfg, root)
            self.assertEqual(changed, ['.github/ISSUE_TEMPLATE/bug_report.yml'])
            text = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertIn('placeholder: "3.47.7-5"', text)
            self.assertNotIn('3.40.0', text)
            self.assertEqual(self.drift.run_checks(root), [])

    def test_current_package_version_placeholder_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-5'))
            self.assertEqual(self.drift.run_checks(root), [])

    def test_unrelated_bare_semver_in_other_field_is_ignored(self):
        """The Dart SDK placeholder is a bare semver but not the version field."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._dart_field('3.13.5'))
            self.assertEqual(self.drift.run_checks(root), [])
            cfg = self.drift.load_build_config(root)
            self.assertEqual(self.drift.apply_version_autofix(cfg, root), [])
            text = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertIn('placeholder: "3.13.5"', text)

    def test_unrelated_bare_semver_is_untouched_while_version_field_is_fixed(self):
        body = self._version_field('3.47.7-3') + self._dart_field('3.13.5')
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, body)
            self.assertEqual(len(self.drift.run_checks(root)), 1)
            cfg = self.drift.load_build_config(root)
            self.assertEqual(
                self.drift.apply_version_autofix(cfg, root),
                ['.github/ISSUE_TEMPLATE/bug_report.yml'],
            )
            text = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertIn('placeholder: "3.47.7-5"', text)
            self.assertIn('placeholder: "3.13.5"', text)
            self.assertEqual(self.drift.run_checks(root), [])

    def test_non_version_placeholders_are_ignored(self):
        for placeholder in (
            '0.118.3, aarch64',
            'Pixel 7, Android 14',
            'flutter build apk --release',
            '3.47.7-3, built from source',
            'flutter --version',
        ):
            with self.subTest(placeholder=placeholder), tempfile.TemporaryDirectory() as tmp:
                root = self._tree(tmp, self._version_field(placeholder))
                self.assertEqual(self.drift.run_checks(root), [])

    def test_unquoted_version_placeholder_is_checked_and_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3', quote=''))
            errors = self.drift.run_checks(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("found '3.47.7-3', expected '3.47.7-5'", errors[0])
            cfg = self.drift.load_build_config(root)
            self.drift.apply_version_autofix(cfg, root)
            text = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertIn('placeholder: 3.47.7-5\n', text)
            self.assertEqual(self.drift.run_checks(root), [])

    def test_single_quoted_version_placeholder_keeps_its_quoting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3', quote="'"))
            cfg = self.drift.load_build_config(root)
            self.drift.apply_version_autofix(cfg, root)
            text = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertIn("placeholder: '3.47.7-5'", text)
            self.assertEqual(self.drift.run_checks(root), [])

    def test_autofix_rewrites_stale_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3'))
            cfg = self.drift.load_build_config(root)
            changed = self.drift.apply_version_autofix(cfg, root)
            self.assertEqual(changed, ['.github/ISSUE_TEMPLATE/bug_report.yml'])
            template = root / self._TEMPLATE
            self.assertIn('placeholder: "3.47.7-5"', template.read_text(encoding='utf-8'))
            self.assertEqual(self.drift.run_checks(root), [])

    def test_autofix_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3'))
            cfg = self.drift.load_build_config(root)
            self.assertEqual(
                self.drift.apply_version_autofix(cfg, root),
                ['.github/ISSUE_TEMPLATE/bug_report.yml'],
            )
            after_first = (root / self._TEMPLATE).read_text(encoding='utf-8')
            self.assertEqual(self.drift.apply_version_autofix(cfg, root), [])
            self.assertEqual((root / self._TEMPLATE).read_text(encoding='utf-8'), after_first)

    def test_yaml_extension_template_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3'), name='bug_report.yaml')
            self.assertEqual(len(self.drift.run_checks(root)), 1)
            cfg = self.drift.load_build_config(root)
            self.assertEqual(
                self.drift.apply_version_autofix(cfg, root),
                ['.github/ISSUE_TEMPLATE/bug_report.yaml'],
            )

    def test_missing_template_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, '', with_template=False)
            self.assertEqual(self.drift.issue_template_files(root), [])
            self.assertEqual(self.drift.run_checks(root), [])

    def test_malformed_template_yaml_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, self._version_field('3.47.7-3'))
            (root / self._TEMPLATE).write_text('body: [unclosed\n', encoding='utf-8')
            errors = self.drift.run_checks(root)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn('not valid YAML', errors[0])
            # --fix must not traceback or mangle the unparseable file.
            cfg = self.drift.load_build_config(root)
            self.assertEqual(self.drift.apply_version_autofix(cfg, root), [])
            self.assertEqual(
                (root / self._TEMPLATE).read_text(encoding='utf-8'), 'body: [unclosed\n'
            )

    def test_expected_package_version_requires_a_tag(self):
        self.assertEqual(self.drift.expected_package_version({'tag': '', 'pkg_rel': '5'}), '')
        self.assertEqual(self.drift.expected_package_version({'tag': '3.47.7', 'pkg_rel': '5'}), '3.47.7-5')
        self.assertEqual(self.drift.expected_package_version({'tag': '3.47.7'}), '3.47.7')

    def test_shipped_template_matches_build_toml(self):
        root = Path(__file__).parent
        cfg = self.drift.load_build_config(root)
        expected = self.drift.expected_package_version(cfg)
        text = (root / self._TEMPLATE).read_text(encoding='utf-8')
        field = next(
            item for item in yaml.safe_load(text)['body'] if item.get('id') == 'package_version'
        )
        self.assertEqual(field['attributes']['placeholder'], expected)
        self.assertEqual([e for e in self.drift.run_checks(root) if 'ISSUE_TEMPLATE' in e], [])


class CheckRepoNdkHelperDriftTest(unittest.TestCase):
    """Guard the two duplicated NDK backup/restore helpers (review finding 35).

    ``install_flutter_complete.sh`` and ``scripts/install/post_install.sh`` both
    ship a ``backup_ndk_file``/``restore_ndk_backups`` pair. They are
    intentionally *different* implementations (the installer variant maps both
    ``ANDROID_HOME`` and ``NDK_PATH`` and tracks absent preimages, while
    ``post_install.sh`` maps ``ANDROID_SDK`` only), so the guard cannot demand
    byte equality. It instead asserts the shared contract the two copies were
    forked from: both function definitions exist and both copies keep the
    ``ndk_backups/`` preimage directory plus the exact ``cp -a`` backup and
    restore commands.
    """

    def _real_copies(self):
        return {
            rel: (Path(__file__).parent / rel).read_text(encoding='utf-8')
            for rel in self.check_repo.NDK_HELPER_COPIES
        }

    def setUp(self):
        self.check_repo = _load_ci_script('check_repo.py')

    def test_real_copies_satisfy_the_shared_contract(self):
        self.assertEqual(self.check_repo.ndk_helper_drift_messages(self._real_copies()), [])

    def test_removing_each_required_function_from_each_copy_is_drift(self):
        for rel in self.check_repo.NDK_HELPER_COPIES:
            for name in self.check_repo.NDK_HELPER_FUNCTIONS:
                with self.subTest(copy=rel, function=name):
                    texts = self._real_copies()
                    texts[rel] = texts[rel].replace(
                        f'{name}() {{', f'{name}_renamed() {{'
                    )
                    messages = self.check_repo.ndk_helper_drift_messages(texts)
                    self.assertTrue(messages, f'no drift reported for {rel}:{name}')
                    self.assertTrue(
                        any(rel in message for message in messages), messages
                    )

    def test_breaking_each_invariant_in_each_copy_is_drift(self):
        for rel in self.check_repo.NDK_HELPER_COPIES:
            for invariant in self.check_repo.NDK_HELPER_INVARIANTS:
                with self.subTest(copy=rel, invariant=invariant):
                    texts = self._real_copies()
                    texts[rel] = texts[rel].replace(invariant, 'REPLACED-BY-TEST')
                    messages = self.check_repo.ndk_helper_drift_messages(texts)
                    self.assertTrue(messages, f'no drift reported for {rel}:{invariant}')
                    self.assertTrue(
                        any(rel in message for message in messages), messages
                    )


if __name__ == '__main__':
    unittest.main()
