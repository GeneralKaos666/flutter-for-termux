"""Tooling to render and publish the flutter-for-termux apt repository.

Subcommands on the command-line interface:

- ``stub``: build the architecture-independent stub ``.deb`` with ``dpkg-deb``.
- ``repo``: generate the repository indexes, ``Release``/``InRelease``,
  ``index.html`` and ``key.gpg``, signing with the key in ``GNUPGHOME``.

Default paths are ``Path(__file__)``-relative so importing and rendering work
from any working directory; the CLI's ``apt-repo-work``/``repo`` defaults are
resolved against the current working directory like any other CLI argument.
"""

import email.utils
import hashlib
import os
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BUILD_TOML = _REPO_ROOT / "build.toml"
_DEFAULT_PACKAGE_YAML = _REPO_ROOT / "package.yaml"
_DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "stub"
_TEMPLATE_NAMES = ("preinst", "postinst", "postrm")
_RESOLVE_PASSES = 10

# The published site's shape (spec section 3): one suite, one component, one
# architecture. "Architecture: all" stubs are indexed under binary-aarch64.
_SUITE = "termux"
_COMPONENT = "main"
_SITE_ARCH = "aarch64"
_DEFAULT_STUB_DIR = Path("apt-repo-work")
_DEFAULT_SITE_DIR = Path("repo")

_QUICK_START = (
    "pkg install -y gnupg\n"
    'mkdir -p "$PREFIX/etc/apt/keyrings"\n'
    "curl -fsSL https://GeneralKaos666.github.io/flutter-for-termux/key.gpg \\\n"
    '  | gpg --dearmor > "$PREFIX/etc/apt/keyrings/flutter.gpg"\n'
    'echo "deb [signed-by=$PREFIX/etc/apt/keyrings/flutter.gpg] '
    'https://GeneralKaos666.github.io/flutter-for-termux termux main" \\\n'
    '  > "$PREFIX/etc/apt/sources.list.d/flutter.list"\n'
    "pkg update && pkg install flutter\n"
    "flutter doctor"
)

