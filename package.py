#!/usr/bin/env python3

import ast
import io
import utils
import string
import base64
import requests
import tarfile
import zipfile
import hashlib
import tempfile
import subprocess
from git import Repo
from loguru import logger
from pathlib import Path


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
    assert src.exists()

    if src.is_dir():
        for root, dirs, files in src.walk():
            rel = root.relative_to(src)
            for it in dirs:
                yield rel/it
            for it in files:
                yield rel/it


def explore_git(src: Path):
    assert src.is_dir()

    for it in Repo(src).tree().traverse():
        yield it.path
    git = src/'.git'
    for it in explore_file(git):
        yield '.git'/it


def emit(out, src, git):
    assert isinstance(src, (Path, bytes, list)), src

    if isdir := isinstance(src, list):
        yield {'out': out}
    if isinstance(src, bytes):
        yield {'out': out, 'src': src}
        return
    for src, it in explore(src, git):
        yield {
            'out': out/src.name/it if isdir else out/it,
            'src': src/it}


def explore(src, git):
    explore = explore_git if git else explore_file

    if not isinstance(src, list):
        src = [src]
    for src in src:
        src = src.absolute()
        if not src.exists():
            logger.warning(f'source not found: "{src}"')
            continue
        yield src, Path('.')
        for it in explore(src):
            yield src, it


def reset(info):
    info.uid = 0
    info.gid = 0
    info.mtime = 0
    info.uname = 'root'
    info.gname = 'root'
    info.mode |= 0o200


def add_bin(tar, out, src, mod=None):
    assert tar, 'bad tar'
    assert out, 'bad out'
    assert isinstance(src, bytes), f'bad src type: "{type(src)}"'

    info = tarfile.TarInfo(str(out))
    info.mode = mod or 0o644
    info.size = len(src)
    reset(info)
    tar.addfile(info, io.BytesIO(src))


def add_file(tar, out, src, mod=None):
    assert tar, 'bad tar'
    assert out, 'bad out'
    assert src.exists(), f'source not found: "{src}"'

    info = tar.gettarinfo(src, out)
    info.mode = mod or info.mode
    reset(info)

    with open(src, 'rb') as f:
        tar.addfile(info, f)


def add_dir(tar, out, mod=None):
    assert tar, 'bad tar'
    assert out, 'bad out'

    cache = getattr(tar, '__cache__', set())
    tar.__cache__ = cache

    if out.parent == Path('.') or out in cache:
        return

    add_dir(tar, out.parent)
    info = tarfile.TarInfo(f'{out}/')
    info.type = tarfile.DIRTYPE
    info.mode = mod or 0o755
    reset(info)
    tar.addfile(info)
    cache.add(out)


def tar(path, data):
    if not data:
        logger.warning('no work to do.')
        return
    if isinstance(data, dict):
        data = [data]
    assert hasattr(data, '__iter__'), f'bad data format: "{data}"'

    with tarfile.open(path, mode='w:xz', format=tarfile.GNU_FORMAT, dereference=True) as tar:
        for it in data:
            out = it.get('out')
            src = it.get('src')
            mod = it.get('mod')
            assert out, f'bad out field: "{out}"'
            out = Path(out)
            assert mod is None or isinstance(mod, int)

            if isinstance(src, bytes):
                add_bin(tar, out, src, mod)
            elif not src or src.is_dir():
                add_dir(tar, out, mod)
            elif src.exists():
                add_file(tar, out, src, mod)
            else:
                raise FileNotFoundError(src)


def base64_md5_file(path):
    md5 = hashlib.md5()
    with open(path, 'rb') as f:
        while s := f.read(8192):
            md5.update(s)
    return base64.b64encode(md5.digest()).decode('utf8')


def download(url, out, timeout=(10, 60)):
    assert url, 'bad url'
    assert out, 'bad out'

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


class Output(object):
    def __init__(self, root, arch):
        self.any = None
        for it in utils.__MODE__:
            out = utils.target_output(root, arch, it)
            self.__dict__[it] = out
            if not self.any and Path(out).is_dir():
                self.any = out

        assert self.any, 'no valid out path found.'


@utils.record
class Package(object):
    def __init__(self, root, arch, control, resource, define=None, **extra):
        root = Path(root).resolve()
        assert root.is_dir(), f'bad flutter root path: "{root}"'
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

    def __format__(self, s, **extra):
        return string.Template(s).safe_substitute(
            **self.globals,
            **self.defines,
            **extra)

    def gen_control(self):
        bin = io.BytesIO()
        for k, v in self.control.items():
            bin.write(self.__format__(f'{k}: {v}\n').encode('utf8'))
        return {'out': 'control', 'src': bin.getvalue()}

    def gen_resource(self, name=None):
        if isinstance(name, str):
            yield from self.gen_resource_internal(name)
        elif isinstance(name, list):
            for it in name:
                yield from self.gen_resource_internal(it)
        elif not name:
            for it in self.resource.keys():
                yield from self.gen_resource_internal(it)
        else:
            raise ValueError(f'bad name: "{name}"')

    def gen_resource_internal(self, name=None):
        if not (data := self.resource.get(name)):
            raise ValueError(f'unknown resource name: "{name}"')

        git = data.get('git', False)
        src = data.get('source', [])
        out = data.get('output')
        bin = data.get('binary', False)
        mod = data.get('mode')
        dep = data.get('define', {})
        ext = {}

        for k, v in dep.items():
            dep[k] = safe_eval(v, self.globals, self.defines)

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
            out = (Path(self.__format__(it, **dep)) for it in out)
        else:
            raise ValueError(f'bad output type: "{type(out)}"')
        # expect None, str, list
        if isinstance(src, str):
            src = self.__format__(src, **dep)
            src = src.encode('utf8') if bin else Path(src)
        if isinstance(src, list) and not bin:
            src = [Path(self.__format__(it, **dep)) for it in src]
        elif not isinstance(src, (bytes, Path)):
            raise ValueError(f'bad source type: "{type(src)}"')

        for out in out:
            for it in emit(out, src, git):
                yield it | ext

    def test_resource(self, name=None, dest_dir=None):
        if isinstance(name, str):
            yield self.test_resource_internal(name, dest_dir=dest_dir)
        elif isinstance(name, list):
            for it in name:
                yield self.test_resource_internal(it, dest_dir=dest_dir)
        elif not name:
            for it in self.resource.keys():
                yield self.test_resource_internal(it, dest_dir=dest_dir)
        else:
            raise ValueError(f'bad name: "{name}"')

    def test_resource_internal(self, name, dest_dir=None):
        if not (data := self.resource.get(name)):
            raise ValueError(f'unknown resource name: "{name}"')

        if not (test := data.get('test', {})):
            return None
        deps = data.get('define', {}).items()
        deps = {k: safe_eval(v, self.globals, self.defines) for k, v in deps}
        file = self.__format__(test['file'], **deps)
        path = self.__format__(test['path'], **deps)
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
