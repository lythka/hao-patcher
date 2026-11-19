"""`ssdimg generalize` plans, on a synthetic Data volume in a temporary folder (no image, no root).

    python3.14 -I -B -m unittest discover -s tools/ssdimg/tests -p test_generalize.py
"""
import os
import sys
import tempfile
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TOOLS)

from ssdimg import generalize  # noqa: E402

# The local Kerberos (LKDC) setup state: generalize leaves all of it or none of it.
LKDC = ["Library/Keychains/System.keychain",                                   # the KDC identities
        f"{generalize.DSLOCAL}/config/KerberosKDC.plist",                      # the realm
        "private/var/db/.configureLocalKDC"]                                   # the done-marker


def volume(root, files):
    os.makedirs(os.path.join(root, generalize.PREFS))  # every Data volume has it; generalize writes plists there
    for rel in files:
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write("x")


def run(root):
    plan = generalize.build(root, clean_logs=False)
    for _desc, fn in plan.steps:
        fn()
    return plan


class Lkdc(unittest.TestCase):
    def test_lkdc_state_goes_together(self):
        with tempfile.TemporaryDirectory() as root:
            volume(root, LKDC + ["private/var/db/krb5kdc/.keep"])
            run(root)
            self.assertEqual([rel for rel in LKDC if os.path.lexists(os.path.join(root, rel))], [])
            # launchd-tuning's folder is not LKDC state (it only stops Setup's rerun); it stays.
            self.assertTrue(os.path.isdir(os.path.join(root, "private/var/db/krb5kdc")))

    def test_marker_alone_is_removed(self):
        """An image generalized by an older ssdimg has the marker but no keychain; a second run fixes it."""
        with tempfile.TemporaryDirectory() as root:
            volume(root, [LKDC[2]])
            plan = run(root)
            self.assertFalse(os.path.lexists(os.path.join(root, LKDC[2])))
            self.assertTrue(any(".configureLocalKDC" in d for d, _ in plan.steps))

    def test_every_lkdc_path_is_listed(self):
        listed = {rel for rel, _why in generalize.FILES}
        self.assertEqual([rel for rel in LKDC if rel not in listed], [])


if __name__ == "__main__":
    unittest.main()
