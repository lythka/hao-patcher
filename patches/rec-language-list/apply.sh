# rec-language-list: the Language Chooser shows its list as soon as it is ready. Apple's binary moves to
# "Language Chooser.real" in its own bundle; lcwrap takes its path and runs it with lcfast.dylib inserted.
M="$BS/System/Library/CoreServices/Language Chooser.app/Contents/MacOS"
mv "$M/Language Chooser" "$M/Language Chooser.real"
install -o root -g wheel -m 755 $P/build/lcwrap "$M/Language Chooser"
install -o root -g wheel -m 755 $P/build/lcfast.dylib "$M/lcfast.dylib"
ls -la "$M"
