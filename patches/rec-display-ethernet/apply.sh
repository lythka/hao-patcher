# rec-display-ethernet: the Ivy Bridge framebuffer and HD 4000 kexts from the root-patched System; kexts.txt puts them
# (and the built-in BCM57765 Ethernet) into the rebuilt BaseSystemKernelExtensions.kc.
E=System/Library/Extensions
for t in $E/AppleIntelFramebufferCapri.kext $E/AppleIntelHD4000Graphics.kext; do
    rm -rf "$BS/$t"; ditto "$SYS/$t" "$BS/$t"
done
