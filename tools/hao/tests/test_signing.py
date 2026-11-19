"""Release signatures (src/signing.py): RFC 8032's vectors, ssh-keygen's own SSH signatures, and the refusals: a
changed file, another namespace, an RSA key, an unknown key, a missing signature, malformed blobs, and hao fetch /
create-installer / install stopping with exit 4 before anything is read or written.

fixtures/signing (OpenSSH 10.5 ssh-keygen, throwaway keys whose secret halves were never kept):
  release.json.sig      ssh-keygen -Y sign -f test-key -n hao-release release.json            (sha512)
  sig-sha256            the same with -O hashalg=sha256
  sig-other-namespace   -n file
  sig-rsa               an RSA key (rsa-key.pub)"""
import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
FIX = HERE / 'fixtures' / 'signing'
sys.path.insert(0, str(TOOL / 'src'))
import signing as SIGN    # noqa: E402

VECTORS = [   # RFC 8032 section 7.1, tests 1-3: secret, public, message, signature
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
]


def ssh_string(b):
    return len(b).to_bytes(4, 'big') + b


def sshsig_files(secret, data, namespace='hao-release', hash_name='sha512'):
    """(public key line, armored SSHSIG) as ssh-keygen writes them, made with SECRET (for tests' own keys)."""
    pub = SIGN.secret_to_public(secret)
    key_blob = ssh_string(b'ssh-ed25519') + ssh_string(pub)
    sig = SIGN.sign(secret, SIGN.signed_data(namespace, hash_name, data))
    blob = (b'SSHSIG' + (1).to_bytes(4, 'big') + ssh_string(key_blob) + ssh_string(namespace.encode()) + ssh_string(b'')
            + ssh_string(hash_name.encode()) + ssh_string(ssh_string(b'ssh-ed25519') + ssh_string(sig)))
    b64 = base64.b64encode(blob).decode()
    armor = '\n'.join([SIGN.BEGIN] + [b64[k:k + 70] for k in range(0, len(b64), 70)] + [SIGN.END]) + '\n'
    return 'ssh-ed25519 %s test\n' % base64.b64encode(key_blob).decode(), armor


def sk_sshsig_files(secret, data, application=b'ssh:hao-release', flags=0x05, counter=7, namespace='hao-release',
                    key_application=None):
    """(public key line, armored SSHSIG) as a FIDO security key (sk-ssh-ed25519@openssh.com) signs through ssh-keygen:
    the authenticator signs sha256(application) || flags || counter || sha256(SSHSIG data) (OpenSSH PROTOCOL.u2f)."""
    kind = b'sk-ssh-ed25519@openssh.com'
    pub = SIGN.secret_to_public(secret)
    key_blob = ssh_string(kind) + ssh_string(pub) + ssh_string(key_application or application)
    signed = SIGN.signed_data(namespace, 'sha512', data)
    sig = SIGN.sign(secret, hashlib.sha256(application).digest() + bytes([flags]) + counter.to_bytes(4, 'big')
                    + hashlib.sha256(signed).digest())
    blob = (b'SSHSIG' + (1).to_bytes(4, 'big') + ssh_string(key_blob) + ssh_string(namespace.encode()) + ssh_string(b'')
            + ssh_string(b'sha512') + ssh_string(ssh_string(kind) + ssh_string(sig) + bytes([flags])
                                                 + counter.to_bytes(4, 'big')))
    b64 = base64.b64encode(blob).decode()
    armor = '\n'.join([SIGN.BEGIN] + [b64[k:k + 70] for k in range(0, len(b64), 70)] + [SIGN.END]) + '\n'
    return 'sk-ssh-ed25519@openssh.com %s test\n' % base64.b64encode(key_blob).decode(), armor


