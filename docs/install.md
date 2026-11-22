# Install

For installing hao on a MacBook Pro (13-inch, Mid 2012) from a signed release kit.

> [!WARNING]
> Developer preview. Installation erases the target Mac's internal disk and uses reduced security. Keep a backup and use a Mac that holds nothing you need to protect.

## Requirements

- A Mac, Intel or Apple silicon, running macOS 13 or newer, with internet access and power.
- A USB drive of at least 64 GB; it will be erased.
- About 26 GB free on the build Mac, plus the release's builder tools. hao checks the exact requirement before writing.
- Allow roughly 30–60 minutes to prepare the drive and several hours to install and rebuild the shared cache. Download speed and the target Mac affect the time.

The release supplies its own Python and builder tools. Use a release that contains
`hao`, the builder kit, `release.json`, `SHA256SUMS`, and both signatures. Older
unsigned releases do not support this command.

## Make the USB installer

Replace `<RELEASE>` with the exact release name from the hao-assets release page:

```sh
curl -fsSL https://github.com/lythka/hao-assets/releases/download/<RELEASE>/hao -o hao && sh hao
```

hao verifies the signed release and its downloads, then asks which Mac and which
USB drive to use. Confirm the drive by name. Your password is requested once to
write it. Keep the work folder; running the same command again reuses verified downloads.

Keep the finished installer with you until installation. Its manifest detects
changed files before installation.

## Install on the MacBook Pro

1. Plug in the USB installer. Hold Option while starting the Mac and choose **EFI Boot**.
2. In Recovery, open Utilities > Terminal and run:

   ```sh
   /Volumes/HAO/install
   ```

3. Choose the internal disk and confirm. Leave the Mac connected to power while
   installation and the shared-cache rebuild finish, then follow the restart instructions.

## Report a problem

On the build Mac, run `sh hao report`. In Recovery, run `/Volumes/HAO/report`;
on the installed Mac, run `hao report`. Attach the report to a
[GitHub issue](https://github.com/lythka/hao-patcher/issues/new/choose).

To inspect the build Mac's readiness, run `sh hao doctor`.
