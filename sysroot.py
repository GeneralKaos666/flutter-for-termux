#!/usr/bin/env python3

import asyncio
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.parse

import aiohttp
from loguru import logger

import utils

MAX_CONCURRENT_DOWNLOADS = 8
DOWNLOAD_RETRIES = 3


async def _download(sess, url, dst, sem):
    path = urllib.parse.urlparse(url).path
    name = pathlib.Path(path).name
    path = pathlib.Path(dst, name)
    last_error = None
    async with sem:
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            try:
                async with sess.get(url) as resp:
                    resp.raise_for_status()
                    sha256 = hashlib.sha256()
                    size = 0
                    with open(path, 'wb') as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            if not chunk:
                                continue
                            sha256.update(chunk)
                            size += len(chunk)
                            f.write(chunk)
                    return {
                        'path': path,
                        'sha256': sha256.hexdigest(),
                        'size': size,
                    }
            except Exception as e:
                last_error = e
                logger.warning(f'download attempt {attempt}/{DOWNLOAD_RETRIES} failed: {name}: {e}')
                if attempt < DOWNLOAD_RETRIES:
                    await asyncio.sleep(2 ** attempt)
    raise RuntimeError(f'✗ download failed after {DOWNLOAD_RETRIES} attempts: {name}: {last_error}')


async def _spawn(tasks):
    if not tasks:
        return []
    # asyncio.gather preserves input order (unlike wait's done-set),
    # so callers can zip inputs ↔ results safely. First exception propagates.
    return list(await asyncio.gather(*tasks))


async def _download_packages(out, arch, *src):
    timeout = aiohttp.ClientTimeout(total=500)
    sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        resolved = await _spawn([
            _resolve_packages(sess, arch, **it) for it in src
        ])
        # resolved: list of dicts name -> metadata; flatten preserving repo/dist
        metas = []
        for meta in resolved:
            metas.extend(meta.values())
        downloads = await _spawn([
            _download(sess, m['url'], out, sem) for m in metas
        ])
        for m, d in zip(metas, downloads):
            m['archive_path'] = urllib.parse.urlparse(m['url']).path.lstrip('/')
            m['sha256_download'] = d['sha256']
            m['size_download'] = d['size']
            m['local_path'] = str(d['path'])
            # Prefer server-advertised size/sha when present, else download-observed
            m.setdefault('size', d['size'])
            if not m.get('sha256'):
                m['sha256'] = d['sha256']
        return metas


async def _resolve_packages(sess, arch, repo, dist, pkgs):
    if not repo or not pkgs:
        return {}

    bin = f'dists/{dist}/main/binary-{arch}/Packages'
    url = urllib.parse.urljoin(repo, bin)

    current = None
    entry = {}
    package = {}

    async with sess.get(url) as resp:
        resp.raise_for_status()
        async for line in resp.content:
            line = line.decode().strip()

            if not line:
                if current in pkgs and entry.get('Filename'):
                    urlpath = entry['Filename']
                    package[current] = {
                        'name': current,
                        'version': entry.get('Version', ''),
                        'url': urllib.parse.urljoin(repo, urlpath),
                        'archive_path': urlpath,
                        'size': int(entry.get('Size', 0) or 0),
                        'sha256': entry.get('SHA256', ''),
                        'repo': repo,
                        'dist': dist,
                    }
                current = None
                entry = {}
                if len(package) == len(pkgs):
                    break
                continue
            if line.startswith('Package:'):
                current = line.split(':', 1)[1].strip()
                entry = {}
            elif current in pkgs and ':' in line:
                key, _, value = line.partition(':')
                entry[key.strip()] = value.strip()

            if len(package) == len(pkgs):
                break

    remains = [it for it in pkgs if it not in package]
    if remains:
        raise FileNotFoundError(f'packages{remains} not found.')
    return package


def _extract(out, deb):
    subprocess.run(['dpkg', '-x', str(deb), str(out)], check=True)
    logger.info(f'✓ installed {pathlib.Path(deb).name}')


def _tree_hash(out):
    """Best-effort content hash of the extracted sysroot (mtime/size/name)."""
    h = hashlib.sha256()
    for root, _, files in os.walk(out):
        for name in sorted(files):
            p = pathlib.Path(root, name)
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(f'{p.relative_to(out)}:{st.st_size}:{st.st_mtime_ns}\n'.encode())
    return h.hexdigest()


def _write_lock(lock_path, arch, metas, tree_hash):
    lock_path = pathlib.Path(lock_path)
    data = {}
    if lock_path.is_file():
        try:
            data = json.loads(lock_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            logger.warning(f'could not read existing lock {lock_path}: {e}')
            data = {}
    packages = {}
    for m in metas:
        packages[m['name']] = {
            'name': m['name'],
            'version': m.get('version', ''),
            'url': m.get('url', ''),
            'sha256': m.get('sha256', ''),
            'size': m.get('size', 0),
            'archive_path': m.get('archive_path', ''),
            'repo': m.get('repo', ''),
            'dist': m.get('dist', ''),
        }
    entry = {
        'arch': arch,
        'created_at': datetime.datetime.now(datetime.UTC).isoformat(),
        'tree_hash': tree_hash,
        'packages': packages,
    }
    # Canonical key is aarch64; keep arm64 alias for backwards compat.
    data[arch] = entry
    if arch == 'aarch64':
        data['arm64'] = entry
    elif arch == 'arm64':
        data['aarch64'] = entry
    lock_path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    logger.info(f'✓ wrote sysroot lock: {lock_path} ({len(packages)} packages)')


async def _work(out, arch, *src, lock_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        metas = await _download_packages(tmp, arch, *src)
        for m in metas:
            _extract(out, pathlib.Path(m['local_path']))

    usr = out/'usr'
    dst = 'data/data/com.termux/files/usr'

    assert os.path.isdir(out/dst)

    try:
        usr.symlink_to(dst, True)
    except FileExistsError:
        if not usr.samefile(out/dst):
            raise

    pthread = out/'usr/lib/libpthread.a'
    pthread.write_bytes(b'INPUT(-lc)')

    if lock_path is None:
        lock_path = pathlib.Path(__file__).parent / 'sysroot.lock.json'
    _write_lock(lock_path, arch, metas, _tree_hash(out))


@utils.record
class Sysroot:
    def __init__(self, path: str, **kwargs):
        self.path = pathlib.Path(path).expanduser().resolve()
        self.data = {}

        if not self.path.exists():
            self.path.mkdir()
        assert self.path.is_dir(), f'bad sysroot path: "{path}"'

        for k, v in kwargs.items():
            if isinstance(v, dict):
                self.__include__(k, **v)

    def __include__(self, name, repo, dist, pkgs):
        assert name and repo and dist and pkgs

        self.data[name] = {'repo': repo, 'dist': dist, 'pkgs': pkgs}

    def __call__(self, arch: str):
        arch = utils.termux_arch(arch)

        if self.data:
            asyncio.run(_work(self.path, arch, *self.data.values()))
        else:
            logger.info('no work to do.')

    def __str__(self):
        return str(self.path)


if __name__ == '__main__':
    import tomllib

    import fire

    with open('build.toml', 'rb') as f:
        src = tomllib.load(f)

    fire.Fire(Sysroot(**src['sysroot']))
