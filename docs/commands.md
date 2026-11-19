# Commands

For anyone installing or running hao: what each `hao` command does, where it runs, and its options.

`hao` is the one command you type, on every Mac involved: the Mac that makes the USB installer, the USB installer's
Recovery, and the Mac running macOS 27 with hao. `hao` on its own lists the commands for where it runs, with the hao
and macOS versions; `hao help --all` adds the developer commands; `hao COMMAND --help` gives a command's options.

| Command | Runs on | What it does |
|---|---|---|
| [`verify-release`](#hao-verify-release) | anywhere | checks a release is the one the project signed: run it before `create-installer` |
| [`create-installer`](#hao-create-installer) | any Mac on its own macOS | makes the USB installer (asks for your password to write the drive) |
| [`install`](#hao-install) | the USB installer's Recovery | installs macOS 27 on this Mac |
| [`doctor`](#hao-doctor) | any Mac on its own macOS | checks this Mac is ready to make the USB installer |
| [`status`](#hao-status) | anywhere | one screen about hao on this Mac |
| [`report`](#hao-report) | anywhere | one file with what a developer needs, for a GitHub issue |
| [`verify`](#hao-verify) | a Mac with hao | checks every installed pack file against its manifest |
| [`explain`](#hao-explain) | a Mac with hao, or the Mac that made the installer | where a file comes from and every edit to it |
| [`run`](#hao-run) | a Mac with hao | runs an ARM program with the runtime's diagnostics |
| [`aot`](#hao-aot) | a Mac with hao | the runtime's ahead-of-time caches |

A command started where it doesn't belong stops and says where it runs (exit code 5).

## hao verify-release

```sh
./hao verify-release [vX.Y.Z | FOLDER]
```

Checks that the release files are the ones the project signed, before anything runs with your password: the signatures
on `SHA256SUMS` and `release.json` against the keys hao carries (`tools/hao/data/release-keys.pub`), every file present
against its sha256 (with the downloads in `~/Library/Caches/hao`), and that the public tag is the commit `release.json`
names. With `vX.Y.Z` it downloads that release's signed files first; with a folder it checks the files there, or a USB
installer (`/Volumes/HAO`). On the USB installer, plain `hao verify-release` checks that drive. One line per check:
PASS, FAIL, or SKIP with the reason (SKIP means not checked). Any FAIL exits 4: don't go on.

| Option | |
|---|---|
| `--offline` | no network: the tag is not looked up (`vX.Y.Z` needs the network) |
| `--cache DIR` | the downloads to check as well |

## hao create-installer

```sh
sudo ./hao create-installer [/dev/diskN] [--model MODEL | --profile FILE] [--unproven] [--dry-run]
```

Run `./hao verify-release` right before it, as yourself: `sudo` runs hao's files as root, so check them first.
`sudo ./hao` runs only python.org's Python 3.14, and only while it is root's alone (`./hao doctor` checks this and
prints the fix). It refuses a release whose signature does not hold (exit 4).

Download `SHA256SUMS`, `release.json` and their `.sig` files and put them beside hao. The release key is
`SHA256:HPvLz28RozOa/bJ+7bJ0ha4s27LBEjQ/B3TxSqQCdek`; a missing or wrong signature stops hao (exit 4).

Asks which Mac the installer is for (a proven model, or a profile file from `hao detect`) and which USB drive to use,
and needs its name typed before erasing it. Then it downloads what the release pins (each file checked against its
sha256), prepares the packs and the kit, and writes the USB drive: OpenCore and the Recovery on `HAOBOOT`, hao and the
kit on `HAO`. It ends with what to do on the Intel Mac.

| Option | |
|---|---|
| `/dev/diskN` | the USB drive (asked when left out) |
| `--model MODEL` | the Mac to build for, for example `MacBookPro9,2` |
| `--profile FILE` | a profile from `hao detect`, for a Mac that is not a proven model |
| `--unproven` | go on with a Mac hao has not proven (without it, exit 5); kept in the install record |
| `--dry-run` | ask the questions and show the plan; write nothing |
| `--confirm DISK` | the typed confirmation, given ahead (scripts) |
| `--release FILE` | the release.json to download from (default: the one beside hao) |
| `--cache DIR` | where downloads are kept |
| `--minimum-bytes N` | the smallest USB drive accepted |
| `--keep-kit` | keep the staged kit on this Mac (default: removed once the USB installer is written and checked) |

## hao doctor

```sh
hao doctor [--for create-installer | ci | release] [--offline]
```

Read only. Checks the Python running hao (python.org's 3.14 on the Mac that makes the installer; `sudo ./hao` runs only that Python, and only while it is root's alone), Xcode and its Metal
toolchain, the macOS version, the free disk against the release's peak, that each download URL answers, and that each
download has a mirror. A failing check names its fix; any failure exits 5. `--for release` also requires the exact Xcode
version and a mirror for every download.

| Option | |
|---|---|
| `--for create-installer \| ci \| release` | the checks for that use (default `create-installer`) |
| `--offline` | skip the network checks |
| `--release FILE` | the release.json to check against (default: the one beside hao) |
| `--cache DIR` | where downloads go (default `~/Library/Caches/hao`) |

## hao install

```sh
/Volumes/HAO/install [/dev/diskN] [--dry-run] [--resume FOLDER] [--no-restart]
```

Run in Terminal in the USB installer's Recovery. Checks this Mac against the release, asks which disk, and needs
`ERASE` typed. Then it installs in seven steps: lay out the disk, copy macOS 27, add Intel support, link the kernel,
apply the packs, seal, and make it bootable. It ends with a summary: the time it took, the files and edits the packs
brought, the kernel repairs, and the log.

| Option | |
|---|---|
| `/dev/diskN` | the internal disk (asked when left out) |
| `--dry-run` | check the Mac and the disk and show the steps; write nothing |
| `--resume FOLDER` | continue a failed install at the step it stopped in (`/Volumes/HAO/hao-install-DATE`), when that step can run again |
| `--no-restart` | stay in the Recovery when the install is done (default: restart after a 10 s countdown) |
| `--model MODEL`, `--profile FILE`, `--unproven` | as for `create-installer` |
| `--confirm WORD` | the typed confirmation, given ahead (scripts) |

## hao status

```sh
hao status [--log json]
```

Read only. hao's release, where it runs, macOS and its build, the Mac, SIP and the boot arguments, the install record
and live patches, the ahead-of-time caches, and the last command that stopped.

## hao report

```sh
hao report [--output FILE]
```

Writes one text file for a GitHub issue: hao's version, this Mac, its `hao detect` profile, the last command's log and
the install record. Serial numbers, hardware UUIDs, computer, user and Wi-Fi names, e-mail and hardware addresses are
removed before it is written. From the USB installer use `/Volumes/HAO/report`, which writes into the drive's
`reports` folder.

## hao verify

```sh
hao verify [ROOT] [--manifests FILE|DIR]...
```

Checks every installed pack file against its manifest and lists the ones that differ (exit code 4 when any does).

## hao explain

```sh
hao explain [PATH | NAME | RECIPE]
```

Where a file comes from (Apple, OCLP, a Tahoe donor or hao) and every edit made to it, with its bytes and its reason.

## hao run

```sh
hao run [-v | -vv] [--interp] [--stats] [--allow-unsigned] PROGRAM [ARGS]
```

ARM-only programs run when you start them as usual; `hao run` is for the runtime's diagnostics: `-v` and `-vv` print
what it does, `--stats` prints counts at exit, `--interp` uses the interpreter only, `--allow-unsigned` runs an
unsigned program. The program's output and exit code are its own. Under `sudo`, `-v` is ignored.

## hao aot

```sh
sudo hao aot                 # build the caches of the programs the release ships; valid ones are skipped
hao aot --status             # valid, missing and stale caches, and the start-up job
sudo hao aot --background    # the same build, detached, at background priority
sudo hao aot --clear         # remove the caches; the start-up job builds them again
```

The runtime's ahead-of-time caches make ARM programs start faster. They are in `/private/var/db/hao`, which every user,
root and the system's services read, so building and removing them needs your password. A start-up job (`hao.aot`)
builds them once after the first start-up and again after each runtime update; `hao status` shows how far it is.

## Output

Every command takes the same detail levels:

| Flag | Shows |
|---|---|
| `--quiet` | progress and the questions only |
| (none) | each step and its check |
| `-v` | one line per file or recipe |
| `--show-edits` | every edit, with its origin, bytes and reason, and the hardware detection |
| `--log json` | one JSON event per line and nothing else |

On a terminal hao draws with [Rich](https://github.com/Textualize/rich): the step being worked on stays at the bottom
with the overall progress, and the run ends with a summary. In a pipe or a log, or with `NO_COLOR` set or
`TERM=dumb`, it prints the same steps as plain lines. Everything the tools hao runs print goes to the command's log
(`~/Library/Logs/hao`, `/Library/Logs/hao` as root, or `logs` on the USB installer), which a failure names.

| Variable | |
|---|---|
| `NO_COLOR` | no colour and no redrawn lines |
| `HAO_THEME=dark\|light` | the colours for the terminal's background (otherwise hao asks the terminal) |
| `HAO_ASCII=1` | ASCII spinners and bars |
| `HAO_RECORD=FILE.svg` | save the finished screen as SVG (or HTML for `.html`) |

## Exit codes

| Code | |
|---|---|
| 0 | done |
| 1 | stopped safely (the report says what was left untouched) |
| 2 | the command line is wrong |
| 3 | not enough space, or the wrong drive |
| 4 | a file did not match its sha256 |
| 5 | refused: the wrong Mac or place, or it needs your password |
