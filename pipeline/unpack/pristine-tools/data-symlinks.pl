#!/usr/bin/perl
# data-symlinks.pl DATAROOT TSV : replace template entries with relative symlinks (path<TAB>target), as templateMigrator does
use strict; use warnings; use File::Path qw(remove_tree make_path); use File::Basename;
my ($root, $f) = @ARGV; open(my $h, '<', $f) or die "open $f: $!"; my ($n, $rm, $err) = (0, 0, 0);
while (<$h>) { chomp; my ($p, $t) = split /\t/; my $d = "$root/$p";
  if (-l $d) { unlink $d } elsif (-d $d) { remove_tree($d); $rm++ } elsif (-e $d) { unlink $d; $rm++ }
  make_path(dirname($d)) unless -d dirname($d);
  if (symlink($t, $d)) { $n++ } else { $err++; print STDERR "ERR $p: $!\n" } }
print "symlinks=$n replaced=$rm err=$err\n";
