# acquire

Downloads every file a release pins: each by URL (plus optional mirrors), size and SHA-256. Workflow plan
step 12. Apple's installers and KDKs are never published by this project; the builder fetches them from their
own servers on the user's Mac.

```sh
python3.14 -I tools/acquire/acquire fetch release.json --cache CACHE      # download what is missing
python3.14 -I tools/acquire/acquire fetch release.json --cache CACHE --assets-from STAGED_DIR   # assets from a folder
python3.14 -I tools/acquire/acquire verify release.json --cache CACHE     # all present and intact?
python3.14 -I tools/acquire/acquire path release.json NAME --cache CACHE  # where a file is
```

- **Pinned files:** each lives at `CACHE/sha256/<hex>`. A file already there with the right hash is never
  fetched again.
- **Resuming:** an interrupted download resumes from `CACHE/partial/<hex>` with an HTTP Range request. A server
  that ignores ranges gets a clean restart.
- **Refusals:** a file of the wrong size or hash is refused and never moved into place, and the next mirror is
  tried.
- **Output:** `--quiet`, the default (one line per file plus progress), or `--log json`.
- **Dependencies:** the standard library only.

Manifest: a `release.json` (`pipeline/release-cut --write`), or just its `downloads`. Its `downloads` are Apple's and
third-party files, its `assets` our own (each named by its `file`); both are fetched and checked the same way. An entry
without a URL is refused. `--assets-from DIR|URL` fetches the assets from that folder or URL prefix first (a staged
release before upload, a mirror) and from their own URLs only when it lacks them.

```json
{"downloads": [{"name": "macos-27.2.1-26B131-installer", "url": "https://…", "size": 14812043264,
                "sha256": "…", "mirrors": ["https://…"]}]}
```

Not done yet:

- **Decoding and unpacking:** the pinned `ipsw` binary for AEA decoding and the prebuilt unpack tools.
- **The host matrix:** which steps work on macOS 12, 13 and 14 and later, on Intel and Apple silicon.

## Host matrix

```sh
python3.14 -I tools/acquire/host-check [--json] [--need-gb N]
```

`host-check` lists each builder step and whether this Mac can run it, naming what is missing:

- download and verify;
- unpack the installers;
- decode the macOS 27 IPSW (`aea` on macOS 13 and later, or the pinned `ipsw`);
- edit and sign files;
- rebuild Metal libraries (Xcode's Metal toolchain, or the bundled LLVM via `HAO_LLVM_BUNDLE`);
- Rosetta on Apple silicon;
- write the USB stick (root).

Running it on each host the release supports gives the plan's host matrix.

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/acquire/tests      # includes host-check on synthetic hosts
```

The tests serve files from a local HTTP server and never touch the network. They cover a fetch, then reuse
without a request; resuming from a partial file; a server that ignores ranges; a wrong file refused and a mirror
used; a missing URL; a duplicate manifest entry; a release's downloads and assets; assets from a staged folder
(file:// URLs), a changed staged asset refused, an entry without a URL; and the command line.
