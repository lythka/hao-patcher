"""Return a macOS install to a first-boot (Setup Assistant) state: a factory
reset of everything the owner and the source Mac left behind, with the OCLP
installation as the only exception.

Operates offline on mounted volumes of the *image* (never the running system):
the Data volume, plus the Preboot volume (login/recovery user info) and the VM
volume (swap). Keeps everything OCLP: /Applications/OpenCore-Patcher.app, the
Dortania LaunchAgents/Daemons, helper tool and Application Support folder, the
OCLP settings file in /Users/Shared, /Library/KernelCollections, the Preboot
kernel collections, the System volume and the EFI partition. The Boot.plist in
SystemConfiguration is kept as well.
"""
import glob
import os
import plistlib
import shutil
import subprocess

DSLOCAL = "private/var/db/dslocal/nodes/Default"
PREFS = "Library/Preferences"

# Top-level Data-volume entries a stock install has. Anything else is deleted
# when it is an empty folder or the installer's "Previous Content"; otherwise
# it is reported.
STOCK_TOP = {
    "Applications", "Library", "System", "Users", "Volumes", "cores", "home", "mnt",
    "private", "usr", "MobileSoftwareUpdate", ".fseventsd", ".Spotlight-V100",
    ".TemporaryItems", ".DocumentRevisions-V100", ".PKInstallSandboxManager-SystemSoftware",
    ".com.apple.templatemigration.boot-install", ".vol", ".file", ".DS_Store",
}

# /Library/Application Support entries a stock install (plus OCLP) has.
STOCK_APPSUPPORT = {"App Store", "Apple", "BTServer", "CrashReporter", "ProApps", "Script Editor",
                    "iLifeMediaBrowser", "livefsd", "Dortania"}

FILES = [  # (relative path on the Data volume, why)
    ("private/var/db/.AppleSetupDone", "re-run Setup Assistant (Hello splash) on first boot"),
    ("private/etc/kcpassword", "auto-login password"),
    ("Library/Preferences/com.apple.loginwindow.plist", "last user, recent users, guest and auto-login settings"),
    ("Library/Preferences/SystemConfiguration/preferences.plist", "network sets + computer name"),
    ("Library/Preferences/SystemConfiguration/NetworkInterfaces.plist", "this Mac's NIC list"),
    ("Library/Preferences/SystemConfiguration/com.apple.airport.preferences.plist", "Wi-Fi networks"),
    ("Library/Preferences/SystemConfiguration/com.apple.airport.preferences.plist.backup", "Wi-Fi networks"),
    ("Library/Preferences/SystemConfiguration/com.apple.wifi.message-tracer.plist", "Wi-Fi history"),
    ("Library/Preferences/SystemConfiguration/com.apple.smb.server.plist", "NetBIOS name and local Kerberos realm"),
    ("Library/Preferences/com.apple.wifi.known-networks.plist", "Wi-Fi known networks"),
    ("Library/Preferences/com.apple.preferences.accounts.plist", "deleted-user bookkeeping"),
    ("Library/Preferences/com.apple.Bluetooth.plist", "paired Bluetooth devices"),
    # The System keychain holds the Wi-Fi passwords and every other system-wide
    # secret (802.1X, VPN, Time Machine, printers) plus per-machine identities.
    # It cannot be opened offline (it is unlocked by this Mac's SystemKey), so it
    # goes as a whole: on the next boot /usr/libexec/security-checksystem sees it
    # missing and runs `systemkeychain -C -f`, which creates an empty keychain
    # and a fresh SystemKey.
    ("Library/Keychains/System.keychain", "legacy system keychain: system-wide secrets; recreated empty on first boot"),
    ("private/var/db/SystemKey", "unlock key of the System keychain; recreated with it"),
    ("Library/Keychains/apsd.keychain", "Apple Push Service device identity; re-issued on first boot"),
    ("Library/Keychains/system-keychain-2.db",
     "modern system keychain: Wi-Fi passwords, Bluetooth link keys, push tokens; recreated empty on first boot"),
    ("Library/Keychains/system-keychain-2.db-wal", "journal of the modern system keychain"),
    ("Library/Keychains/system-keychain-2.db-shm", "journal of the modern system keychain"),
    ("Library/Preferences/com.apple.security.systemidentities.plist", "pointers to identities in the removed keychain"),
    # Directory service and this Mac's identities
    (f"{DSLOCAL}/config/KerberosKDC.plist", "this Mac's local Kerberos realm; recreated at boot"),
    (f"{DSLOCAL}/sqlindex", "directory-service index that still lists the deleted accounts; rebuilt at boot"),
    (f"{DSLOCAL}/sqlindex-wal", "journal of the directory-service index"),
    (f"{DSLOCAL}/sqlindex-shm", "journal of the directory-service index"),
    (f"{DSLOCAL}/secureaccesstoken.plist", "secure-token bookkeeping of the deleted accounts"),
    ("private/var/db/lockdown/SystemConfiguration.plist", "lockdown host identifier; regenerated"),
    # Choices Setup Assistant makes again
    ("Library/Preferences/.GlobalPreferences.plist", "language, region and time-zone city chosen at setup"),
    ("Library/Preferences/com.apple.HIToolbox.plist", "keyboard layout chosen at setup"),
    ("private/etc/localtime", "time zone; set again at setup"),
    ("Library/Preferences/com.apple.timezone.auto.plist", "automatic time-zone setting"),
    ("private/var/db/.AppleDiagnosticsSetupDone", "analytics question already answered"),
    ("Library/Application Support/CrashReporter/DiagnosticMessagesHistory.plist", "analytics sharing choice"),
    # Privacy and account state
    ("Library/Preferences/com.apple.FindMyMac.plist", "Find My Mac state"),
    ("Library/Preferences/com.apple.mdmclient.plist", "device-enrollment bookkeeping"),
    # Third-party remnants
    ("Library/Application Support/Mozilla", "Firefox remnant (native messaging hosts)"),
]

