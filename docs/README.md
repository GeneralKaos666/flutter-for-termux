# flutter apt repository for Termux (metadata only)

This site is an APT repository containing the **metadata** for the `flutter`
package. The actual `.deb` (~635 MB) is stored as a **GitHub release asset**
— APT's `Filename` field is always resolved against the repository URL, and
GitHub Pages cannot hold files larger than 100 MB, so `prefetch.sh` (below)
hands the `.deb` to APT.

## Install / update (Termux)

```sh
# 1. trust the repository signing key
mkdir -p "$PREFIX/etc/apt/keyrings"
curl -fsSL https://generalkaos666.github.io/flutter-for-termux/key.gpg \
     -o "$PREFIX/etc/apt/keyrings/flutter-apt-repo.gpg"

# 2. add the repository
echo "deb [signed-by=$PREFIX/etc/apt/keyrings/flutter-apt-repo.gpg] https://generalkaos666.github.io/flutter-for-termux termux main" \
     > "$PREFIX/etc/apt/sources.list.d/flutter-apt-repo.list"
apt update

# 3. fetch the .deb into apt's cache (sha256-verified, idempotent)
curl -fsSL https://generalkaos666.github.io/flutter-for-termux/prefetch.sh | bash

# 4. install / upgrade
apt install flutter
```

Steps 3 has to be repeated whenever a new version appears in `apt update`
output; steps 1–2 are one-time.

## Verify the key

```
fingerprint: 1DED 0840 4574 B867 B058  A7BB 476F C230 ECBB FDF0
```

## Files

| Path | Purpose |
|------|---------|
| `dists/termux/InRelease` | signed Release file (APT entry point) |
| `dists/termux/main/binary-aarch64/Packages(.xz)` | package metadata |
| `key.gpg` | public key that signs `InRelease` |
| `prefetch.sh` | downloads the release asset into APT's cache |

## Repository maintainers

Rebuild and publish after a new `.deb` is dropped into `debs/` (GitHub Pages
serves this `docs/` directory of the `apt` branch as the site root):

```sh
./termux-apt-repo debs docs termux main --sign --strip-debs
gpg --export 476FC230ECBBFDF0 > docs/key.gpg
# commit docs/ on the apt branch and push; the .deb must be attached to the
# GitHub release whose tag is the Flutter version minus its deb-revision
# suffix (3.47.7-5 -> tag 3.47.7), asset name = the .deb's file name.
# (Pages source config: branch apt, path /docs)
```