# The quick start is substituted verbatim: it holds no "<" and only a
# non-ambiguous "&&", so it is valid HTML text as-is.
_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flutter-for-termux apt repository</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 48rem; padding: 0 1rem; }
  pre { background: #f4f4f4; padding: 1rem; overflow-x: auto; }
  code { font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<h1>flutter-for-termux apt repository</h1>
<p>
  This is the apt repository for <code>flutter</code> on Termux. The signing key is
  <a href="key.gpg">key.gpg</a>; the snippet below installs <code>gnupg</code>, creates the
  apt keyrings directory, stores the key as
  <code>$PREFIX/etc/apt/keyrings/flutter.gpg</code> and adds the repository to
  <code>$PREFIX/etc/apt/sources.list.d/flutter.list</code>.
</p>
<h2>Quick start</h2>
<pre><code><!--QUICK_START-->
</code></pre>
<p>
  Full instructions live in the
  <a href="https://github.com/GeneralKaos666/flutter-for-termux#quick-start">README quick start</a>.
  Never run <code>flutter upgrade</code>; use <code>apt upgrade flutter</code> instead.
</p>
</body>
</html>
"""


def load_config(build_toml: Path | None = None) -> dict:
    """Read ``build.toml`` and return the version fields the renderer needs.

    Returns ``{"tag": str, "pkg_rel": str, "version": str}`` where ``version``
    is ``<tag>-<pkg_rel>`` — or just ``tag`` when the package revision is
    empty, the same rule ``build.py`` applies.
    """
    path = _DEFAULT_BUILD_TOML if build_toml is None else Path(build_toml)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    tag = str(raw["flutter"]["tag"]).strip()
    pkg_rel = str(raw["package"].get("pkg_rel") or "").strip()
    version = f"{tag}-{pkg_rel}" if pkg_rel else tag
    return {"tag": tag, "pkg_rel": pkg_rel, "version": version}


def render_scripts(
    version: str,
    owned_paths: Sequence[str],
    template_dir: Path | None = None,
    build_toml: Path | None = None,
) -> dict[str, str]:
    """Render the maintainer-script templates found in ``template_dir``.

    Each of ``preinst``/``postinst``/``postrm`` that exists is copied and its
    ``@VERSION@``, ``@TAG@`` and ``@OWNED_PATHS@`` placeholders are
    substituted. Returns ``{template name: rendered text}`` for whichever
    templates exist.

    ``build_toml`` supplies the release tag for ``@TAG@``; it defaults to the
    repo-root ``build.toml``, so callers that omit it are unaffected. Pass the
    same config as :func:`render_control`/:func:`build_stub` to keep the
    control version and the script URL tag in agreement.
    """
    directory = _DEFAULT_TEMPLATE_DIR if template_dir is None else Path(template_dir)
    tag = load_config(build_toml)["tag"]
    rendered: dict[str, str] = {}
    for name in _TEMPLATE_NAMES:
        template = directory / name
        if not template.is_file():
            continue
        text = template.read_text(encoding="utf-8")
        rendered[name] = (
            text.replace("@VERSION@", version)
            .replace("@TAG@", tag)
            .replace("@OWNED_PATHS@", " ".join(owned_paths))
        )
    return rendered


def _unquote(value: object) -> str:
    """Strip ``package.yaml``'s quoting from a value.

    Values arrive wrapped in one of several ways: ``'"data/.../usr"'`` (a quoted
    plain scalar), ``f'{distro}/bin/cache'`` (an ``f``-string shell fragment)
    or bare text. Only the wrapper is removed; the content is left verbatim.
    """
    text = str(value).strip()
    if len(text) >= 3 and text[0] in "fF" and text[1] in "'\"":
        text = text[1:]
    return text.strip().strip("'\"")


def _substitute(text: str, mapping: dict[str, str], passes: int = _RESOLVE_PASSES) -> str:
    """Replace ``$key`` and ``{key}`` occurrences from ``mapping``.

    Iterates so values that reference each other resolve fully; longer keys are
    substituted first so a key can never eat the prefix of a longer one.
    Unresolvable tokens (``$arch``, ``{version}``) are left in place.
    """
    for _ in range(passes):
        before = text
        for key in sorted(mapping, key=len, reverse=True):
            value = mapping[key]
            text = text.replace(f"${key}", value).replace("{" + key + "}", value)
        if text == before:
            break
    return text


def _resolve_defines(defines: dict) -> dict[str, str]:
    """Unquote a resource's define map and resolve its self-references."""
    resolved = {key: _unquote(value) for key, value in defines.items()}
    for _ in range(_RESOLVE_PASSES):
        updated = {key: _substitute(value, resolved) for key, value in resolved.items()}
        if updated == resolved:
            break
        resolved = updated
    return resolved


def parse_owned_paths(package_yaml: Path) -> list[str]:
    """Derive the stub's owned path groups from ``package.yaml`` resources.

    Groups are never hand-listed: every resource ``output`` is resolved against
    the merged define map, stripped of the Termux prefix, and collapsed to the
    shallowest safe group. Anything under the Flutter distro collapses to
    ``opt/flutter``; sibling files collapse to their common parent directory
    only when that parent holds at least two paths *and* is not a bare
    top-level component — so ``share/flutter`` is one group while ``bin/flutter``
    and ``etc/profile.d/flutter.sh`` stay individual paths (a bare ``etc`` or
    ``bin`` would let ``postrm`` delete shared directories).
    """
    document = yaml.safe_load(Path(package_yaml).read_text(encoding="utf-8")) or {}
    global_define = document.get("define") or {}
    resolved_global = _resolve_defines(global_define)
    prefix = resolved_global.get("prefix", "").rstrip("/")
    distro = resolved_global.get("distro", "")
    distro_group = distro[len(prefix) :].lstrip("/") if prefix and distro.startswith(prefix) else ""
    keep = f"{prefix}/"

    owned: set[str] = set()
    children: dict[str, list[str]] = {}
    for resource in (document.get("resource") or {}).values():
        merged = {**global_define, **(resource.get("define") or {})}
        resolved = _resolve_defines(merged)
        outputs = resource.get("output") or []
        if isinstance(outputs, str):
            outputs = [outputs]
        for output in outputs:
            path = _substitute(str(output), resolved).lstrip("/")
            if not path.startswith(keep):
                continue
            path = path[len(keep) :]
            if not path:
                continue
            if distro_group and (path == distro_group or path.startswith(f"{distro_group}/")):
                owned.add(distro_group)
                continue
            children.setdefault(str(Path(path).parent), []).append(path)

    for parent, paths in children.items():
        if len(paths) >= 2 and len(Path(parent).parts) >= 2:
            owned.add(parent)
        else:
            owned.update(paths)
    return sorted(owned)


def render_control(
    version: str,
    package_yaml: Path,
    build_toml: Path | None = None,
) -> str:
    """Render the stub's ``control`` member from ``package.yaml``.

    Field order follows ``package.yaml``; only ``Version`` and ``Architecture``
    are overridden and the installer's unconditional dependencies are appended
    to the payload's own ``Depends`` list.
    """
    document = yaml.safe_load(Path(package_yaml).read_text(encoding="utf-8")) or {}
    control = dict(document.get("control") or {})

    path = _DEFAULT_BUILD_TOML if build_toml is None else Path(build_toml)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    installer = raw.get("installer") or {}

    depends = [item.strip() for item in str(control.get("Depends", "")).split(",") if item.strip()]
    extras = [
        str(installer["java"]),
        "wget",
        "curl",
        "unzip",
        str(installer["zip_tool"]),
        "binutils",
        "tar",
        "xz-utils",
    ]
    control["Version"] = version
    control["Architecture"] = "all"
    control["Depends"] = ", ".join([*depends, *extras])
    return "".join(f"{key}: {value}\n" for key, value in control.items())


def build_stub(
    out_dir: str | Path = "apt-repo-work",
    pkg_rel: str | None = None,
    build_toml: Path | None = None,
    package_yaml: Path | None = None,
) -> Path:
    """Build the architecture-independent stub ``.deb`` and return its path.

    ``pkg_rel`` overrides ``[package] pkg_rel`` from ``build.toml`` (used by
    tests and acceptance bumps). The deb is written to
    ``out_dir/flutter_<version>_all.deb``; being control-only means dpkg tracks
    no files while ``postinst`` owns the real 635 MB tree.
    """
    config = load_config(build_toml)
    # Fire parses numeric-looking CLI arguments into int, so coerce before use.
    rel = str(pkg_rel).strip() if pkg_rel is not None else ""
    version = f"{config['tag']}-{rel}" if rel else config["version"]
    package = _DEFAULT_PACKAGE_YAML if package_yaml is None else Path(package_yaml)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stage = out / ".stub-root"
    deb = out / f"flutter_{version}_all.deb"
    if stage.exists():
        shutil.rmtree(stage)
    try:
        control_dir = stage / "DEBIAN"
        control_dir.mkdir(parents=True)
        # dpkg-deb rejects a control directory the umask made private.
        stage.chmod(0o755)
        control_dir.chmod(0o755)

        control_path = control_dir / "control"
        control_path.write_text(render_control(version, package, build_toml), encoding="utf-8")
        control_path.chmod(0o644)
        for name, text in render_scripts(
            version, parse_owned_paths(package), build_toml=build_toml
        ).items():
            script = control_dir / name
            script.write_text(text, encoding="utf-8")
            script.chmod(0o755)

        result = subprocess.run(
            ["dpkg-deb", "--build", str(stage), str(deb)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"dpkg-deb --build failed ({result.returncode}): {result.stderr.strip()}"
            )
        return deb
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _resolve_signing_key(key: str) -> str:
    """Return the fingerprint to sign with, auto-detecting a single secret key.

    ``key == ""`` lists the secret keys in ``GNUPGHOME``: exactly one is used,
    while zero or many raise ``ValueError`` telling the caller to pass ``key=``
    (``--key`` on the CLI).
    """
    if key:
        return key
    result = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gpg --list-secret-keys failed ({result.returncode}): {result.stderr.strip()}"
        )
    fingerprints, expecting = [], False
    for line in result.stdout.splitlines():
        kind = line.split(":", 1)[0]
        if kind == "sec":
            expecting = True
        elif kind == "fpr" and expecting:
            fingerprints.append(line.split(":")[9])
            expecting = False
    if len(fingerprints) != 1:
        home = os.environ.get("GNUPGHOME") or "~/.gnupg"
        raise ValueError(
            f"found {len(fingerprints)} secret keys in GNUPGHOME={home}; "
            "pass key= (--key on the CLI) to choose the signing key"
        )
    return fingerprints[0]


def _passphrase_flags(passphrase: str | None) -> list[str]:
    """Extra gpg arguments selecting loopback pinentry.

    ``None`` first consults ``APT_REPO_SIGNING_PASSPHRASE``; when neither the
    argument nor the environment carries a value, gpg-agent supplies the
    passphrase (the unlocked personal keyring's mode). An empty string is still a
    value: it means "this key has no passphrase", so loopback pinentry is used
    with an empty passphrase rather than left to a pinentry prompt.
    """
    value = os.environ.get("APT_REPO_SIGNING_PASSPHRASE") if passphrase is None else passphrase
    if value is None:
        return []
    return ["--pinentry-mode", "loopback", "--passphrase", value]


def _extract_control(deb: Path, into: Path) -> Path:
    """Extract ``deb``'s control member into ``into`` and return the ``control`` file."""
    result = subprocess.run(
        ["dpkg-deb", "-e", str(deb), str(into)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"dpkg-deb -e failed for {deb} ({result.returncode}): {result.stderr.strip()}"
        )
    return into / "control"


def _parse_control(path: Path) -> list[tuple[str, str]]:
    """Parse a control paragraph into ordered ``(field, value)`` pairs.

    Continuation lines (leading whitespace) are folded into the preceding
    field's value; the paragraph's field order is preserved because the
    ``Packages`` index keeps it.
    """
    fields: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and fields:
            name, value = fields[-1]
            fields[-1] = (name, f"{value} {line.strip()}")
            continue
        name, _, value = line.partition(":")
        fields.append((name.strip(), value.strip()))
    return fields


def _xz(path: Path) -> None:
    """Write ``<path>.xz`` at level 9 beside ``path``, keeping the original."""
    if shutil.which("xz") is None:
        raise RuntimeError("xz is required to build the apt repository indexes")
    result = subprocess.run(
        ["xz", "-9", "-k", "-f", path.name],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"xz failed for {path} ({result.returncode}): {result.stderr.strip()}")


def _render_release(dists: Path, index_files: Sequence[str]) -> str:
    """Render ``dists/termux/Release`` with a SHA256 block over ``index_files``.

    Each entry is `` <hash> <size> <component-relative-path>``; paths are
    relative to ``dists/<suite>/``, exactly as apt resolves them.
    """
    lines = [
        f"Codename: {_SUITE}",
        "Version: 1",
        f"Architectures: {_SITE_ARCH}",
        "Description: flutter-for-termux apt repository",
        f"Suite: {_SUITE}",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        f"Components: {_COMPONENT}",
        "SHA256:",
    ]
    for relative in index_files:
        data = (dists / relative).read_bytes()
        lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data)} {relative}")
    return "\n".join(lines) + "\n"


def _clearsign(directory: Path, fingerprint: str, flags: Sequence[str]) -> None:
    """Clear-sign ``Release`` into ``InRelease`` with ``cwd`` set to ``directory``."""
    result = subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",  # rebuilds over an existing site must not stop on "File exists"
            *flags,
            "--local-user",
            fingerprint,
            "--clearsign",
            "--output",
            "InRelease",
            "Release",
        ],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gpg --clearsign failed ({result.returncode}): {result.stderr.strip()}"
        )


