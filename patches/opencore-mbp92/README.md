# opencore-mbp92

The MacBookPro9,2's part of the image's OpenCore configuration (pack `mbp92`): its early Tahoe display and wired-network
kexts and the codeless kext that keeps its Bluetooth HID-proxy devices off the HID dext. Split out of [hao-opencore](../hao-opencore/README.md) (r67) unchanged; it runs just before that recipe, on the
EFI partition that `pipeline/stages/build-efi.py` made from OCLP's model data.

## How it works

`apply.sh` runs `src/early_kexts.py`, which injects the Tahoe drivers listed in `src/early-kexts.txt` (Capri
framebuffer, HD 4000, BCM5701 Ethernet, the same set as [rec-display-ethernet](../rec-display-ethernet/README.md)) and
their dependencies, in OSBundleLibraries order, into `EFI/OC/Kexts` and `Kernel.Add`, only when neither the Boot nor the
System collection has them. Drivers already in the System collection stay there: injecting a second copy makes the
kernel discard the collection's copy while other drivers still call into it.

On every recorded build all three are in a collection, so this step adds nothing.

`src/hid_proxy.py` then writes `HIDProxyKernelMatch.kext` (its only file, Contents/Info.plist, is
`src/HIDProxyKernelMatch-Info.plist`) to `EFI/OC/Kexts` and adds it to
`Kernel.Add` (fixes #79). At boot the BCM20702 Bluetooth controller shows two HID-proxy devices (05ac:820a keyboard,
05ac:820b mouse) that leave when Bluetooth switches to HCI mode. The AppleUserHIDDrivers dext matched them (its USB HID
personalities have IOProbeScore 1), and their termination never completed: on every recorded 26B131 and 25G241 boot
ioreg shows both `inactive, busy 2` under the BRCM20702 hub, and EHC1 busy for the whole boot. EHCI's change to power
state Off (`AppleUSB20HostController::lowerOnePowerStateTo(0)`) waits, re-checking each second, until every device on
the bus has terminated, so a wake that powered EHCI off hung there and panicked after 180 s ("Wake transition timed
out ... IOUSBHostFamily"). The kext's two personalities (IOProbeScore 10000, the device ids, configuration 1,
interface 0) give both to the kernel's `IOUSBHostHIDDevice`, which the Boot collection already links.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/opencore-mbp92/tests`.
- The build log prints `Tahoe driver already supplied by collection or EFI:` for each of the three, or
  `Early Tahoe injection:` with the kext's version and sha256; then `HID proxy: HIDProxyKernelMatch.kext in Kernel.Add`.
- On the Mac: `ioreg -p IOUSB -w0 | grep -E 'EHC1|1d1811|1d1812'` shows no `inactive` device and EHC1 `busy 0`;
  EHC1 powered off and on through its `UsbForcePower` property, and a sleep and wake, both complete.

## Validation

The fb6542df image builds print `Tahoe driver already supplied by collection or EFI:` for the framebuffer, HD 4000 and Ethernet drivers. Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r5): boots to the desktop; a hands-free install of the fb6542df release kit on a second MacBookPro9,2 boots with the network up. The sleep/wake panic (#79) stays open.

## Revisions
- r6 2026-11-19 Validated r5 on hardware (Validation). Nothing it installs changes.
- r5 2026-11-17 HIDProxyKernelMatch.kext removed (r4): on hardware the Bluetooth HID-proxy devices stay stuck terminating under the kernel HID driver too, so the kext changed nothing. Five sleep/wake cycles woke without a panic with or without it (#79 stays open: the panic needs EHC1 to go to Off).
- r4 2026-11-17 Adds HIDProxyKernelMatch.kext (codeless) to Kernel.Add: the Bluetooth HID-proxy devices go to the kernel HID driver, so EHCI can power off on wake (fixes #79).
- r3 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r2 2026-11-17 Declares its scope (`requires`, docs/requires.md): this Mac's display and Ethernet kexts. Nothing it installs changes.
- r1 2026-11-16 New: hao-opencore's early Tahoe kext step (src/early_kexts.py, src/early-kexts.txt and its test), unchanged, in pack mbp92.
