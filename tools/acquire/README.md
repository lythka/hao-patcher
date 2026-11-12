# acquire

Downloads every file a release pins: each by URL (plus optional mirrors), size and SHA-256. Workflow plan
step 12. Apple's installers and KDKs are never published by this project; the builder fetches them from their
own servers on the user's Mac.

```sh
python3.14 -I tools/acquire/acquire fetch release.json --cache CACHE      # download what is missing
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

Manifest (the `downloads` part of a `release.json`):

```json
{"downloads": [{"name": "macos-27.2.1-26B131-installer", "url": "https://…", "size": 14812043264,
                "sha256": "…", "mirrors": ["https://…"]}]}
```

Not done yet:

- **Decoding and unpacking:** the pinned `ipsw` binary for AEA decoding and the prebuilt unpack tools.
- **The host matrix:** which steps work on macOS 12, 13 and 14 and later, on Intel and Apple silicon.
- **Real URLs and hashes:** `release cut` writes them (step 13).

## Verify

```sh
python3.14 -I -B -m unittest discover -s tools/acquire/tests
```

The tests serve files from a local HTTP server and never touch the network. They cover a fetch, then reuse
without a request; resuming from a partial file; a server that ignores ranges; a wrong file refused and a mirror
used; a missing URL; a duplicate manifest entry; and the command line.