GLOBS = [  # (glob relative to the Data volume, why)
    ("Library/Keychains/.fl*", "keychain lock files"),
    ("Library/Preferences/com.apple.PowerManagement.*.plist", "energy settings keyed by this Mac's platform UUID"),
    ("Library/Application Support/com.apple.TCC/TCC.db*", "system privacy grants; rebuilt with defaults"),
]

CLEAR_DIRS = [  # contents removed, directory kept
    ("private/var/log", "system logs"),
    ("private/var/db/diagnostics", "unified logs"),
    ("private/var/db/uuidtext", "unified log string tables"),
    ("Library/Logs", "system log files and crash reports"),
    ("Library/Caches", "system caches"),
    ("private/var/folders", "per-user temp/cache dirs"),
    ("private/var/tmp", "temp files"),
    ("private/tmp", "temp files"),
    (".fseventsd", "file-system event history"),
    (".DocumentRevisions-V100", "saved versions of the owner's documents"),
    ("private/var/db/dhcpclient/leases", "DHCP leases of the owner's networks"),
    ("private/var/db/locationd", "Location Services choice and clients; asked again at setup"),
    ("private/var/db/ConfigurationProfiles/Store", "configuration-profile store"),
    ("private/var/db/powerlog", "battery and power logs"),
    ("private/var/db/analyticsd", "analytics state"),
    ("private/var/root/Library", "root's caches, containers, logs and keychains"),
]

# The Spotlight index holds terms from the deleted users' documents. The kernel
# lets only mds touch this directory (even root gets EPERM), so it is removed
# through mdutil rather than deleted.
SPOTLIGHT = ".Spotlight-V100"

# /Library/Preferences is otherwise wiped (owner-set system settings). Kept:
# anything OCLP wrote, the Boot.plist, and the update settings written below.
PREFS_KEEP = {"com.apple.SoftwareUpdate.plist", "com.apple.commerce.plist"}
SYSCONFIG_KEEP = {"com.apple.Boot.plist"}

# Automatic updates off, including security responses and system data files:
# an OS update would undo the OCLP root patches.
UPDATE_PREFS = {
    "com.apple.SoftwareUpdate.plist": {
        "AutomaticCheckEnabled": False,
        "AutomaticDownload": False,
        "AutomaticallyInstallMacOSUpdates": False,
        "ConfigDataInstall": False,
        "CriticalUpdateInstall": False,
    },
    "com.apple.commerce.plist": {"AutoUpdate": False},
}

PREBOOT_FILES = [  # under <volume-group UUID>/var/db on the Preboot volume
    ("AdminUserRecoveryInfo.plist", "admin credentials for Recovery mode"),
    ("CryptoUserInfo.plist", "names and pictures of the volume's crypto users"),
    ("secureaccesstoken.plist", "secure-token bookkeeping"),
]


class Plan:
    def __init__(self, root):
        self.root = root
        self.steps = []   # (description, fn); fn may return a warning string
        self.notes = []

    def p(self, rel):
        return os.path.join(self.root, rel)

    def add(self, desc, fn):
        self.steps.append((desc, fn))


