# rec-audio: the AppleHDA stack from the root-patched System (its HAL plug-in is inside AppleHDA.kext, so the trees go
# on disk too); kexts.txt puts the kexts into the rebuilt BaseSystemKernelExtensions.kc.
E=System/Library/Extensions
for t in $E/AppleHDA.kext $E/IOAudioFamily.kext $E/AppleGFXHDA.kext; do
    rm -rf "$BS/$t"; ditto "$SYS/$t" "$BS/$t"
done
