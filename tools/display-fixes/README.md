# display-fixes

Builds, prepares and installs the two native display repairs for Golden Gate
(26A428, 26B131) on MacBookPro9,2, outside the image build:

- the display-state adapter, [`tools/display-state-shim`](../display-state-shim/README.md);
- the WindowServer display-inversion and deferred software-backdrop filter,
  [`patches/golden-gate-windowserver-filter`](../../patches/golden-gate-windowserver-filter).

Each repair keeps its own per-build framework UUID table and process checks. The image build
installs the same libraries through `patches/golden-gate-userspace`; this tool is
for building the libraries and for installing them into an offline System by
hand.

## Usage

Requires macOS 26 or later, Python 3.12 or later, the Xcode command-line tools and
an SDK with QuartzCore. On Apple silicon, Rosetta runs the signed x86_64 test
programs. Run from this folder:

```sh
python3 -I display-fixes build --out /path/to/new-artifact
python3 -I display-fixes prepare \
  --artifact /path/to/new-artifact \
  --windowserver /path/to/kept-native-WindowServer \
  --golden-windowserver /path/to/original-Golden-Gate-WindowServer \
  --out /path/to/new-prepared-payload
sudo python3 -I display-fixes install \
  --prepared /path/to/new-prepared-payload --system /Volumes/OfflineSystem
python3 -I display-fixes test
```

- `build` compiles and signs `dss_shim.dylib` and `windowserver_filter.dylib`
  and writes each producer's manifest and a `build.json` with source and output
  hashes. It needs no Apple binary: the CoreDisplay link stub is in the source,
  and headers and libraries come from the SDK.
- `prepare` makes a signed payload from two Apple launchers you supply: the
  native Intel WindowServer kept by the image, and the original Golden Gate launcher,
  whose embedded Info.plist, identifier and entitlements it adopts. It adds a
  load command for `/usr/lib/dss_shim.dylib` to a copy of the native launcher
  and makes `dss_shim.dylib` load the filter. The inputs are not modified.
- `install` copies the three prepared files into an offline System mounted
  writable, with root ownership and restricted flags. It refuses the running
  `/`. It does not mount or unseal disks, touch EFI or kernel collections, or
  create the final System snapshot; the enclosing maintenance procedure does that.

Every output folder must be new.

## Outputs

`prepare` writes `build.json` and these signed files under `payload/`:

- `usr/lib/dss_shim.dylib`
- `usr/lib/windowserver_filter.dylib`
- `System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer`

With the recorded toolchain, `build` reproduces the libraries selected by the
`display_state_shim` (SHA256 `b5067e61...`) and `windowserver_filter_shim`
(SHA256 `599bccc7...`) input rows.

## Verify

```sh
python3 -I display-fixes test
```

The tests cover the display-state converter, library binding and signatures, the
inversion, backdrop, UUID and process checks, the installer callback used by the
image build, and a full build, prepare and copy on synthetic launcher fixtures
(ownership and flags are mocked; no disk is mounted). They also run the
`display-state-shim` and `golden-gate-windowserver-filter` test suites.

## Limitations

Installation into a physical offline System with this command, and display
inversion and rendering on hardware, are not yet confirmed.

Licensing: see [NOTICE.md](NOTICE.md).
