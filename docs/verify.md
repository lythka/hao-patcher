# Verify a release

For checking a signed hao builder kit before making an installer.

The release's `hao` bootstrap carries the owner's SSH public key, fingerprint
`SHA256:HPvLz28RozOa/bJ+7bJ0ha4s27LBEjQ/B3TxSqQCdek`. Verify that fingerprint
through a trusted copy of this project before trusting a newly downloaded bootstrap.

## Automatic checks

`sh hao` verifies the SSH signatures on `release.json` and `SHA256SUMS` with
namespace `hao-release`. It then checks the release manifest's checksum and every
builder download's SHA256 before extracting or executing it. A failed signature,
hash, download or architecture check stops with exit status 4. Finished, verified
downloads are kept for the next run.

The manifest pins the code snapshot, Python for each architecture, LLVM tools,
our binaries, and Apple's inputs. A final cut names one clean tagged commit.
Development after that cut cannot change the pinned files. A shipped release's
fixes are cut from its release branch as a new release.

## Check by hand

Download `hao`, `release.json`, `SHA256SUMS`, their two `.sig` files, and the assets
from the same release. Save the trusted public key as `owner.pub`, then run:

```sh
ssh-keygen -lf owner.pub
{ printf 'hao-release '; cat owner.pub; } > allowed-signers
ssh-keygen -Y verify -f allowed-signers -I hao-release -n hao-release -s release.json.sig < release.json
ssh-keygen -Y verify -f allowed-signers -I hao-release -n hao-release -s SHA256SUMS.sig < SHA256SUMS
shasum -a 256 -c SHA256SUMS --ignore-missing
```

Both signature checks must succeed and every downloaded file must say `OK`.
Files you have not downloaded are skipped. Check the bootstrap itself against the
signed checksum list before running it when verifying manually.

The older unsigned v0.0.1 exception is not accepted by the bootstrap. Four
harvested caches remain covered by the existing [reproduction exception](reproduce.md#what-must-match).
