"""Copy hook code/owned inputs, leaving declared read-only inputs on the build share."""
import argparse
from pathlib import Path
import subprocess


def copy_hooks(source, target, owner=None):
    shared = []
    for declaration in source.glob('*/read-from-share.txt'):
        for line in declaration.read_text().splitlines():
            name = line.split('#', 1)[0].strip()
            if name:
                shared.append(Path(declaration.parent.name)/'inputs'/name)
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    command = ['tar', '-C', str(source), '-cf', '-']
    command.extend('--exclude=./'+str(path) for path in shared)
    command.append('.')
    producer = subprocess.Popen(command, stdout=subprocess.PIPE)
    consumer = subprocess.Popen(['tar', '-C', str(target), '-xf', '-'], stdin=producer.stdout)
    producer.stdout.close()
    consumed, produced = consumer.wait(), producer.wait()
    if produced or consumed:
        raise RuntimeError(f'Hook copy failed: producer={produced}, consumer={consumed}')
    if owner:
        subprocess.run(['chown', '-R', owner, str(target)], check=True)
    for relative in shared:
        (target/relative).symlink_to(source/relative)
        print('Read-only shared hook input:', relative, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    parser.add_argument('--owner')
    args = parser.parse_args()
    copy_hooks(args.source, args.target, args.owner)
