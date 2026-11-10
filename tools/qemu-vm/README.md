# Test an image in QEMU

Boot a built image through a disposable overlay before installing it on hardware.
The source image and supplied EFI remain unchanged.

```sh
python3.14 -I tools/qemu-vm/qemu-vm start \
  --image /path/to/target.img --esp /path/to/efi --out /path/to/new-run
python3.14 -I tools/qemu-vm/qemu-vm stop /path/to/new-run
```

`--esp` is an OpenCore EFI folder for QEMU, such as the `qemu_esp_hao` input
row made by `tools/kernel-foundation make vm-esp`.

Requirements: macOS, Python 3.14, QEMU and its EDK II firmware. The recorded
configuration uses Q35/TCG with an Ivy Bridge CPU. `--ssh-port`, `--memory` and
`--boot-arg` override per-run settings; SSH requires an image configured to run sshd.
The reduced Golden Gate profile does not install development SSH services.

Each run records the command, input identities, serial log, process state and
writable overlay. `logs RUN` extracts guest logs from a stopped run; `retire RUN
--note TEXT` preserves its records before retiring temporary storage. Read the
command's `--help` before these operations.

## Verification

```sh
python3.14 -I -B -m unittest discover -s tools/qemu-vm/tests
```

Host fixtures exercise commands and lifecycle behavior without starting QEMU.
A successful boot is a separate check, and does not prove physical graphics,
sleep or application compatibility.
