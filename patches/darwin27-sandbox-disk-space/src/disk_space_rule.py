"""Add macOS 27.2's disk-space rule to a system.sb that lacks it: disk_space_rule.py SYSTEM_SB OUT.

27.2 (26B123) added exactly this block after the libmalloc block, and nothing else, to system.sb. A file that already
allows the info type is written unchanged; a file without the libmalloc block stops with an error.
"""
import sys

ANCHOR = (';;; libmalloc requirements\n'
          '(allow syscall-mig\n'
          '       (kernel-mig-routine mach_vm_reallocate\n'
          '                           vm_reallocate))\n')
RULE = ('\n'
        ';;; Disk space reporting ({,f}statfs(), getattrlist() ATTR_VOL_SPACE*)\n'
        '(allow system-info\n'
        '       (info-type "vfs.disk-space"))\n')


def add_rule(text):
    if '(info-type "vfs.disk-space")' in text:
        return text
    if text.count(ANCHOR) != 1:
        raise ValueError('system.sb: the libmalloc block occurs %d times, expected once' % text.count(ANCHOR))
    return text.replace(ANCHOR, ANCHOR + RULE)


def main(argv):
    if len(argv) != 3:
        raise SystemExit('usage: disk_space_rule.py SYSTEM_SB OUT')
    with open(argv[1], encoding='utf-8', newline='') as f:
        text = f.read()
    out = add_rule(text)
    with open(argv[2], 'w', encoding='utf-8', newline='') as f:
        f.write(out)
    print('system.sb: %s' % ('rule already present' if out == text else 'disk-space rule added'))


if __name__ == '__main__':
    main(sys.argv)