def users(root):
    """[(name, uid, generateduid, home)] for real (uid >= 501) local accounts."""
    out = []
    for f in sorted(glob.glob(os.path.join(root, DSLOCAL, "users", "*.plist"))):
        u = _load(f)
        try:
            uid = int(u.get("uid", ["0"])[0])
        except ValueError:
            continue
        name = u.get("name", [os.path.basename(f)[:-6]])[0]
        if uid >= 501 and not name.startswith("_"):
            out.append((name, uid, u.get("generateduid", [""])[0], u.get("home", [""])[0]))
    return out


def build(root, data_dev=None, keep_users=(), clean_logs=True, preboot=None, vm=None):
    plan = Plan(root)
    accounts = [u for u in users(root) if u[0] not in keep_users]

    for name, uid, guid, home in accounts:
        rec = plan.p(f"{DSLOCAL}/users/{name}.plist")
        plan.add(f"delete account record {name} (uid {uid})", lambda rec=rec: _rm(rec))
        h = plan.p(home.lstrip("/")) if home.startswith("/Users/") else plan.p(f"Users/{name}")
        if os.path.exists(h):
            plan.add(f"delete home folder {h[len(root):]}", lambda h=h: _rm(h))
        lo = plan.p(f"private/var/db/com.apple.xpc.launchd/disabled.{uid}.plist")
        if os.path.lexists(lo):
            plan.add(f"delete {lo[len(root):]} (launchd service overrides of uid {uid})", lambda f=lo: _rm(f))

    names = {a[0] for a in accounts}
    guids = {a[2] for a in accounts if a[2]}
    for g in sorted(glob.glob(plan.p(f"{DSLOCAL}/groups/*.plist"))):
        grp = _load(g)
        if names & set(grp.get("users", [])) or guids & set(grp.get("groupmembers", [])):
            plan.add(f"remove {', '.join(sorted(names))} from group {os.path.basename(g)[:-6]}",
                     lambda g=g: _edit_group(g, names, guids))
    for s in sorted(glob.glob(plan.p(f"{DSLOCAL}/sharepoints/*.plist"))):
        sp = _load(s)
        path = (sp.get("directory_path") or [""])[0]
        if any(path.startswith(f"/Users/{n}/") or path == f"/Users/{n}" for n in names):
            plan.add(f"delete file share {os.path.basename(s)[:-6]}", lambda s=s: _rm(s))

    covered = set()
    for rel, why in FILES:
        f = plan.p(rel)
        covered.add(f.lower())
        if os.path.lexists(f):
            plan.add(f"delete /{rel} ({why})", lambda f=f: _rm(f))
    for pattern, why in GLOBS:
        for f in sorted(glob.glob(plan.p(pattern))):
            covered.add(f.lower())
            plan.add(f"delete {f[len(root):]} ({why})", lambda f=f: _rm(f))

    for hist in glob.glob(plan.p("private/var/root/.*_history")) + glob.glob(plan.p("private/var/root/.zsh_sessions")):
        plan.add(f"delete root shell history {hist[len(root):]}", lambda h=hist: _rm(h))

    if clean_logs:
        for rel, why in CLEAR_DIRS:
            d = plan.p(rel)
            if not os.path.isdir(d):
                continue
            try:
                empty = not os.listdir(d)
            except PermissionError:
                plan.notes.append(f"/{rel} is protected by macOS and cannot be inspected or emptied here")
                continue
            if not empty:
                plan.add(f"empty /{rel} ({why})", lambda d=d: _empty(d))
        if os.path.lexists(plan.p(SPOTLIGHT)):
            plan.add(f"remove /{SPOTLIGHT} with mdutil -X (Spotlight index of the old contents; "
                     "the deployed Mac rebuilds it)", lambda: remove_spotlight_index(root))

    rest = _prefs_to_wipe(root, covered)
    if rest:
        kept = sorted(PREFS_KEEP | {f"SystemConfiguration/{k}" for k in SYSCONFIG_KEEP})
        plan.add(f"delete {len(rest)} other files in /{PREFS} (owner-set system settings; kept: {', '.join(kept)}, "
                 "subfolders): " + ", ".join(p[len(plan.p(PREFS)) + 1:] for p in rest),
                 lambda rest=rest: _rm_all(rest))
    for fname, keys in UPDATE_PREFS.items():
        plan.add(f"write /{PREFS}/{fname} with {', '.join(keys)} = off",
                 lambda f=plan.p(f"{PREFS}/{fname}"), k=keys: _write_plist(f, k))

    rlc = plan.p("private/var/db/.RunLanguageChooserToo")
    if not os.path.lexists(rlc):
        plan.add("create /private/var/db/.RunLanguageChooserToo (language chooser on first boot)", lambda: _touch(rlc))

    if data_dev:
        for snap in _snapshots(data_dev):
            plan.add(f"delete APFS snapshot {snap} on Data volume",
                     lambda s=snap: subprocess.run(["diskutil", "apfs", "deleteSnapshot", data_dev, "-name", s],
                                                   check=True, capture_output=True))
        cu = subprocess.run(["diskutil", "apfs", "listCryptoUsers", data_dev], capture_output=True, text=True)
        plan.notes.append("APFS crypto users on Data volume (the deleted account's secure-token record cannot be "
                          "removed offline; check `sysadminctl -secureTokenStatus` of the first new user):\n    "
                          + (cu.stdout.strip() or cu.stderr.strip()).replace("\n", "\n    "))

    leftover = []
    for e in sorted(set(os.listdir(root)) - STOCK_TOP):
        p = plan.p(e)
        if e == "Previous Content":
            plan.add(f"delete /{e} (macOS installer leftovers)", lambda p=p: _rm(p))
        elif os.path.isdir(p) and not os.path.islink(p) and _listdir(p) == []:
            plan.add(f"delete empty folder /{e}", lambda p=p: _rm(p))
        else:
            leftover.append(e)
    if leftover:
        plan.notes.append("Non-standard top-level entries on the Data volume (left untouched; "
                          "inspect and delete manually if unwanted): " + ", ".join(leftover))
    asup = plan.p("Library/Application Support")
    if os.path.isdir(asup):
        other = sorted(e for e in os.listdir(asup) if e not in STOCK_APPSUPPORT and not e.startswith("com.apple.")
                       and not e.startswith(".") and os.path.join(asup, e).lower() not in covered)
        if other:
            plan.notes.append("Non-Apple entries in /Library/Application Support kept: " + ", ".join(other))
    local = plan.p("usr/local")
    if os.path.isdir(local) and os.listdir(local):
        plan.notes.append("/usr/local is not empty (Homebrew or other tools?): "
                          + ", ".join(sorted(os.listdir(local))))
    others = [d for d in os.listdir(plan.p("Users")) if d not in ("Shared", ".localized")
              and d not in names and not d.startswith(".")] if os.path.isdir(plan.p("Users")) else []
    if others:
        plan.notes.append("Folders in /Users kept (no matching account removed): " + ", ".join(others))
    kc = plan.p("Library/Keychains")
    if os.path.isdir(kc):
        left = sorted(e for e in os.listdir(kc) if os.path.join(kc, e).lower() not in covered)
        if left:
            plan.notes.append("Other entries in /Library/Keychains kept (an institutional FileVault or "
                              "certificate keychain would be here): " + ", ".join(left))
    oclp = plan.p("Applications/OpenCore-Patcher.app")
    plan.notes.append("OpenCore-Patcher.app in /Applications: " + ("present (kept)" if os.path.lexists(oclp) else "NOT FOUND"))

    if preboot:
        _preboot_steps(plan, preboot, names)
    if vm and os.path.isdir(vm):
        for f in sorted(os.listdir(vm)):
            if f.startswith("swapfile") or f == "sleepimage":
                plan.add(f"delete {f} on the VM volume (memory contents of the source Mac; recreated on boot)",
                         lambda p=os.path.join(vm, f): _rm(p))
    return plan


