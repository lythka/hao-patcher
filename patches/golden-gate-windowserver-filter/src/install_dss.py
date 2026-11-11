"""Install the display-state repair using the image builder's copy/signing operations.

The two calls remain on either side of IO80211 installation in the userspace
recipe. This keeps the existing file, Info.plist, load-command and signing order.
"""

# The Golden Gate x86_64 native server omits the flag byte its clients require.
# The dylib gates the repair on the SkyLight/CoreDisplay UUIDs listed in its dss_builds.h.
DISPLAY_STATE_SHIM = 'usr/lib/dss_shim.dylib'
DISPLAY_STATE_CLIENTS = (
    'System/Library/PrivateFrameworks/SkyLight.framework/Versions/A/Resources/WindowServer',
)


def install_library(system, shim, install_file):
    install_file(shim, system/DISPLAY_STATE_SHIM)


def resign_clients(system, source, workspace, clients, resign_with_entitlements):
    for rel in clients:
        dylibs = (DISPLAY_STATE_SHIM,) if rel in DISPLAY_STATE_CLIENTS else ()
        print('Re-signed with 27.0 entitlements:', rel,
              resign_with_entitlements(system/rel, source/rel, workspace, dylibs),
              'loads', dylibs, flush=True)
