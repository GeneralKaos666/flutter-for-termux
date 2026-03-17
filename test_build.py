import os
import re
import unittest
from unittest.mock import patch

import build


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
        self.assertIn(
            f'extra_cflags=["{vulkan}", "-I{stubs}", "-D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__"]',
            gn_args,
        )
        self.assertIn(
            f'extra_cflags_cc=["{vulkan}", "-I{stubs}", "-D__ANDROID_UNAVAILABLE_SYMBOLS_ARE_WEAK__", "-Wno-newline-eof"]',
            gn_args,
        )

    def test_default_build_modes_cover_packaged_variants(self):
        self.assertEqual(self.instance.mode, ['debug'])

    def test_engine_patch_contains_termux_build_fixes(self):
        patch_file = os.path.join(os.path.dirname(__file__), 'patches', 'engine.patch')
        with open(patch_file, encoding='utf-8') as f:
            patch_contents = f.read()

        self.assertIn('"-Wno-unknown-warning-option"', patch_contents)
        self.assertIn('"-llog"', patch_contents)
        self.assertIn('#if defined(__TERMUX__)', patch_contents)
        self.assertIn('diff --git a/engine/src/flutter/shell/platform/linux/fl_view_accessible.cc', patch_contents)
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


if __name__ == '__main__':
    unittest.main()
