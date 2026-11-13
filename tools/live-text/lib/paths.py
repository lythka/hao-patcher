"""How build records name their inputs: as the repository does, never by an absolute path (a home folder)."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def repo_path(path):
    """PATH relative to the repository (its store/ as store/...), or only its name when it lies outside both."""
    path = Path(path).resolve()
    for base, prefix in (((REPO/'store').resolve(), Path('store')), (REPO.resolve(), Path())):
        try:
            return str(prefix/path.relative_to(base))
        except ValueError:
            pass
    return path.name
