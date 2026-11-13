# Status and progress

What works on the development Mac (MacBookPro9,2: Ivy Bridge, Intel HD 4000), what doesn't yet, and what changed.
Back to the [README](../README.md).

Tested with macOS 27.2.1 (26B131), 2026-11-12. The next image (built 2026-11-13, not yet tested on hardware)
adds the fixes under [What's new](#whats-new).
[The 26B131 repair review](repair-review-26B131.md) records earlier evidence.

**Working**
- Startup to Setup Assistant (screen on about 65 s after the kernel starts); Setup, account
  creation and the automatic login after it.
- Desktop: menu bar, Dock, Finder, Liquid Glass, lock screen, login window.
- Accelerated graphics (HD 4000); hardware video decoding (YouTube 1080p H.264, no dropped frames).
- Wi-Fi: joining from Setup or Settings, and automatic joining of remembered networks,
  also at startup.
- Speakers, brightness, keyboard, trackpad, Bluetooth; AirPlay displays are discovered.
- Safari (including YouTube), Calendar, Terminal, System Settings, Preview, App Store, Books, Calculator,
  Automator.
- iPad-style apps: Stocks (watchlist, interactive charts, news) and News (Today feed with images); Home opens
  (it needs an iCloud account to go further).
- Control Center (Wi-Fi, Bluetooth, AirDrop, Now Playing, Display, Sound) and Notification Center widgets,
  with hao-runtime up to 7415a0b; see Known issues.
- Maps: tiles, search, place cards, current location, satellite (3D globe and street level) and 3D buildings
  (VectorKit's shader library repaired for the old compiler).
- Weather: conditions, hourly, air quality, 10-day, and the precipitation, temperature, air-quality and wind
  maps (native Metal argument buffers and ASTC textures on the HD 4000).
- WebGPU in Safari: compute and rendering on the HD 4000.
- SceneKit: 3D scenes with physically based materials, cube-map environments, shadows and reflections; Preview
  opens SceneKit scenes.
- Photos: import, thumbnails, full-size view.
- Spotlight opens and searches.
- macOS 27's ARM-only programs run on the Intel CPU through hao-runtime, including the
  Geekbench 6 ARM CPU benchmark's workloads.

**Known issues** (2026-11-13)
- Control Center crashes when opened, and new accounts get no widgets: a regression in hao-runtime after
  7415a0b (a Swift value passed to SwiftUI in the wrong layout), being bisected.
- The lock screen can lose the wallpaper: WallpaperAgent crashes under hao-runtime; the next image logs the cause.
- AirDrop: no longer takes mDNSResponder down in the next image; finding nearby devices is not confirmed.
- First login is slow (Setup's language-to-region step, account creation); the next image preloads the
  account template.

**In development**
- Spotlight: local app results. The next image lets macOS download its search data (only dyld is treated as
  an internal install, so the asset service uses Apple's public servers).
- Live Text: the analysis service runs text recognition on the HD 4000; results in Photos
  and Preview are being finished.
- Maps pin shadows: the VectorKit shader repair is built and installed.
- Performance of translated code: general code runs at about 1/2 to 1/3 of native speed;
  vector and floating-point work is profiled, with hao-runtime optimizations lined up.
- Safari WebGL and canvases: WebGPU computes and renders correctly; WebGL drawing and showing WebGL and WebGPU
  canvases on screen are being worked on.
- Freeform: opens; opening a board is being fixed in hao-runtime.
- Maps links to a coordinate (`maps://?ll=`): fixed in hao-runtime, in the next payload.
- RealityKit: needs placement heaps, which the HD 4000's Metal driver lacks.
- Sleep and updates: not tested yet.

## What's new

- **Weather and WebGPU in the image:** the Metal argument-buffer support now installs with the image
  (`golden-gate-metal-argbuf`).
- **Faster first login:** new accounts start from a preloaded account template (app and widget containers,
  widget descriptors, Reminders stores), so the first login doesn't build them.
- **Only dyld is treated as an internal install:** the `ic_csr_internal` boot argument is gone, so the rest of
  macOS behaves as on a customer Mac and can download its Spotlight, Siri and language assets.
- **mDNSResponder:** no longer crashes when AirDrop registers its service (Wi-Fi peer-to-peer compatibility).
- **Background services:** the Kerberos daemon (`gssd`) crash loop is fixed (hao-runtime 397b267).
- **Clean startup:** an Apple logo only, with the OpenCore RELEASE build; developer access and diagnostics are a
  separate opt-in pack.
- **`hao` command:** hardware detection, downloads, verification and image builds from one command
  ([tools/hao](../tools/hao)).
- **Earlier (2026-11-11 to 11-12):** native Metal argument buffers and ASTC textures on the HD 4000; SceneKit's
  shader library repaired; Wi-Fi joining from Setup and automatic joining; Setup's automatic login; Spotlight and
  the `rapportd` and Control Center crash loops fixed; preloaded shader, icon and font caches; recipes run once in
  set-file order.

In development: Control Center and widgets (regression), Live Text, Spotlight's local results, Maps shadows,
faster translated vector and floating-point code.

## Fixes

Each fix has an installation recipe in `patches/` and its source and builder in `tools/`,
`cache/` or the patch's own `build/` folder.

| Fix | Recipe | Source |
|---|---|---|
| Intel kernel, boot and platform repairs | `darwin27-kernel`, `hao-opencore` | [tools/kernel-fixes](../tools/kernel-fixes), [tools/kernel-foundation](../tools/kernel-foundation) |
| Metal libraries and framebuffer | `golden-gate-metal-libraries`, `golden-gate-metal-framebuffer` | [tools/metal-fixes](../tools/metal-fixes), [cache/metallib](../cache/metallib) |
| First-boot hello and Welcome animation | `golden-gate-hello` | [patches/golden-gate-hello](../patches/golden-gate-hello), [cache/metallib](../cache/metallib) |
| Legacy Metal selectors | `metalold-selectors` | [patches/metalold-selectors/build](../patches/metalold-selectors/build) |
| CoreSpotlight API adapter | `golden-gate-corespotlight-compat` | [patches/golden-gate-corespotlight-compat/build](../patches/golden-gate-corespotlight-compat/build) |
| Display and WindowServer adapters | `golden-gate-windowserver-filter` | [tools/display-fixes](../tools/display-fixes), [tools/display-state-shim](../tools/display-state-shim) |
| Userspace, Wi-Fi, Wi-Fi peer-to-peer and pre-login audio adapters | `golden-gate-userspace` | [patches/golden-gate-userspace](../patches/golden-gate-userspace), [tools/audiomxd-fix](../tools/audiomxd-fix) |
| Tahoe CoreWiFi for Tahoe's airportd | native Tahoe donor + `golden-gate-userspace` (compat library) | [tools/tahoe-donors](../tools/tahoe-donors), hao-tools `tools/cache-dylib`, [patches/golden-gate-userspace/src/corewifi](../patches/golden-gate-userspace/src/corewifi) |
| ARM-only programs | `hao-runtime`, `hao-opencore` | [hao-runtime](https://github.com/lythka/hao-runtime) payload |
| Apple code identity of hao and re-signed programs | `golden-gate-userspace` (carrier in Security), `golden-gate-native-identities` | [patches/golden-gate-userspace/src/codeid](../patches/golden-gate-userspace/src/codeid), hao-runtime `libhao_codeid` |
| Boot time: no failing Aux KC build; kernel-manager helper and, before Setup, Setup's system service started early | `hao-opencore` (`auxKCAtBoot=false`), `golden-gate-launchd-warm` | [patches/golden-gate-launchd-warm](../patches/golden-gate-launchd-warm) |
| Live Text (Vision text recognition) | `metal-compiler-compat`, `golden-gate-live-text-shared`, `golden-gate-live-text-service` | [tools/live-text](../tools/live-text), [cache/metallib](../cache/metallib) |
| Metal argument buffers and ASTC textures (HD 4000) | `golden-gate-metal-argbuf` | [tools/metal-argbuf](../tools/metal-argbuf) |
| On-disk Metal libraries without the internal-build flag | `golden-gate-dyld-internal`, `hao-opencore` | [patches/golden-gate-dyld-internal](../patches/golden-gate-dyld-internal) |
| First login: account template and per-account caches | `golden-gate-user-template`, `golden-gate-user-seed` | [cache/seeds](../cache/seeds) |
| macOS 27's own Metal on the HD 4000 (adapter in place of MetalOld) | laptop test only | [tools/metal-adapter](../tools/metal-adapter) |

[patches/README.md](../patches/README.md) describes the recipe format, and
[patches/TRACKER.md](../patches/TRACKER.md) lists every recipe with its stage and
validation state.