class Ed25519(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for sk, pk, m, sg in VECTORS:
            sk, pk, m, sg = map(bytes.fromhex, (sk, pk, m, sg))
            self.assertEqual(SIGN.secret_to_public(sk), pk)
            self.assertEqual(SIGN.sign(sk, m), sg)
            self.assertTrue(SIGN.ed25519_verify(pk, m, sg))
            self.assertFalse(SIGN.ed25519_verify(pk, m + b'x', sg))

    def test_malformed_signatures_fail(self):
        sk, pk, m, sg = map(bytes.fromhex, VECTORS[1])
        s = int.from_bytes(sg[32:], 'little')
        self.assertFalse(SIGN.ed25519_verify(pk, m, sg[:32] + int.to_bytes(s + SIGN.q, 32, 'little')))  # S >= L
        self.assertFalse(SIGN.ed25519_verify(pk, m, b'\xff' * 32 + sg[32:]))      # R does not decode
        self.assertFalse(SIGN.ed25519_verify(b'\xff' * 32, m, sg))                  # A does not decode
        self.assertFalse(SIGN.ed25519_verify(pk, m, sg[:63]))


class SSHSig(unittest.TestCase):
    def setUp(self):
        self.keys = SIGN.load_keys(FIX / 'test-key.pub')
        self.data = (FIX / 'release.json').read_bytes()
        self.sig = (FIX / 'release.json.sig').read_text()

    def test_ssh_keygen_signatures_verify(self):
        kid, hashed = SIGN.verify(self.data, self.sig, self.keys)
        self.assertEqual(kid, 'SHA256:mKzv3UcvDYUzs6dQuc9E/qAbvOkDKBkwSGLo2d4ZuhA')    # as ssh-keygen -l shows it
        self.assertEqual(hashed, 'sha512')
        self.assertEqual(SIGN.verify(self.data, (FIX / 'sig-sha256').read_text(), self.keys)[1], 'sha256')

    def blob(self, sig=None):
        return base64.b64decode(''.join((sig or self.sig).strip().splitlines()[1:-1]))

    def armor(self, blob):
        return '%s\n%s\n%s\n' % (SIGN.BEGIN, base64.b64encode(blob).decode(), SIGN.END)

    def test_refusals(self):
        other = SIGN.parse_keys(sshsig_files(b'\1' * 32, b'')[0])
        blob = self.blob()
        for why, data, sig, keys in (
                ('changed file', self.data + b' ', self.sig, self.keys),
                ('other namespace', self.data, (FIX / 'sig-other-namespace').read_text(), self.keys),
                ('rsa key', self.data, (FIX / 'sig-rsa').read_text(), self.keys),
                ('unknown key', self.data, self.sig, other),
                ('not armored', self.data, 'hello', self.keys),
                ('bad base64', self.data, '%s\n!!!\n%s\n' % (SIGN.BEGIN, SIGN.END), self.keys),
                ('trailing bytes', self.data, self.armor(blob + b'\0'), self.keys),
                ('cut short', self.data, self.armor(blob[:-5]), self.keys),
                ('version 2', self.data, self.armor(blob[:6] + (2).to_bytes(4, 'big') + blob[10:]), self.keys)):
            with self.assertRaises(SIGN.SignatureError, msg=why):
                SIGN.verify(data, sig, keys)

    def test_reserved_and_hash_must_be_known(self):
        secret = b'\6' * 32
        pub_line, _ = sshsig_files(secret, self.data)
        keys = SIGN.parse_keys(pub_line)
        pub = SIGN.secret_to_public(secret)
        for reserved, hash_name in ((b'x', 'sha512'), (b'', 'sha1')):
            signed = (b'SSHSIG' + ssh_string(b'hao-release') + ssh_string(reserved) + ssh_string(hash_name.encode())
                      + ssh_string(hashlib.sha1(self.data).digest()))
            sig = SIGN.sign(secret, signed)
            blob = (b'SSHSIG' + (1).to_bytes(4, 'big') + ssh_string(ssh_string(b'ssh-ed25519') + ssh_string(pub))
                    + ssh_string(b'hao-release') + ssh_string(reserved) + ssh_string(hash_name.encode())
                    + ssh_string(ssh_string(b'ssh-ed25519') + ssh_string(sig)))
            with self.assertRaises(SIGN.SignatureError):
                SIGN.verify(self.data, self.armor(blob), keys)

    @unittest.skipUnless(shutil.which('ssh-keygen'), 'no ssh-keygen here')
    def test_ssh_keygen_accepts_what_the_helper_signs(self):
        with tempfile.TemporaryDirectory() as d:
            pub, sig = sshsig_files(b'\7' * 32, self.data)
            (Path(d) / 'allowed').write_text('owner ' + pub)
            (Path(d) / 'sig').write_text(sig)
            r = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(Path(d) / 'allowed'), '-I', 'owner',
                                '-n', 'hao-release', '-s', str(Path(d) / 'sig')], input=self.data, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_helper_signatures_verify_here(self):
        pub, sig = sshsig_files(b'\7' * 32, self.data)
        self.assertEqual(SIGN.verify(self.data, sig, SIGN.parse_keys(pub))[1], 'sha512')

    def test_keys_file(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SIGN.SignatureError):
                SIGN.load_keys(Path(d) / 'none.pub')
            (Path(d) / 'empty.pub').write_text('# nothing\n')
            with self.assertRaises(SIGN.SignatureError):
                SIGN.load_keys(Path(d) / 'empty.pub')
            with self.assertRaises(SIGN.SignatureError):     # only ssh-ed25519 keys are release keys
                SIGN.load_keys(FIX / 'rsa-key.pub')
            two = Path(d) / 'two.pub'
            two.write_text((FIX / 'test-key.pub').read_text() + sshsig_files(b'\1' * 32, b'')[0])
            self.assertEqual(len(SIGN.load_keys(two)), 2)

    def test_non_canonical_base64_and_weak_keys_are_refused(self):
        self.assertEqual(base64.b64decode('QR=='), b'A')            # other unused bits: same byte, other spelling
        self.assertEqual(SIGN._canonical_b64('QQ==', 'x'), b'A')
        with self.assertRaises(SIGN.SignatureError):
            SIGN._canonical_b64('QR==', 'x')
        weak = b'\x01' + b'\0' * 31                                # the neutral point (order 1)
        blob = ssh_string(b'ssh-ed25519') + ssh_string(weak)
        with self.assertRaises(SIGN.SignatureError):
            SIGN.parse_keys('ssh-ed25519 %s\n' % base64.b64encode(blob).decode())

    def test_verified_release_reads_once_and_keeps_a_private_copy(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ('release.json', 'release.json.sig'):
                (Path(d) / n).write_bytes((FIX / n).read_bytes())
            copy, kid, hashed = SIGN.verified_release(Path(d) / 'release.json', self.keys)
            self.assertNotEqual(copy.parent, Path(d))
            self.assertEqual(copy.read_bytes(), self.data)
            self.assertEqual(oct(copy.parent.stat().st_mode & 0o777), '0o700')
            self.assertTrue(SIGN.signature_of(copy).is_file())
            (Path(d) / 'release.json.sig').unlink()
            with self.assertRaises(SIGN.SignatureError):
                SIGN.verified_release(Path(d) / 'release.json', self.keys)
            (Path(d) / 'release.json.sig').symlink_to(FIX / 'release.json.sig')
            with self.assertRaises(SIGN.SignatureError):     # not through a symlink
                SIGN.verified_release(Path(d) / 'release.json', self.keys)


class Rotation(unittest.TestCase):
    """Two allowed signers in release-keys.pub: a plain ssh-ed25519 key (a key file, or a Ledger through ledger-agent,
    which signs as ssh-ed25519) and a YubiKey's sk-ssh-ed25519; either one's signature is accepted."""
    def test_either_key_of_two_verifies(self):
        data = b'{"release": "test"}\n'
        plain_pub, plain_sig = sshsig_files(b'\31' * 32, data)
        sk_pub, sk_sig = sk_sshsig_files(b'\32' * 32, data)
        keys = SIGN.parse_keys('# key 1 (Ledger)\n' + plain_pub + '# key 2 (YubiKey)\n' + sk_pub)
        self.assertEqual(len(keys), 2)
        self.assertEqual(SIGN.verify(data, plain_sig, keys)[1], 'sha512')
        self.assertEqual(SIGN.verify(data, sk_sig, keys)[1], 'sha512')
        other_pub, other_sig = sshsig_files(b'\33' * 32, data)
        with self.assertRaises(SIGN.SignatureError):
            SIGN.verify(data, other_sig, keys)


class SecurityKey(unittest.TestCase):
    """sk-ssh-ed25519@openssh.com: a YubiKey's ed25519-sk key."""
    data = b'{"release": "test"}\n'

    def test_a_touched_signature_verifies(self):
        pub, sig = sk_sshsig_files(b'\10' * 32, self.data)
        kid, hashed = SIGN.verify(self.data, sig, SIGN.parse_keys(pub))
        self.assertTrue(kid.startswith('SHA256:'))

    @unittest.skipUnless(shutil.which('ssh-keygen'), 'no ssh-keygen here')
    def test_ssh_keygen_agrees(self):
        with tempfile.TemporaryDirectory() as d:
            # touched + PIN (0x05), as the release keys sign: ssh-keygen with the allowed-signers line users are given
            # accepts it too. Without a touch or the PIN hao refuses (test_refusals); ssh-keygen's allowed-signers
            # format has no option to require either, so only hao (and the release gate) enforce them.
            for flags, want in ((0x05, 0),):
                pub, sig = sk_sshsig_files(b'\10' * 32, self.data, flags=flags)
                (Path(d) / 'allowed').write_text('hao namespaces="hao-release" ' + pub)
                (Path(d) / 'sig').write_text(sig)
                r = subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(Path(d) / 'allowed'), '-I', 'hao',
                                    '-n', 'hao-release', '-s', str(Path(d) / 'sig')], input=self.data,
                                   capture_output=True)
                self.assertEqual(r.returncode, want, (flags, r.stderr))
                ok = True
                try:
                    SIGN.verify(self.data, sig, SIGN.parse_keys(pub))
                except SIGN.SignatureError:
                    ok = False
                self.assertEqual(ok, want == 0, flags)

    def test_refusals(self):
        secret = b'\10' * 32
        pub, _ = sk_sshsig_files(secret, self.data)
        keys = SIGN.parse_keys(pub)
        cases = {
            'no touch': sk_sshsig_files(secret, self.data, flags=0x04)[1],
            'no PIN': sk_sshsig_files(secret, self.data, flags=0x01)[1],
            'neither': sk_sshsig_files(secret, self.data, flags=0x00)[1],
            'other application': sk_sshsig_files(secret, self.data, application=b'ssh:other')[1],
            'application changed after signing': sk_sshsig_files(secret, self.data, key_application=b'ssh:x')[1],
            'changed file': sk_sshsig_files(secret, self.data + b' ')[1],
        }
        _, good = sk_sshsig_files(secret, self.data, counter=7)
        blob = bytearray(base64.b64decode(''.join(good.strip().splitlines()[1:-1])))
        blob[-1] ^= 1                                           # the counter, changed after signing
        cases['changed counter'] = '%s\n%s\n%s\n' % (SIGN.BEGIN, base64.b64encode(bytes(blob)).decode(), SIGN.END)
        for why, sig in cases.items():
            with self.assertRaises(SIGN.SignatureError, msg=why):
                SIGN.verify(self.data, sig, keys)

    def test_kinds_do_not_mix(self):
        secret = b'\11' * 32
        plain_pub, _ = sshsig_files(secret, self.data)
        _, sk_sig = sk_sshsig_files(secret, self.data)
        with self.assertRaises(SIGN.SignatureError):            # same Ed25519 key, but trusted only as a plain key
            SIGN.verify(self.data, sk_sig, SIGN.parse_keys(plain_pub))
        with self.assertRaises(SIGN.SignatureError):            # a key line whose type word lies
            SIGN.parse_keys(plain_pub.replace('ssh-ed25519', 'sk-ssh-ed25519@openssh.com', 1))