def _export_public_key(fingerprint: str) -> bytes:
    """ASCII-armored public export of ``fingerprint`` (the published ``key.gpg``)."""
    result = subprocess.run(
        ["gpg", "--batch", "--export", "--armor", fingerprint],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gpg --export failed ({result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    if not result.stdout.strip():
        raise RuntimeError(f"gpg --export produced no key for {fingerprint}")
    return result.stdout


def write_index_html(site_root: str | Path) -> Path:
    """Write the version-free quick-start ``index.html`` and return its path."""
    index = Path(site_root) / "index.html"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(_INDEX_HTML.replace("<!--QUICK_START-->", _QUICK_START), encoding="utf-8")
    index.chmod(0o644)
    return index


def _index_deb(deb: Path, binary: Path) -> tuple[list[tuple[str, str]], Path]:
    """Extract control fields, copy deb to binary dir, and return (fields, published_path)."""
    published = binary / deb.name
    shutil.copyfile(deb, published)
    published.chmod(0o644)
    with tempfile.TemporaryDirectory(prefix="apt-repo.") as work:
        fields = _parse_control(_extract_control(deb, Path(work) / "control"))
    fields.extend(
        [
            ("Filename", f"dists/{_SUITE}/{_COMPONENT}/binary-{_SITE_ARCH}/{deb.name}"),
            ("Size", str(published.stat().st_size)),
            ("SHA256", hashlib.sha256(published.read_bytes()).hexdigest()),
        ]
    )
    return fields, published


def build_repo(
    deb_path: str | Path | Sequence[str | Path] = "",
    out_dir: str | Path = _DEFAULT_SITE_DIR,
    key: str = "",
    passphrase: str | None = None,
    debs_dir: str | Path | None = None,
) -> Path:
    """Generate the signed apt repository for one or more debs.

    Writes the pinned site under ``out_dir`` — ``index.html``, ``key.gpg``,
    ``dists/termux/{InRelease,Release}`` and
    ``dists/termux/main/{Contents-aarch64,.xz}`` plus
    ``dists/termux/main/binary-aarch64/{Packages,Packages.xz,<deb>}`` — and
    returns ``Path(out_dir)``. ``key`` is a fingerprint (``""`` auto-resolves a
    single secret key in ``GNUPGHOME``); see :func:`_passphrase_flags` for the
    ``passphrase``/``APT_REPO_SIGNING_PASSPHRASE`` seam.
    """
    fingerprint = _resolve_signing_key(key)
    flags = _passphrase_flags(passphrase)

    out = Path(out_dir)
    dists = out / "dists" / _SUITE
    component = dists / _COMPONENT
    binary = component / f"binary-{_SITE_ARCH}"
    binary.mkdir(parents=True, exist_ok=True)

    deb_paths: list[Path] = []
    if debs_dir is not None:
        for p in sorted(Path(debs_dir).glob("*.deb")):
            deb_paths.append(p)
    elif deb_path == "" or deb_path is None:
        # Default behavior: prefer apt-repo-work for stub, but also allow any .deb in cwd patterns
        work_dir = _DEFAULT_STUB_DIR
        deb_paths = sorted(work_dir.glob("*.deb")) if work_dir.exists() else []
    else:
        if isinstance(deb_path, (str, Path)):
            deb_paths = [Path(deb_path)]
        else:
            for p in deb_path:
                deb_paths.append(Path(p))

    if not deb_paths:
        raise FileNotFoundError("no .deb files found to index")
    for deb in deb_paths:
        if not deb.is_file():
            raise FileNotFoundError(f"stub deb not found: {deb}")

    packages_parts = []
    for deb in deb_paths:
        fields, _published = _index_deb(deb, binary)
        packages_parts.append("".join(f"{name}: {value}\n" for name, value in fields) + "\n")
    packages = "".join(packages_parts)
    (binary / "Packages").write_text(packages, encoding="utf-8")
    contents = component / f"Contents-{_SITE_ARCH}"
    contents.write_text("", encoding="utf-8")
    _xz(binary / "Packages")
    _xz(contents)

    index_files = [
        f"{_COMPONENT}/binary-{_SITE_ARCH}/Packages",
        f"{_COMPONENT}/binary-{_SITE_ARCH}/Packages.xz",
        f"{_COMPONENT}/Contents-{_SITE_ARCH}",
        f"{_COMPONENT}/Contents-{_SITE_ARCH}.xz",
    ]
    (dists / "Release").write_text(_render_release(dists, index_files), encoding="utf-8")
    (dists / "Release").chmod(0o644)
    _clearsign(dists, fingerprint, flags)
    (dists / "InRelease").chmod(0o644)

    (out / "key.gpg").write_bytes(_export_public_key(fingerprint))
    (out / "key.gpg").chmod(0o644)
    write_index_html(out)
    return out


def _discover_stub_deb(work_dir: Path) -> Path:
    """The single ``*.deb`` in ``work_dir``; ``ValueError`` on zero or several."""
    candidates = sorted(work_dir.glob("*.deb"))
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one .deb in {work_dir}, found {len(candidates)}; "
            "pass deb= (--deb on the CLI) explicitly"
        )
    return candidates[0]


def repo(deb: str = "", out: str = "repo", key: str = "") -> str:
    """Generate the signed apt repository from a stub ``.deb``.

    ``deb`` defaults to the single ``apt-repo-work/*.deb``; both it and ``out``
    resolve against the current working directory.
    """
    deb_path = Path(str(deb)) if str(deb) else _discover_stub_deb(_DEFAULT_STUB_DIR)
    return str(build_repo(deb_path, out_dir=Path(str(out) or "repo"), key=str(key)))


if __name__ == "__main__":
    import fire

    def stub(out: str = "apt-repo-work", pkg_rel: str = "") -> str:
        """Build the architecture-independent stub ``.deb`` into ``out``."""
        # Fire hands numeric-looking arguments over as int; only an empty string
        # means "use [package] pkg_rel from build.toml".
        return str(build_stub(out_dir=out, pkg_rel=str(pkg_rel) or None))

    fire.Fire({"stub": stub, "repo": repo})
