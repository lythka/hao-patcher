# dev-access: root SSH on the test laptop for bench runs (dev set only). launchd starts sshd per connection on port 22
# with its own config: root by the one public key in inputs, no passwords. Apple's Remote Login setting is untouched.
T=$DATA/private/var/root/.testssh
install -d -o root -g wheel -m 700 $T
install -o root -g wheel -m 600 $P/inputs/test_host_key $T/ssh_host_ed25519_key
install -o root -g wheel -m 600 $P/inputs/test_authorized_key $T/authorized_keys
install -o root -g wheel -m 644 $P/src/sshd_config $T/sshd_config
install -d -o root -g wheel -m 755 $DATA/Library/LaunchDaemons
install -o root -g wheel -m 644 $P/src/local.testssh.plist $DATA/Library/LaunchDaemons/local.testssh.plist
ls -la $T $DATA/Library/LaunchDaemons/local.testssh.plist
