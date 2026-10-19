"""Tooling to render and publish the flutter-for-termux apt repository.

Subcommands on the command-line interface:

- ``stub``: build the architecture-independent stub ``.deb`` with ``dpkg-deb``.
- ``repo``: generate the repository indexes, ``Release``/``InRelease``,
  ``index.html`` and ``key.gpg`` (follow-up work; not registered yet).

This task ships the configuration loader, the maintainer-script renderer, the
control renderer and the owned-path parser, plus the ``stub`` subcommand.
Default paths are ``Path(__file__)``-relative so importing and rendering work
from any working directory.
"""

import shutil
import subprocess
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


if __name__ == "__main__":
    import fire

    def stub(out: str = "apt-repo-work", pkg_rel: str = "") -> str:
        """Build the architecture-independent stub ``.deb`` into ``out``."""
        # Fire hands numeric-looking arguments over as int; only an empty string
        # means "use [package] pkg_rel from build.toml".
        return str(build_stub(out_dir=out, pkg_rel=str(pkg_rel) or None))

    fire.Fire({"stub": stub})
