# rec-wifi: OCLP's Modern Wireless IO80211.framework from the root-patched System (the kexts come from OpenCore).
t=System/Library/PrivateFrameworks/IO80211.framework
rm -rf "$BS/$t"; ditto "$SYS/$t" "$BS/$t"
