# ssdvm

Launcher for the builder VM: a headless arm64 macOS virtual machine on Apple's
Virtualization framework, with a shared folder, USB and disk images, a fixed MAC
address and a loopback-only VNC server for scripted control (`pipeline/vm/vmctl`).
`pipeline/vm/start.sh` runs `$VMBIN/ssdvm run VM --mac MAC --share SHARE --usb target.img`;
`VMBIN` defaults to `store/vm/bin`.

## Usage

```
ssdvm install VMDIR RESTORE.ipsw [GB]
ssdvm run VMDIR [--recovery] [--vnc PORT] [--share DIR] [--mac ADDR] [--display WxH[@PPI]]
                [--usb IMG[:ro]]... [--disk IMG[:ro]]...
ssdvm capabilities
```

- `install` makes a new VM from a macOS restore IPSW.
- `run` boots it (recoveryOS with `--recovery`). `--share` exposes a folder as the
  virtiofs tag `ssd`; `--usb` attaches a raw image as a USB disk and `--disk` as a
  virtio disk. `--display` sets the guest's one display (default 1440x900@100).
- `capabilities` prints what the build supports.

## Build

```sh
tools/ssdvm/build.sh store/vm/bin
```

Needs the Xcode command-line tools. The binary is signed ad hoc with only the
`com.apple.security.virtualization` entitlement. Stop the VM before replacing it.

## Verify

`build.sh` ends by running `ssdvm capabilities`, which prints `ssdvm-clone-mac-v1`.
There are no automated tests, since they would need a VM.
