# hao-contract

Checks the files that pass between the hao repositories: hao-runtime's `generated.lock` and payload
`payload-manifest.json`, and public hygiene (no home folders or private names in tracked files or commit
messages). Standard library only, Python 3.9 or later. This is the checker's home; other repositories vendor a
pinned copy and declare their checks in their own `contract.json`.

```sh
python3 -I tools/hao-contract/hao_contract.py check REPO      # the checks REPO/contract.json names
python3 -I tools/hao-contract/hao_contract.py message FILE    # hygiene on one commit message
python3 -I tools/hao-contract/hao_contract.py --version
```

Private names are not in this repository. The hygiene check reads them from `$HAO_CONTRACT_PRIVATE` or
`~/.config/hao-contract/private-names`, one `LABEL: TEXT` per line; without that file it checks home folders only.
The labels are the ones a `contract.json` `allow` list names.

Schemas for `contract.json`, `generated.lock` and `payload-manifest.json` are in [`schemas/`](schemas); the tests
keep them in step with the checker.

```sh
python3.14 -I -B -m unittest discover -s tools/hao-contract/tests
```
