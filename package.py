#!/usr/bin/env python3

import ast
import base64
import hashlib
import io
import string
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import requests
from git import Repo
from loguru import logger

import utils

# Restricted expression evaluator for package.yaml `define:` values.
# Replaces bare eval() to close the code-injection surface while keeping
# the existing template contract (string literals, f-strings, attribute
# access such as output.any, and plain names from globals/defines).
_SAFE_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.BinOp,
    ast.Add,
    ast.Mod,
    ast.Mult,
    ast.UnaryOp,
    ast.UAdd,
    ast.USub,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.IfExp,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Subscript,
    ast.Slice,
)


def safe_eval(expr: str, globals_dict: dict, locals_dict: dict | None = None):
    """Evaluate a package.yaml define expression with a constrained AST.

    Allows literals, names, attribute access, f-strings, and simple
    container/operator expressions. Rejects calls, imports, lambdas,
    comprehensions, and dunder access.
    """
    if not isinstance(expr, str):
        raise ValueError(f'bad define expression type: "{type(expr)}"')
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as e:
        raise ValueError(f'bad define expression: "{expr}": {e}') from e
    allowed = dict(globals_dict)
    if locals_dict:
        allowed.update(locals_dict)
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            raise ValueError(
                f'forbidden expression in define: "{expr}": {type(node).__name__}'
            )
        if isinstance(node, ast.Name) and node.id.startswith('__'):
            raise ValueError(f'forbidden name in define: "{expr}": {node.id}')
        if isinstance(node, ast.Attribute) and node.attr.startswith('__'):
            raise ValueError(f'forbidden attribute in define: "{expr}": {node.attr}')
        if isinstance(node, ast.Name) and node.id not in allowed and node.id not in (
            'True', 'False', 'None',
        ):
            raise ValueError(f'unknown name in define: "{expr}": {node.id}')
    return eval(  # noqa: S307 - AST allowlisted above, no builtins exposed
        compile(tree, '<package-define>', 'eval'),
        {'__builtins__': {}},
        allowed,
    )


def explore_file(src: Path):
    if not src.exists():
        raise FileNotFoundError(src)

    if src.is_dir():
        for root, dirs, files in src.walk():
            rel = root.relative_to(src)
            for it in dirs:
                yield rel/it
            for it in files:
                yield rel/it


def explore_git(src: Path):
    if not src.is_dir():
        raise ValueError(f'not a directory: "{src}"')

    for it in Repo(src).tree().traverse():
        yield it.path
    git = src/'.git'
    for it in explore_file(git):
        yield '.git'/it


def emit(out, src, git):
    if not isinstance(src, (Path, bytes, list)):
        raise ValueError(f'bad src type: "{type(src)}"')

    nested = isinstance(src, list)
    if nested:
        yield {'out': out}
    if isinstance(src, bytes):
        yield {'out': out, 'src': src}
        return
    for src, it in explore(src, git):
        yield {
            'out': out/src.name/it if nested else out/it,
            'src': src/it}


def explore(src, git):
    walk = explore_git if git else explore_file

    if not isinstance(src, list):
        src = [src]
    for src in src:
        src = src.absolute()
        if not src.exists():
            logger.warning(f'source not found: "{src}"')
            continue
        yield src, Path('.')
        for it in walk(src):
            yield src, it


def reset(info):
    info.uid = 0
    info.gid = 0
    info.mtime = 0
    info.uname = 'root'
    info.gname = 'root'
    info.mode |= 0o200


def add_bin(archive, out, src, mod=None):
    if not archive:
        raise ValueError('bad tar')
    if not out:
        raise ValueError('bad out')
    if not isinstance(src, bytes):
        raise ValueError(f'bad src type: "{type(src)}"')

    info = tarfile.TarInfo(str(out))
    info.mode = mod or 0o644
    info.size = len(src)
    reset(info)
    archive.addfile(info, io.BytesIO(src))


def add_file(archive, out, src, mod=None):
    if not archive:
        raise ValueError('bad tar')
    if not out:
        raise ValueError('bad out')
    if not src.exists():
        raise FileNotFoundError(src)

    info = archive.gettarinfo(src, out)
    info.mode = mod or info.mode
    reset(info)

    with open(src, 'rb') as f:
        archive.addfile(info, f)