def _preboot_steps(plan, preboot, names):
    """The Preboot volume mirrors the accounts for the login/recovery screens:
    <volume-group UUID>/var/db/AllUsersInfo.plist and friends. macOS rewrites
    them (diskutil apfs updatePreboot) when the first account is created."""
    for d in sorted(os.listdir(preboot)):
        vdb = os.path.join(preboot, d, "var", "db")
        if not os.path.isdir(vdb):
            continue
        where = f"Preboot/{d[:8]}…/var/db"
        aui = os.path.join(vdb, "AllUsersInfo.plist")
        info = _load(aui)
        gone = sorted(n for n in info if n in names) if isinstance(info, dict) else []
        if gone:
            plan.add(f"remove {', '.join(gone)} from {where}/AllUsersInfo.plist (login names and pictures)",
                     lambda f=aui, g=gone: _drop_keys(f, g))
        for f, why in PREBOOT_FILES:
            p = os.path.join(vdb, f)
            if os.path.lexists(p):
                plan.add(f"delete {where}/{f} ({why}; rewritten when the first account is created)",
                         lambda p=p: _rm(p))
        wp = os.path.join(vdb, "Wallpapers")
        if os.path.isdir(wp) and _listdir(wp):
            plan.add(f"empty {where}/Wallpapers (login wallpaper copy)", lambda d=wp: _empty(d))


