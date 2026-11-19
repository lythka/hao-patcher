# Reproduce this release

For anyone checking a release's source: rebuild its files from the public tag and compare them byte for byte.

> **v0.0.1 preview: release pending.** The `v0.0.1` tag and its release files are not published yet, so the commands below don't
> work until [v0.0.1](https://github.com/lythka/hao-patcher/releases) appears under Releases.

## You need

- The build Mac from [Install](install.md#requirements): any Mac on its normal macOS, Xcode with its Metal toolchain,
  Python 3.14 (python.org's build), about 26 GB free, an internet connection.
- Nothing from this project beyond its public repositories at the tag.
- For the hao-runtime archive only: macOS 27 with Xcode 27.2 (a macOS 27 virtual machine is enough). It reproduces
  only there; the rest of the release reproduces on the build Mac.

## Commands

Each of this project's files is rebuilt on its own from a fresh clone of the tag, with nothing else from us:

```sh
git clone --branch v0.0.1 https://github.com/lythka/hao-patcher && cd hao-patcher
python3.14 -I tools/asset-build/asset-build list          # the files you can rebuild
python3.14 -I tools/asset-build/asset-build ROW OUT       # rebuild one into the new folder OUT
```

`asset-build` prints the rebuilt file's sha256 and exits 1 if it differs from the release's. One file, `codeid_carrier`,
also needs a clean hao-runtime checkout: add `--hao-runtime CHECKOUT`.

hao-runtime's archive is rebuilt in hao-runtime, on macOS 27 with Xcode 27.2, with `tools/hao-runtime-from-source`.
Its inputs come only from Apple's macOS 27.2.1 IPSW (URL, size and sha256 in [Build inputs](inputs.md)):

1. Unzip four images from the IPSW. `BuildManifest.plist` names them: `OS` (the system image), `Cryptex1,SystemOS`,
   `Cryptex1,AppOS` and `Cryptex1,RosettaOS`.
2. Decrypt the two `.aea` images with [ipsw](https://github.com/blacktop/ipsw) (`brew install ipsw`;
   `ipsw fw aea FILE.aea --output DIR`). It gets the key from Apple's public key service.
3. Mount each image read-only: `hdiutil attach -readonly -nobrowse -mountpoint M IMAGE.dmg`, at `ROOT`, `OSX`, `APPX`
   and `ROS`.
4. From a clone of hao-runtime at the tag:

   ```sh
   tools/hao-runtime-from-source --cryptex OS=OSX --cryptex App=APPX ROOT ROS/System/Library/dyld \
       hao-runtime-VERSION-public.tar.gz OUT
   ```

   It prints `identical` and exits 0. The rebuild takes about 5 minutes after the download.

Before each release is published, we run the same rebuild in a clean macOS virtual machine and refuse to publish unless
every file is identical.

## What must match

Every file in each release's `SHA256SUMS` and the `release.json`, byte for byte, except these four:

| File in hao-assets | What it is |
|---|---|
| `user_template_golden_gate.tar` | Harvested cache: the new-account template |
| `font_registry_golden_gate.tar` | Harvested cache: the font registry |
| `shader_caches_golden_gate.tar` | Harvested cache: the HD 4000 shader caches |
| `icon_caches_golden_gate.tar` | Harvested cache: the icon caches |

Nothing else may differ. You can check all four against `SHA256SUMS` ([Verify this release](verify.md)), but not
rebuild them.

### The harvested caches

They make the MacBook Pro (Mid 2012)'s first start-up faster. macOS made them itself on a MacBookPro9,2 running hao
(the shader caches when apps first drew on the HD 4000, the others at first start-up and login); we copied them from
that Mac. Before release each was scanned for personal data (home folder, account, host name, serial number, IP and MAC
addresses, e-mail addresses, in every archive member and record): no hits, apart from the public contact addresses in
the copyright notices of fonts that ship with macOS.

### The typed boundary tables

hao-runtime's tables of how Swift values cross between ARM and Intel code are public source at the tag
(`src/swiftwalk_gen.inc`, `src/swiftguest_gen.inc`, one set per supported macOS build). libhao builds from them, so it
reproduces like everything else. The tool that generates the tables is not public, so you can't regenerate the tables
themselves from the public tree.

## What comes from Apple

No Apple file is published. `release.json` names each one (the macOS 27.2.1 IPSW, the macOS 26.7.3 installer and the
other inputs in [Build inputs](inputs.md)) by its URL, size and sha256. hao downloads them from Apple and stops if a size
or sha256 differs. The values are in the input rows under `pipeline/inputs/` at the tag; they change only with a new
release.

To check a release without rebuilding it, see [Verify this release](verify.md).