class Commands(unittest.TestCase):
    def hao(self, *argv, env=None):
        return subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'hao')] + list(argv), capture_output=True,
                              text=True, env=dict(os.environ, **(env or {})))

    def release(self, d, sign=True):
        rel = Path(d) / 'release.json'
        rel.write_text(json.dumps({'downloads': [{'name': 'a.bin', 'url': 'file:///nonexistent', 'size': 1,
                                                  'sha256': '0' * 64}]}))
        if sign:
            (Path(d) / 'release.json.sig').write_text(sshsig_files(b'\2' * 32, rel.read_bytes())[1])
        return rel

    def test_fetch_refuses_an_unsigned_release_with_exit_4(self):
        with tempfile.TemporaryDirectory() as d:
            rel = self.release(d, sign=False)
            r = self.hao('fetch', str(rel), '--cache', d + '/cache', env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn('not signed by hao', r.stdout + r.stderr)
            self.assertFalse((Path(d) / 'cache').exists())

    def test_fetch_refuses_an_unsigned_v001(self):
        # v0.0.1 is signed: with SHA256SUMS but no signatures, fetch stops before any download
        with tempfile.TemporaryDirectory() as d:
            rel = Path(d) / 'release.json'
            rel.write_text(json.dumps({'version': 'v0.0.1', 'downloads': [
                {'name': 'a.bin', 'url': 'file:///nonexistent', 'size': 1, 'sha256': '0' * 64}]}))
            (Path(d) / 'SHA256SUMS').write_text('%s  release.json\n' % hashlib.sha256(rel.read_bytes()).hexdigest())
            r = self.hao('fetch', str(rel), '--cache', d + '/cache', env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertNotIn('UNSIGNED release', r.stdout + r.stderr)
            self.assertFalse((Path(d) / 'cache').exists())

    def test_fetch_refuses_a_release_signed_by_another_key(self):
        with tempfile.TemporaryDirectory() as d:
            rel = self.release(d)
            r = self.hao('fetch', str(rel), '--cache', d + '/cache', env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertFalse((Path(d) / 'cache').exists())

    def test_create_installer_refuses_an_unsigned_release(self):
        with tempfile.TemporaryDirectory() as d:
            rel = self.release(d, sign=False)
            r = self.hao('create-installer', '--dry-run', '--release', str(rel), env={'HAO_LOGS': d})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)

    def test_install_checks_the_drive(self):
        with tempfile.TemporaryDirectory() as d:
            self.release(d, sign=False)
            (Path(d) / 'stick.json').write_text(json.dumps({'release_sha256': 'f' * 64}))
            r = self.hao('install', '--dry-run', env={'HAO_STICK': d, 'HAO_LOGS': d, 'HAO_ANYWHERE': '1'})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            (Path(d) / 'release.json').unlink()
            r = self.hao('install', '--dry-run', env={'HAO_STICK': d, 'HAO_LOGS': d, 'HAO_ANYWHERE': '1'})
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn('is gone', r.stdout + r.stderr)

    def test_signed_release_accepts_the_owners_key_in_process(self):
        hao = runpy.run_path(str(TOOL / 'hao'), run_name='hao_module')
        import console as CON
        with tempfile.TemporaryDirectory() as d:
            rel = self.release(d, sign=False)
            pub, sig = sshsig_files(b'\3' * 32, rel.read_bytes())
            (Path(d) / 'release.json.sig').write_text(sig)
            (Path(d) / 'keys.pub').write_text(pub)
            keys = hao['SIGN'].KEYS
            hao['SIGN'].KEYS = Path(d) / 'keys.pub'
            try:
                import argparse
                c = CON.Console('quiet', env={})
                copy = hao['signed_release'](argparse.Namespace(unsigned=False), c, rel)
            finally:
                hao['SIGN'].KEYS = keys
            self.assertEqual(Path(copy).read_bytes(), rel.read_bytes())

    def test_unsigned_is_for_a_checkout_only(self):
        hao = runpy.run_path(str(TOOL / 'hao'), run_name='hao_module')
        import argparse
        import console as CON
        with tempfile.TemporaryDirectory() as d:
            rel = self.release(d, sign=False)
            c = CON.Console('quiet', env={})
            hao['signed_release'].__globals__['REPO'] = Path(d)          # no .git there: a release
            with self.assertRaises(CON.Stopped) as x:
                hao['signed_release'](argparse.Namespace(unsigned=True), c, rel)
            self.assertEqual(x.exception.code, 5)



class ShippedKeys(unittest.TestCase):
    """The tree carries the owner's release key (DECISIONS 2026-11-17) and no waiver for a signed release."""
    def test_the_release_key_is_the_owners(self):
        keys = SIGN.parse_keys((TOOL / 'data' / 'release-keys.pub').read_text())
        self.assertEqual(sorted(keys.values()), ['SHA256:HPvLz28RozOa/bJ+7bJ0ha4s27LBEjQ/B3TxSqQCdek'])

    def test_v001_has_no_unsigned_waiver(self):
        waivers = json.loads((TOOL / 'data' / 'unsigned-releases.json').read_text())['releases']
        self.assertNotIn('v0.0.1', waivers)

if __name__ == '__main__':
    unittest.main()