def add_dir(archive, out, mod=None, cache=None):
    """Emit ``out`` and its ancestors (once each) as tar directory entries.

    ``cache`` tracks directories already written for the current archive; it is
    threaded explicitly by :func:`tar` instead of being smuggled onto the
    tarfile object.
    """
    if not archive:
        raise ValueError('bad tar')
    if not out:
        raise ValueError('bad out')
    if cache is None:
        cache = set()

    if out.parent == Path('.') or out in cache:
        return

    add_dir(archive, out.parent, cache=cache)
    info = tarfile.TarInfo(f'{out}/')
    info.type = tarfile.DIRTYPE
    info.mode = mod or 0o755
    reset(info)
    archive.addfile(info)
    cache.add(out)


def tar(path, data):
    if not data:
        logger.warning('no work to do.')
        return
    if isinstance(data, dict):
        data = [data]
    if not hasattr(data, '__iter__'):
        raise ValueError(f'bad data format: "{data}"')

    cache = set()
    with tarfile.open(
        path, mode='w:xz', format=tarfile.GNU_FORMAT, dereference=True
    ) as archive:
        for it in data:
            out = it.get('out')
            src = it.get('src')
            mod = it.get('mod')
            if not out:
                raise ValueError(f'bad out field: "{out}"')
            out = Path(out)
            if mod is not None and not isinstance(mod, int):
                raise ValueError(f'bad mod type: "{type(mod)}"')

            # dpkg does not create missing parent directories while
            # unpacking, so every file entry needs explicit ancestor dir
            # entries emitted before it (e.g. share/flutter for manifest.json
            # and post_install.sh). add_dir() recurses and de-duplicates.
            if isinstance(src, bytes):
                add_dir(archive, out.parent, cache=cache)
                add_bin(archive, out, src, mod)
            elif not src or src.is_dir():
                add_dir(archive, out, mod, cache=cache)
            elif src.exists():
                add_dir(archive, out.parent, cache=cache)
                add_file(archive, out, src, mod)
            else:
                raise FileNotFoundError(src)


def base64_md5_file(path):
    md5 = hashlib.md5()
    with open(path, 'rb') as f:
        while s := f.read(8192):
            md5.update(s)
    return base64.b64encode(md5.digest()).decode('utf8')


