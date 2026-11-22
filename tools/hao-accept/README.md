# hao-accept, hao-check

Acceptance of a candidate the way a user installs it: `hao-accept` writes the USB installer to a test Mac's drive,
runs `hao install` from its Recovery, boots once and checks the result (`laptop`), or does the same in QEMU (`run`);
`hao-check` gives the per-app and behaviour table of a dev install with a logged-in session.

```sh
python3.14 -I tools/hao-accept/hao-accept laptop --laptop NAME --stick STICK.img --out DIR [--then-check]
python3.14 -I tools/hao-accept/hao-accept run ...          # the same checks in QEMU (tools/qemu-vm)
python3.14 -I tools/hao-accept/hao-check run ...
python3.14 -I -B -m unittest discover -s tools/hao-accept/tests
```

They reach the test Macs through commands kept outside this repository, each named by an environment variable:

| Variable | What |
|---|---|
| `HAO_LAPTOP_SSH` | runs one command as root on a test Mac (`LAPTOP_NAME` picks which) |
| `HAO_LAPTOP_GUI` | installs the GUI helper that takes screenshots and clicks on a test Mac |
| `HAO_LAPTOP_TOOLS` | the folder of the remaining test-Mac commands |
| `HAO_LOCK_TOOL` | takes and releases a test Mac, so two runs never share one |
| `HAO_DISK_TOOL` | checks this Mac's free space before a large run |
| `HAO_NETBOOT_WAKE` | the file that wakes a test Mac sleeping in its network Recovery |
| `HAO_ACCEPT_DRIVES` | a map of each test Mac's installer drive (`NAME<TAB>lun<TAB>N`) |
| `HAO_QEMU_VM`, `HAO_QEMU_ESP` | default: this checkout's `tools/qemu-vm/qemu-vm` and `store/artifacts/qemu-esp-26B131-bootstrap` |
