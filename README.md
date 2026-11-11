# hao-patcher

hao-patcher builds disk images of **macOS 27 (Golden Gate) for Intel x86_64 Macs**.
The current build is macOS 27.2.1 (26B131).

macOS 27 does not support Intel Macs, so hao-patcher builds the image in layers. It
installs Golden Gate's own System and Data, then adds the Intel kernel extensions,
drivers and boot files that macOS 26 (Tahoe) still ships. It adds legacy hardware
support from OpenCore Legacy Patcher, a set of compatibility fixes, and
[hao-runtime](https://github.com/lythka/hao-runtime), which runs Golden Gate's
ARM-only programs on the Intel CPU.

![macOS 27 desktop and Control Center on an Intel Mac](docs/images/desktop-intel.jpg)

## Risks

Use hao-patcher only on a **dedicated test Mac** whose contents you can lose.

- Installing an image (`ssdimg push`, or restoring it any other way) overwrites the
  whole target disk.
- The image weakens macOS security on purpose: AMFI is off (`amfi_get_out_of_my_way=1`);
  SIP is lowered and authenticated root is off (`csr-active-config` 0x803) with Secure
  Boot disabled; `ic_csr_internal=1` makes the kernel report the Apple-internal SIP flag;
  System programs are re-signed ad hoc; OpenCore loads third-party kexts, including
  HaoExec and IntelCommpage.
- hao-runtime changes System files in place and has no uninstaller; see its
  [Read first](https://github.com/lythka/hao-runtime#read-first). Its binary
  translation is experimental and can crash programs or damage their data.
- To recover: hold Option at startup and boot another disk, or Internet Recovery
  (Command-Option-R); reset NVRAM (Command-Option-P-R); then erase the disk, which removes
  the OpenCore EFI, and reinstall a supported macOS or restore a backup. hao is part of
  the image's System snapshot and cannot be removed on its own: deleting its files breaks
  launchd.

## Status

Experimental. Development and testing happen on a 2012 13-inch MacBook Pro
(MacBookPro9,2: Ivy Bridge, Intel HD 4000); other Intel Macs are untested.

Tested with macOS 27.2.1 (26B131), 2026-11-11.
[The 26B131 repair review](docs/repair-review-26B131.md) records earlier evidence.

**Working**
- Startup to Setup Assistant (screen on about 65 s after the kernel starts); Setup, account
  creation and the automatic login after it.
- Desktop: menu bar, Dock, Finder, Liquid Glass, lock screen, login window.
- Accelerated graphics (HD 4000); hardware video decoding (YouTube 1080p H.264, no dropped frames).
- Wi-Fi: joining from Setup or Settings, and automatic joining of remembered networks,
  also at startup.
- Speakers, brightness, keyboard, trackpad, Bluetooth; AirPlay displays are discovered.
- Safari (including YouTube), Calendar, Terminal, System Settings, Preview.
- Control Center (Wi-Fi, Bluetooth, AirDrop, Now Playing, Display, Sound).
- Notification Center and widgets.
- Maps: tiles, search, place cards, current location.
- Photos: import, thumbnails, full-size view.
- Spotlight opens and searches.
- macOS 27's ARM-only programs run on the Intel CPU through hao-runtime, including the
  Geekbench 6 ARM CPU benchmark's workloads.

**In development**
- Spotlight: local app results (the cause is found: its search data isn't downloaded).
- Live Text: the analysis service runs text recognition on the HD 4000; results in Photos
  and Preview are being finished.
- Maps pin shadows: the VectorKit shader repair is built and installed.
- Faster first login for new accounts: a preloaded account template is built.
- Performance of translated code: general code runs at about 1/2 to 1/3 of native speed;
  vector and floating-point work is profiled, with hao-runtime optimizations lined up.
- Weather: its crash is fixed in hao-runtime and its interface units.
- Sleep and updates: not tested yet.

## Requirements

- **Target:** an Intel x86_64 Mac (developed on MacBookPro9,2).
- **Build host:** an Apple silicon Mac with the Xcode command-line tools,
  Python 3.14 and an arm64 macOS virtual machine for disk operations.
- **Inputs:** the macOS 27.2.1 (26B131) IPSW, the Tahoe 25G342 installer and Kernel
  Debug Kit, OCLP support files and a hao-runtime payload, which you build from
  hao-runtime 790720d until a release download is available.
  [Build inputs](docs/inputs.md) lists downloads, checksums and the payload build.

Apple's macOS inputs (IPSW, installer, Kernel Debug Kit) are not included: you supply
them, and the builder writes its images to your machine. The repository does carry some
Apple open-source code under the APSL 2.0 and third-party headers under their own
licenses; [NOTICE.md](NOTICE.md) lists them. There is no end-user installer yet.

## Building

```sh
git clone https://github.com/lythka/hao-patcher.git
cd hao-patcher
# Prepare the inputs (docs/inputs.md), then:
python3.14 -I pipeline/golden-run pipeline/assemblies/mbp92-26B131.json \
  --repo /path/to/new-image-repository
```

[Building an image](docs/building.md) covers input preparation, what the build does
and how to test the result in QEMU before installing it on hardware. The build also
exports a matching OpenCore EFI, generated from the pinned OCLP and OpenCore sources.
A stock EFI regenerated by OCLP lacks this project's additions.

`mbp92-26B131.json` builds 27.2.1 with the `golden-gate-26B131` set. The development build,
`dev-26B131.json`, uses `dev-bs-test`: the same recipes plus `dev-access` (root SSH for
testing) and hao's diagnostic statistics. Every set file says, entry by entry, what each recipe
adds and why, in run order ([layer order](docs/building.md#layer-order)). The build
refuses a set that breaks that order.

## What's new

- **Wi-Fi:** Setup's join dialog works, and remembered networks join automatically
  (Tahoe's CoreWiFi).
- **Login:** Setup's automatic login after creating an account works.
- **Spotlight:** no longer crashes.
- **Background services:** `rapportd` and Control Center crash loops fixed (hao-runtime
  790720d).
- **Faster first boot:** preloaded shader, icon and font caches (optional: builds without them skip them, see
  [optional cache recipes](docs/building.md#optional-cache-recipes)).
- **Build:** recipes run once, in set-file order ([layer order](docs/building.md#layer-order)).

In development: Live Text, a fast first login for new accounts, Spotlight's local results,
Maps shadows, faster translated vector and floating-point code.

## Fixes

Each fix has an installation recipe in `patches/` and its source and builder in `tools/`,
`cache/` or the patch's own `build/` folder.

| Fix | Recipe | Source |
|---|---|---|
| Intel kernel, boot and platform repairs | `darwin27-kernel`, `hao-opencore` | [tools/kernel-fixes](tools/kernel-fixes), [tools/kernel-foundation](tools/kernel-foundation) |
| Metal libraries and framebuffer | `golden-gate-metal-libraries`, `golden-gate-metal-framebuffer` | [tools/metal-fixes](tools/metal-fixes), [cache/metallib](cache/metallib) |
| First-boot hello and Welcome animation | `golden-gate-hello` | [patches/golden-gate-hello](patches/golden-gate-hello), [cache/metallib](cache/metallib) |
| Legacy Metal selectors | `metalold-selectors` | [patches/metalold-selectors/build](patches/metalold-selectors/build) |
| CoreSpotlight API adapter | `golden-gate-corespotlight-compat` | [patches/golden-gate-corespotlight-compat/build](patches/golden-gate-corespotlight-compat/build) |
| Display and WindowServer adapters | `golden-gate-windowserver-filter` | [tools/display-fixes](tools/display-fixes), [tools/display-state-shim](tools/display-state-shim) |
| Userspace, Wi-Fi and pre-login audio adapters | `golden-gate-userspace` | [patches/golden-gate-userspace](patches/golden-gate-userspace), [tools/audiomxd-fix](tools/audiomxd-fix) |
| Tahoe CoreWiFi for Tahoe's airportd | native Tahoe donor + `golden-gate-userspace` (compat library) | [tools/tahoe-donors](tools/tahoe-donors), [tools/cache-dylib](tools/cache-dylib), [patches/golden-gate-userspace/src/corewifi](patches/golden-gate-userspace/src/corewifi) |
| ARM-only programs | `hao-runtime`, `hao-opencore` | [hao-runtime](https://github.com/lythka/hao-runtime) payload |
| Apple code identity of hao and re-signed programs | `golden-gate-userspace` (carrier in Security), `golden-gate-native-identities` | [patches/golden-gate-userspace/src/codeid](patches/golden-gate-userspace/src/codeid), hao-runtime `libhao_codeid` |
| Live Text (Vision text recognition) | `metal-compiler-compat`, `golden-gate-live-text-shared`, `golden-gate-live-text-service` | [tools/live-text](tools/live-text), [cache/metallib](cache/metallib) |

[patches/README.md](patches/README.md) describes the recipe format, and
[patches/TRACKER.md](patches/TRACKER.md) lists every recipe with its stage and
validation state.

## Repository layout

| Path | Contents |
|---|---|
| `pipeline/` | Image assembly, input rows, targets and patch sets |
| `patches/` | Installation recipes, one folder per fix |
| `tools/` | Fix builders, input preparation, builder VM, image and QEMU tools |
| `cache/` | Metal library and shader generators |
| `docs/` | Architecture, inputs, build guide and per-build repair reviews |
| `store/` | Your local inputs, images and build records (ignored by Git) |

See [architecture](docs/architecture.md) for how the pieces fit together and
[CONTRIBUTING.md](CONTRIBUTING.md) for tests and conventions.

## Credits

Built on [OpenCore Legacy Patcher](https://github.com/dortania/OpenCore-Legacy-Patcher),
[OpenCore](https://github.com/acidanthera/OpenCorePkg),
[Lilu](https://github.com/acidanthera/Lilu),
[MacKernelSDK](https://github.com/acidanthera/MacKernelSDK),
[MetallibSupportPkg](https://github.com/dortania/MetallibSupportPkg),
[Apple's XNU sources](https://github.com/apple-oss-distributions/xnu) and
[QEMU](https://www.qemu.org/). Each tool's NOTICE.md lists the third-party source it carries.

This product includes software developed by Dortania and the OpenCore Legacy
Patcher contributors.

## License

Original code is available under the [MIT license](LICENSE). Third-party code and
Apple components keep their own terms; see [NOTICE.md](NOTICE.md).
