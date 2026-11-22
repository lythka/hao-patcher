# Install

For anyone installing hao on a MacBook Pro (13-inch, Mid 2012): what you need, the four steps, and what to send if something
breaks.

> [!WARNING]
> Developer preview. Installing erases the MacBook Pro's internal disk.

Making the USB installer yourself is in progress: parts made on another macOS version may differ in signature; the
graphics parts need Xcode's Metal toolchain.

hao installs macOS 27 on an Intel Mac. You make a USB installer on any Mac, then install from it on the
MacBook Pro.

## Requirements

| Part | Requirement |
|---|---|
| Target | MacBook Pro (13-inch, Mid 2012), the first supported model. Its internal disk must hold at least 86 GB (the installed system takes 85.9 GB) and is erased. |
| Build Mac | Any Mac, Intel or Apple silicon, on its normal macOS, with no SIP or AMFI changes. It needs Xcode with its Metal toolchain and Python 3.14 (python.org's build: run its Install Certificates once). `sudo ./hao` runs only that Python, and only while it is root's alone; `./hao doctor` checks this and prints the fix. |
| USB drive | 64 GB or larger. It is erased. |
| Free disk | About 26 GB on the build Mac at the peak. hao downloads about 3.6 GB and reads about 35 GB of Apple's IPSW and installer in parts. |
| Time | On the build Mac about 25-30 minutes when Apple's servers are fast (over an hour when they are slow); the install about 1-2 hours, most of it one quiet step. |
| Network | Internet on the build Mac (the downloads come from Apple and this project's releases). |

## Install

**Reduced security.** hao is a developer preview with reduced security: it is not as secure as macOS on a supported Mac. System Integrity Protection is partly off, Apple Mobile File Integrity (code-signature enforcement) is relaxed, and the sealed system volume and Secure Boot are off. Install it only on a Mac that holds nothing you need to protect.

1. Get hao and the release's file list:

   ```sh
   git clone --branch v0.0.1 https://github.com/lythka/hao-patcher && cd hao-patcher/tools/hao
   curl -LO https://github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/release.json
   curl -LO https://github.com/lythka/hao-assets/releases/download/27.2.1-26B131.0/SHA256SUMS
   ./hao verify-release
   ```

   `verify-release` checks the file list against the release and the clone against its tag. Any `FAIL`: stop and don't
   run `sudo`. v0.0.1 is unsigned, so it also prints `UNSIGNED` ([Verify this release](verify.md)).

2. Plug in the USB drive and run:

   ```sh
   sudo ./hao create-installer --link-on-target
   ```

   Choose the USB drive and confirm. This erases the drive. hao then downloads what the release lists, makes the parts
   that must be made on this Mac, and writes the USB installer.

   Keep the USB installer with you between making it and installing: anyone who can change it can change what it installs. hao install checks it against its manifest first, which catches damage and casual edits.
3. On the MacBook Pro, hold Option at startup and choose **EFI Boot**. Expect a silent pause of several minutes
   before the Apple logo. In the Recovery, open Utilities > Terminal and run `/Volumes/HAO/install`.
4. After the first start-up, run once:

   ```sh
   sudo /usr/libexec/hao --aot
   ```

   Until then, apps take longer to open the first time.

## Rebuilt-cache candidates

A candidate using profile `beta-v0.0.1` downloads about 27 MB of extra builder inputs when making the stick.
The target then rebuilds and verifies the cache during installation. Keep it connected to power and allow roughly
25–60 extra minutes on Ivy Bridge (estimated, not measured on that hardware). The plan shows the scratch-space
requirement. A failed build can resume; a failure during cache replacement requires a fresh install.
These candidates require boot and hardware validation before release.

## Logs

If something doesn't work, save a report and attach it to a
[GitHub issue](https://github.com/lythka/hao-patcher/issues/new/choose):

```sh
./hao report --output report.txt
```

The report leaves out the serial number, user names and Wi-Fi names.

## Build from source

To build hao and its kit yourself, follow [the developer guide](building.md):

```sh
git clone https://github.com/lythka/hao-patcher && cd hao-patcher
sudo tools/hao/hao create-installer --link-on-target
```