def download(url, out, timeout=(10, 60)):
    if not url:
        raise ValueError('bad url')
    if not out:
        raise ValueError('bad out')

    if (dst := Path(out)) and dst.is_dir():
        dst = dst/url.split('?')[0].split('/')[-1]
    try:
        with requests.get(url, allow_redirects=True, stream=True, timeout=timeout) as resp:
            if resp.status_code != 200:
                logger.warning(f'download failed ({resp.status_code}): "{url}"')
                return None
            expected_md5 = None
            if goog_hash := resp.headers.get('x-goog-hash'):
                try:
                    expected_md5 = dict(
                        it.strip().split('=', 1) for it in goog_hash.split(',')
                    ).get('md5')
                except ValueError:
                    expected_md5 = None
            if dst.is_file() and expected_md5 and base64_md5_file(dst) == expected_md5:
                logger.info(f'download cached: "{dst}"')
                return dst
            md5 = hashlib.md5()
            with open(dst, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    md5.update(chunk)
                    f.write(chunk)
            if expected_md5:
                actual_md5 = base64.b64encode(md5.digest()).decode('utf8')
                if actual_md5 != expected_md5:
                    logger.error(f'md5 mismatch for "{url}"')
                    try:
                        dst.unlink()
                    except OSError:
                        pass
                    return None
            return dst
    except requests.RequestException as e:
        logger.warning(f'download failed: "{url}": {e}')
        return None


class ResourceDispatcher:
    """Resolve a resource selector into an ordered list of resource names.

    ``str`` selects one resource, a list selects each entry in order, and a
    falsy value selects every resource in declaration order. Anything else
    is a usage error. Both the generator and the test paths share this
    ``str|list|falsy|else`` ladder so they can never drift apart.
    """

    __slots__ = ('keys',)

    def __init__(self, keys):
        self.keys = keys

    def names(self, name=None):
        if isinstance(name, str):
            return [name]
        if isinstance(name, list):
            return list(name)
        if not name:
            return list(self.keys)
        raise ValueError(f'bad name: "{name}"')


class Output:
    def __init__(self, root, arch):
        self.any = None
        for mode in utils.__MODE__:
            out = utils.target_output(root, arch, mode)
            self.__dict__[mode] = out
            if not self.any and out.is_dir():
                self.any = out

        if not self.any:
            raise ValueError('no valid out path found.')


@utils.record
class Package:
    def __init__(self, root, arch, control, resource, define=None, **extra):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f'bad flutter root path: "{root}"')
        self.globals = {
            'tag': utils.flutter_tag(root),
            'root': root,
            'arch': arch,
            'output': Output(root, arch),
            'version': utils.engine_version(root),
            'architecture': utils.termux_arch(arch),
        }
        self.globals.update(extra)
        self.defines = {
            k: safe_eval(v, self.globals) for k, v in define.items()
        } if define else {}
        self.control = control
        self.resource = resource
        self.__dict__.update(self.globals)
        self.__dict__.update(self.defines)

    def render(self, s, **extra):
        return string.Template(s).safe_substitute(
            **self.globals,
            **self.defines,
            **extra)

    def gen_control(self):
        bin = io.BytesIO()
        for k, v in self.control.items():
            bin.write(self.render(f'{k}: {v}\n').encode('utf8'))
        return {'out': 'control', 'src': bin.getvalue()}

    def gen_resource(self, name=None):
        for it in ResourceDispatcher(self.resource).names(name):
            yield from self.gen_resource_internal(it)

    def _resolve_defines(self, data):
        """Evaluate a resource's ``define:`` block into a fresh mapping.

        Never mutates the loaded YAML: define blocks are aliased/shared and a
        resource may be generated more than once per process.
        """
        return {
            k: safe_eval(v, self.globals, self.defines)
            for k, v in data.items()
        }

    def gen_resource_internal(self, name=None):
        if not (data := self.resource.get(name)):
            raise ValueError(f'unknown resource name: "{name}"')

        git = data.get('git', False)
        src = data.get('source', [])
        out = data.get('output')
        bin = data.get('binary', False)
        mod = data.get('mode')
        dep = self._resolve_defines(data.get('define', {}))
        ext = {}

        # expect None, str, int
        if isinstance(mod, str):
            mod = int(mod, 8)
        if isinstance(mod, int):
            ext['mod'] = mod
        elif mod is not None:
            raise ValueError(f'bad mode type: "{type(mod)}"')
        # expect str, list
        if isinstance(out, str):
            out = [out]
        if isinstance(out, list):
            out = (Path(self.render(it, **dep)) for it in out)
        else:
            raise ValueError(f'bad output type: "{type(out)}"')
        # expect None, str, list
        if isinstance(src, str):
            src = self.render(src, **dep)
            src = src.encode('utf8') if bin else Path(src)
        if isinstance(src, list) and not bin:
            src = [Path(self.render(it, **dep)) for it in src]
        elif not isinstance(src, (bytes, Path)):
            raise ValueError(f'bad source type: "{type(src)}"')

        for out in out:
            for it in emit(out, src, git):
                yield it | ext

    def test_resource(self, name=None, dest_dir=None):
        for it in ResourceDispatcher(self.resource).names(name):
            yield self.test_resource_internal(it, dest_dir=dest_dir)

    def test_resource_internal(self, name, dest_dir=None):
        if not (data := self.resource.get(name)):
            raise ValueError(f'unknown resource name: "{name}"')

        if not (test := data.get('test', {})):
            return None
        deps = self._resolve_defines(data.get('define', {}))
        file = self.render(test['file'], **deps)
        path = self.render(test['path'], **deps)
        dest_dir = Path(dest_dir).expanduser() if dest_dir else Path(tempfile.gettempdir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        if not (dest := download(file, dest_dir)):
            logger.warning(f'test file not found: "{file}"')
            return None

        data = {it['out'] for it in self.gen_resource(name)}
        with zipfile.ZipFile(dest) as f:
            for it in f.namelist():
                if not it.endswith('.md') and Path(path, it) not in data:
                    logger.error(f'missing file: {path}/{it}')
                    return False
        return True

    def debuild(self, output, section=None):
        output = Path(output or '.').expanduser().resolve()
        if not output.parent.is_dir() or output.is_dir():
            raise ValueError(f'bad output path: "{output}"')

        with tempfile.TemporaryDirectory() as tmp:
            info = Path(tmp, 'debian-binary')
            ctrl = Path(tmp, 'control.tar.xz')
            data = Path(tmp, 'data.tar.xz')

            with open(info, 'wb+') as f:
                f.write(b'2.0\n')
            tar(ctrl, self.gen_control())
            tar(data, self.gen_resource(section))

            subprocess.run(
                    ['ar', 'rc', output, info, ctrl, data],
                    check=True)

        logger.info(f'✓ package built: {output}')


if __name__ == '__main__':
    import fire
    import yaml

    with open('package.yaml', 'rb') as f:
        src = yaml.safe_load(f)
    pkg = Package(root='flutter', arch='arm64', **src)
    fire.Fire(pkg)
