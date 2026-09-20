"""Contract tests for the build pipeline.

All Flutter/Dart/NDK/Android expectations are derived from ``build.toml`` at
test time, so these tests survive a version bump without edits. On a Flutter
release bump (nightly ``autorelease.yml`` rewrites ``build.toml`` and syncs the
repo), the flutter-version-dependent tests below self-adjust; the patch/hunk
contract tests are intentionally tag-agnostic and must keep passing. If a
patch contract intentionally changes, update the assertion deliberately rather
than pinning a version literal.
"""

import json
import os
import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import build
import package


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

    @patch('build.subprocess.run')
    def test_build_requests_packaged_targets(self, run):
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
        output_name = Path(self.instance.output('arm64')).name
        self.assertEqual(output_name, f'flutter_{expected_version}_aarch64.deb')

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
                    dart_version=flutter['dart_version'],
                    framework_revision=flutter['framework_revision'],
                    framework_commit_date=flutter['framework_commit_date'],
                    devtools_version=flutter['devtools_version'],
                    ndk_version=ndk.get('version', ''),
                    compile_sdk=android['compile_sdk'],
                    target_sdk=android['target_sdk'],
                    **src,
                )

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
