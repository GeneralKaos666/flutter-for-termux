import os
import unittest
from pathlib import Path
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
        stubs = os.path.abspath(Path(build.__file__).parent / 'stubs')
        vulkan = f'-I{build.ndk_vulkan_include(self.instance.toolchain)}'

        self.assertIn('termux_api_level=29', gn_args)
        self.assertIn(f'extra_cflags=["{vulkan}", "-I{stubs}"]', gn_args)
        self.assertIn(
            f'extra_cflags_cc=["{vulkan}", "-I{stubs}", "-Wno-newline-eof"]',
            gn_args,
        )

    def test_default_build_modes_cover_packaged_variants(self):
        self.assertEqual(self.instance.mode, ['debug', 'release', 'profile'])


if __name__ == '__main__':
    unittest.main()
