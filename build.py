#!/usr/bin/env python3

import os
import sys
import git
import fire
import yaml
import utils
import shutil
import tomllib
import subprocess
from loguru import logger
from pathlib import Path
from sysroot import Sysroot
from package import Package


class GitProgress(git.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=''):
        logger.trace(f"cloning {cur_count}/{max_count} {message}")


def gn_list(values):
    return '[' + ', '.join(f'"{it}"' for it in values) + ']'


def ndk_vulkan_include(toolchain: str):
    return os.path.abspath(Path(toolchain, '..', '..', '..', 'sources', 'third_party', 'vulkan', 'include'))


def termux_stubs_dir():
    return os.path.abspath(Path(__file__).parent / 'stubs')


@utils.record
class Build:
    @utils.recordm
    def __init__(self, conf='build.toml'):
        path = Path(__file__).parent
        conf = path/conf

        with open(conf, 'rb') as f:
            cfg = tomllib.load(f)

        ndk = cfg['ndk'].get('path') or os.environ.get('ANDROID_NDK')
        api = cfg['ndk'].get('api')
        tag = cfg['flutter'].get('tag')
        repo = cfg['flutter'].get('repo')
        root = cfg['flutter'].get('path')
        arch = cfg['build'].get('arch')
        mode = cfg['build'].get('runtime')
        gclient = cfg['build'].get('gclient')
        sysroot = cfg['sysroot']
        syspath = sysroot.pop('path')
        package = cfg['package'].get('conf')
        release = cfg['package'].get('path')
        patches = cfg.get('patch')

        if not ndk:
            raise ValueError('neither ndk path nor ANDROID_NDK is set')
        if not tag:
            raise ValueError('require flutter tag')

        # TODO: check parameters
        self.tag = tag
        self.api = api or 26
        self.conf = conf
        # TODO: detect host
        self.host = 'linux-x86_64'
        self.repo = repo or 'https://github.com/flutter/flutter'
        self.arch = arch or 'arm64'
        self.mode = mode or 'debug'
        self.sysroot = Sysroot(path=path/syspath, **sysroot)
        self.root = path/root
        self.gclient = path/gclient
        self.release = path/release
        self.toolchain = Path(ndk, f'toolchains/llvm/prebuilt/{self.host}')

        if not self.release.parent.is_dir():
            raise ValueError(f'bad release path: "{release}"')

        with open(path/package, 'rb') as f:
            self.package = yaml.safe_load(f)

        if isinstance(patches, dict):
            self.patches = {}

            def patch(key):
                return lambda: self.patch(**self.patches[key])

            for k, v in patches.items():
                self.patches[k] = {
                    'file': path/v['file'],
                    'path': self.root/v['path']}
                self.__dict__[f'patch_{k}'] = patch(k)

    def config(self):
        info = (f'{k}\t: {v}' for k, v in self.__dict__.items() if k != 'package')
        logger.info('\n'+'\n'.join(info))

    def clone(self, *, url: str = None, tag: str = None, out: str = None):
        url = url or self.repo
        out = out or self.root
        tag = tag or self.tag
        progress = GitProgress()

        if utils.flutter_tag(out) == tag:
            logger.info('flutter exists, skip.')
            return
        elif os.path.isdir(out):
            logger.info(f'moving {out} to {out}.old ...')
            os.rename(out, f'{out}.old')
            return

        try:
            git.Repo.clone_from(
                url=url,
                to_path=out,
                progress=progress,
                branch=tag)
        except git.exc.GitCommandError:
            raise RuntimeError('\n'.join(progress.error_lines))

    def sync(self, *, cfg: str = None, root: str = None):
        cfg = cfg or self.gclient
        src = root or self.root

        shutil.copy(cfg, os.path.join(src, '.gclient'))
        cmd = ['gclient', 'sync', '-DR', '--no-history']
        subprocess.run(cmd, cwd=src, check=True, stdout=True, stderr=True)

    def patch(self, *, file, path):
       # Use git apply with 3-way fallback to survive small upstream shifts.
       cmd = ["git", "apply", "--3way", "--whitespace=nowarn", str(file)]
       subprocess.run(cmd, cwd=str(path), check=True)

    def configure(
        self,
        arch: str,
        mode: str,
        api: int = None,
        root: str = None,
        sysroot: str = None,
        toolchain: str = None,
    ):
        root = root or self.root
        api = self.api if api is None else api
        sysroot = os.path.abspath(sysroot or self.sysroot.path)
        toolchain = os.path.abspath(toolchain or self.toolchain)
        # Stub headers for platform-internal Android headers not shipped with
        # the public NDK (e.g. vk_android_native_buffer.h used by SwiftShader).
        stubs = termux_stubs_dir()
        vulkan = ndk_vulkan_include(toolchain)

        cmd = [
            'vpython3',
            'engine/src/flutter/tools/gn',
            '--linux',
            '--linux-cpu', arch,
            '--enable-fontconfig',
            '--no-goma',
            '--no-backtrace',
            '--clang',
            '--lto',
            '--no-enable-unittests',
            '--no-build-embedder-examples',
            '--no-prebuilt-dart-sdk',
            '--target-toolchain', toolchain,
            '--target-triple', utils.termux_target_triple(arch),
            '--runtime-mode', mode,
            '--no-build-glfw-shell',
            '--gn-args', 'symbol_level=0',
            '--gn-args', 'arm_use_neon=false',
            '--gn-args', 'arm_optionally_use_neon=true',
            '--gn-args', 'dart_include_wasm_opt=false',
            '--gn-args', 'dart_platform_sdk=false',
            '--gn-args', 'is_desktop_linux=false',
            '--gn-args', 'use_default_linux_sysroot=false',
            '--gn-args', 'dart_support_perfetto=false',
            '--gn-args', 'skia_use_perfetto=false',
            '--gn-args', f'custom_sysroot="{sysroot}"',
            '--gn-args', 'is_termux=true',
            '--gn-args', f'is_termux_host={utils.__TERMUX__}',
            '--gn-args', f'termux_api_level={api}',
            '--gn-args', 'extra_ldflags=["-lEGL", "-lGLESv2"]',
            # Provide stub headers for Android platform-internal APIs that are
            # not part of the public NDK (e.g. vk_android_native_buffer.h).
            # Also suppress -Wnewline-eof which fires on third-party headers
            # (SwiftShader) that legitimately lack a trailing newline.
            '--gn-args',
            f'extra_cflags={gn_list([f"-I{vulkan}", f"-I{stubs}"])}',
            '--gn-args',
            f'extra_cflags_cc={gn_list([f"-I{vulkan}", f"-I{stubs}", "-Wno-newline-eof"])}',
        ]
        subprocess.run(cmd, cwd=root, check=True, stdout=True, stderr=True)

    def build(self, arch: str, mode: str, root: str = None, jobs: int = None):
        root = root or self.root
        targets = [
            'flutter',
            'flutter/build/archives:artifacts',
            'flutter/build/archives:dart_sdk_archive',
            'flutter/build/archives:flutter_patched_sdk',
            'flutter/shell/platform/linux:flutter_gtk',
            'flutter/tools/font_subset',
        ]
        cmd = [
            'ninja', '-C', utils.target_output(root, arch, mode),
            *targets,
        ]
        if jobs:
            cmd.append(f'-j{jobs}')
        subprocess.run(cmd, check=True, stdout=True, stderr=True)

    def debuild(self, arch: str, output: str = None, root: str = None, **conf):
        conf = conf or self.package
        root = root or self.root
        output = output or self.output(arch)

        pkg = Package(root=root, arch=arch, **conf)
        pkg.debuild(output=output)

    def output(self, arch: str):
        if self.release.is_dir():
            name = f'flutter_{self.tag}_{utils.termux_arch(arch)}.deb'
            return self.release/name
        else:
            return self.release

    # TODO: check gclient and ninja existence
    def __call__(self, jobs: int = None):
        self.config()
        self.clone()
        self.sync()

        for arch in self.arch:
            self.sysroot(arch=arch)
            for mode in self.mode:
                self.configure(arch=arch, mode=mode)
                self.build(arch=arch, mode=mode, jobs=jobs)
            self.debuild(arch=arch, output=self.output(arch))


if __name__ == '__main__':
    logger.remove()
    logger.add(
        sys.stdout,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <9}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>")
        )
    fire.Fire(Build())
