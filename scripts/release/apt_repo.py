"""Tooling to render and publish the flutter-for-termux apt repository.

Two subcommands back the command-line interface (registered in follow-up work):

- ``stub``: build the architecture-independent stub ``.deb`` with ``dpkg-deb``.
- ``repo``: generate the repository indexes, ``Release``/``InRelease``,
  ``index.html`` and ``key.gpg``.

This task ships only the configuration loader and the maintainer-script
renderer; running the module as a script reports that the subcommands are not
registered yet. Default paths are ``Path(__file__)``-relative so importing and
rendering work from any working directory.
"""

import tomllib
from collections.abc import Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BUILD_TOML = _REPO_ROOT / "build.toml"
_DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "stub"
_TEMPLATE_NAMES = ("preinst", "postinst", "postrm")


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
) -> dict[str, str]:
    """Render the maintainer-script templates found in ``template_dir``.

    Each of ``preinst``/``postinst``/``postrm`` that exists is copied and its
    ``@VERSION@``, ``@TAG@`` and ``@OWNED_PATHS@`` placeholders are
    substituted. Returns ``{template name: rendered text}`` for whichever
    templates exist.
    """
    directory = _DEFAULT_TEMPLATE_DIR if template_dir is None else Path(template_dir)
    tag = load_config()["tag"]
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


if __name__ == "__main__":
    raise SystemExit("subcommands not yet registered")