def _prefs_to_wipe(root, covered):
    """Files in /Library/Preferences (and SystemConfiguration) not already
    covered by FILES/GLOBS and not on a keep list. Subfolders are left alone."""
    out = []
    pd = os.path.join(root, PREFS)
    for sub, keep in (("", PREFS_KEEP), ("SystemConfiguration", SYSCONFIG_KEEP)):
        d = os.path.join(pd, sub) if sub else pd
        if not os.path.isdir(d):
            continue
        for e in sorted(os.listdir(d)):
            p = os.path.join(d, e)
            if os.path.isdir(p) and not os.path.islink(p):
                continue
            if e in keep or _is_oclp(e) or p.lower() in covered:
                continue
            out.append(p)
    return out


def _is_oclp(name):
    n = name.lower()
    return "dortania" in n or "opencore" in n


def _load(path):
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except (plistlib.InvalidFileException, OSError):
        return {}


def _listdir(d):
    try:
        return os.listdir(d)
    except PermissionError:
        return None


def _rm(p, failed=None):
    """Remove a file, link or tree. Entries macOS protects (EPERM) are collected
    in `failed` when given, otherwise the error propagates."""
    if os.path.isdir(p) and not os.path.islink(p):
        if failed is None:
            shutil.rmtree(p)
        else:
            shutil.rmtree(p, onexc=lambda fn, path, exc: failed.append(path) if isinstance(exc, PermissionError) else _raise(exc))
    elif os.path.lexists(p):
        try:
            os.unlink(p)
        except PermissionError:
            if failed is None:
                raise
            failed.append(p)


def _raise(exc):
    raise exc


def _rm_all(paths):
    failed = []
    for p in paths:
        _rm(p, failed)
    return _warn(failed)


def _empty(d):
    failed = []
    for e in os.listdir(d):
        _rm(os.path.join(d, e), failed)
    return _warn(failed)


def _warn(failed):
    if failed:
        return f"{len(failed)} protected entries could not be removed: " + ", ".join(failed[:4]) + (" …" if len(failed) > 4 else "")
    return None


def _touch(p):
    with open(p, "w"):
        pass
    os.chmod(p, 0o644)


def _write_plist(path, d):
    with open(path, "wb") as fh:
        plistlib.dump(d, fh, fmt=plistlib.FMT_BINARY)
    os.chmod(path, 0o644)


def _fmt(path):
    with open(path, "rb") as fh:
        return plistlib.FMT_BINARY if fh.read(6) == b"bplist" else plistlib.FMT_XML


def _edit_group(path, names, guids):
    fmt = _fmt(path)
    with open(path, "rb") as fh:
        grp = plistlib.load(fh)
    for key, drop in (("users", names), ("groupmembers", guids)):
        if key in grp:
            grp[key] = [v for v in grp[key] if v not in drop]
            if not grp[key]:
                del grp[key]
    with open(path, "wb") as fh:
        plistlib.dump(grp, fh, fmt=fmt)


def _drop_keys(path, keys):
    fmt = _fmt(path)
    with open(path, "rb") as fh:
        d = plistlib.load(fh)
    for k in keys:
        d.pop(k, None)
    with open(path, "wb") as fh:
        plistlib.dump(d, fh, fmt=fmt)


def _snapshots(dev):
    p = subprocess.run(["diskutil", "apfs", "listSnapshots", "-plist", dev], capture_output=True)
    if p.returncode:
        return []
    return [s["SnapshotName"] for s in plistlib.loads(p.stdout).get("Snapshots", [])]


def remove_spotlight_index(root):
    """Remove the volume's Spotlight index with mdutil (-X; falls back to -E,
    erase and rebuild). mdutil exits 0 even when it fails, so its text is checked."""
    outs = []
    for flag in ("-X", "-E"):
        p = subprocess.run(["mdutil", flag, root], capture_output=True, text=True)
        out = (p.stdout + p.stderr).strip()
        outs.append(f"mdutil {flag}: {out}")
        if p.returncode == 0 and "Error" not in out and "Must be root" not in out:
            return
    raise RuntimeError("could not remove the Spotlight index:\n  " + "\n  ".join(outs))


def fsevents_marker(root, on):
    """Create (on=True) or remove the .fseventsd/no_log marker on the volume at
    `root`. While the marker exists, fseventsd does not write the volume's event
    log when the volume unmounts; without it, that log would record every path
    generalize just deleted."""
    d = os.path.join(root, ".fseventsd")
    marker = os.path.join(d, "no_log")
    if on:
        os.makedirs(d, exist_ok=True)
        with open(marker, "w"):
            pass
    elif os.path.lexists(marker):
        os.unlink(marker)
