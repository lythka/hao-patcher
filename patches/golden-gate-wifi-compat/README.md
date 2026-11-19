# golden-gate-wifi-compat

The IO80211 and WiFiPeerToPeer compatibility libraries in front of OCLP's Modern Wireless wrappers (pack
`wifi-broadcom-modern`). Split out of golden-gate-userspace (r84) unchanged: the same two steps, the same bytes. It runs
right after golden-gate-userspace.

## Inputs

- `io80211_compat`: the IO80211 compatibility library, built and checked for one build by golden-gate-userspace's
  `src/io80211/make`.
- `wifip2p_compat`: the WiFiPeerToPeer compatibility library, built and checked by golden-gate-userspace's
  `src/wifip2p/make`.

The source of both stays in golden-gate-userspace for now (its CoreWiFi library shares their coverage and method passes).
Its README says what each library adds and how `make` checks it.

## How it works

`src/kc-hook.sh` runs `src/install.py` on the System volume. For each library it:
- renames OCLP's wrapper, which OCLP's hardware overlay installed at the framework's own name, to `IO80211OCLP.dylib` or
  `WiFiPeerToPeerOCLP.dylib` (install name and file), signed again ad hoc with its own identifier;
- installs the compat library, which re-exports that file, under the framework's own name, with the wrapper's file
  flags (`restricted`).

Without them, airportd stops with SIGBUS (IO80211: 7 data exports 27's callers use as code, and one missing function), and
coreaudiod, AirPlayXPCHelper, ControlCenter and sharingd abort in libobjc (WiFiPeerToPeer: 36 Wi-Fi Aware classes).

The two renames are declared in `edits/io80211-oclp.json` and `edits/wifip2p-oclp.json` (matchers on OCLP 13.7.2-25's
files); the compat libraries are whole files from their rows.

## Verify

- `python3.14 -I -B -m unittest discover -s patches/golden-gate-wifi-compat/tests`: the declarations match install.py,
  and install.py's rename steps are golden-gate-userspace's. With `WIFI_COMPAT_OCLP_IO80211` and
  `WIFI_COMPAT_OCLP_WIFIP2P` set to OCLP 13.7.2-25's two wrapper files, each spec's signed output equals the rename and its
  expect hash.
- The build log prints `IO80211: compat library re-exports the original as IO80211OCLP.dylib` and the same for
  WiFiPeerToPeer.
- On the Mac: Wi-Fi joins a network, and airportd, coreaudiod and sharingd stay up.

## Validation

Laptop 2026-11-19, a MacBookPro9,2 with image 8305fdd (26B131, built from fb6542df with r3): the build log prints the IO80211 compat line; Wi-Fi joins a 5 GHz network (join rc 0, an address on en1 after 12 s, https 200), and airportd, sharingd and coreaudiod keep the same processes afterwards. OCLP's IO80211OCLP.dylib and WiFiPeerToPeerOCLP.dylib sit behind the compat libraries.

## Revisions
- r4 2026-11-19 Validated r3 on hardware (Validation). Nothing it installs changes.
- r3 2026-11-17 Status product (was experiment): it ships in the release profiles. Declaration only; `validated` stays false until a boot proves the re-cut. Nothing it installs changes.
- r2 2026-11-17 Declares its scope (`requires`, docs/requires.md): compat rows for both builds. Nothing it installs changes.
- r1 2026-11-16 New: golden-gate-userspace's IO80211 and WiFiPeerToPeer steps, files and edit specs, unchanged, in pack wifi-broadcom-modern.
