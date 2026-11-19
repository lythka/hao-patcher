# Verify this release

For anyone who downloaded a hao release: check that your files are the ones the release published, unchanged, before you
run anything with `sudo`.

> **v0.0.1 preview: release pending.** The `v0.0.1` tag and its release files are not published yet, so the commands below don't
> work until [v0.0.1](https://github.com/lythka/hao-patcher/releases) appears under Releases.

**v0.0.1 is unsigned, this once** (owner's decision). Each release lists the sha256 of its files in `SHA256SUMS`.
Checking it shows that your files match the release's list. It doesn't prove who made the list, because the list comes
from the same GitHub page as the files. Signed releases come in a later release.

## With hao

```sh
git clone --branch v0.0.1 https://github.com/lythka/hao-patcher && cd hao-patcher/tools/hao
curl -LO https://github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/release.json
curl -LO https://github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/SHA256SUMS
./hao verify-release
```

It checks `release.json` against `SHA256SUMS`, every file present against its sha256, and that the public tag is the
commit `release.json` names. Files you didn't download show `SKIP`. For v0.0.1 it prints `UNSIGNED`. Any `FAIL` exits
with status 4: don't install.

- Files you already downloaded: put them in one folder with `release.json` and `SHA256SUMS`, then `./hao verify-release FOLDER`.
- A USB installer: `/Volumes/HAO/hao/tools/hao/hao verify-release /Volumes/HAO`.

hao refuses a bad signature, and any other release that is unsigned.

## Without hao

Download each release's files into a folder of their own, together with that release's `SHA256SUMS`, then run:

```sh
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Every line must say `OK`. Any `FAILED` means the file is not the one published: don't use it. Files you didn't download are skipped.

| Release | What `SHA256SUMS` covers |
|---|---|
| [hao-patcher v0.0.1](https://github.com/lythka/hao-patcher/releases/tag/v0.0.1) | the source archive |
| [hao-runtime v0.0.1](https://github.com/lythka/hao-runtime/releases/tag/v0.0.1) | the runtime archive |
| [hao-assets 27.2.1-26B131.0](https://github.com/lythka/hao-assets/releases/tag/27.2.1-26B131.0) | `release.json` and every asset |

`release.json` also names each Apple file hao downloads (URL, size, sha256). hao checks those itself and stops if
one differs.

Four files can be checked but not rebuilt from source: the caches harvested on a MacBookPro9,2
([which and why](reproduce.md#what-must-match)).

To rebuild the files yourself, see [Reproduce this release](reproduce.md).
