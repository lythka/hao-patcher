"""
utilities.py: Supporting functions for file handling during root volume patching
"""

import os
import logging
import subprocess

from pathlib import Path




# Ported OCLP file operations, with local source bindings. See provenance.json.
import runpy
from enum import Enum

generate_copy_arguments = runpy.run_path(str(Path(__file__).with_name('volume.py')))['generate_copy_arguments']

class PatchType(str, Enum):
    MERGE_SYSTEM_VOLUME = 'Merge System Volume'
    MERGE_DATA_VOLUME = 'Merge Data Volume'

class subprocess_wrapper:
    owner = 'root:wheel'

    @classmethod
    def run_as_root(cls, command, **kwargs):
        command = list(command)
        if command[0] == '/usr/sbin/chown':
            command = [cls.owner if part == 'root:wheel' else part for part in command]
        # An unsuccessful copy must fail the stage, not leave a claimed installed map.
        return subprocess.run(command, check=True, **kwargs)

    run_as_root_and_verify = run_as_root

_RSYNC_USABLE = None


def rsync_usable() -> bool:
    """
    Whether /usr/bin/rsync runs. recoveryOS ships an rsync whose library is missing,
    callers merge with ditto there instead (same result: existing files overwritten, nothing deleted).
    """
    global _RSYNC_USABLE
    if _RSYNC_USABLE is None:
        try:
            _RSYNC_USABLE = subprocess.run(["/usr/bin/rsync", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0
        except OSError:
            _RSYNC_USABLE = False
        if _RSYNC_USABLE is False:
            logging.info("- rsync not usable, merging with ditto")
    return _RSYNC_USABLE


def install_new_file(source_folder: Path, destination_folder: Path, file_name: str, method: PatchType) -> None:
    """
    Installs a new file to the destination folder

    File handling logic:
    - PatchType.MERGE_* are merged with the destination folder
    - Other files are deleted and replaced

    Parameters:
        source_folder      (Path): Path to the source folder
        destination_folder (Path): Path to the destination folder
        file_name           (str): Name of the file to install
    """

    file_name_str = str(file_name)

    if not Path(destination_folder).exists():
        logging.info(f"  - Skipping {file_name}, cannot locate {source_folder}")
        return

    if method in [PatchType.MERGE_SYSTEM_VOLUME, PatchType.MERGE_DATA_VOLUME]:
        # merge with rsync
        logging.info(f"  - Installing: {file_name}")
        if rsync_usable():
            subprocess_wrapper.run_as_root(["/usr/bin/rsync", "-r", "-i", "-a", f"{source_folder}/{file_name}", f"{destination_folder}/"], stdout=subprocess.PIPE)
        else:
            subprocess_wrapper.run_as_root(["/usr/bin/ditto", f"{source_folder}/{file_name}", f"{destination_folder}/{file_name}"], stdout=subprocess.PIPE)
        fix_permissions(destination_folder + "/" + file_name, source=f"{source_folder}/{file_name}")
    elif Path(source_folder + "/" + file_name_str).is_dir():
        # Applicable for .kext, .app, .plugin, .bundle, all of which are directories
        if Path(destination_folder + "/" + file_name).exists():
            logging.info(f"  - Found existing {file_name}, overwriting...")
            subprocess_wrapper.run_as_root_and_verify(["/bin/rm", "-R", f"{destination_folder}/{file_name}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        else:
            logging.info(f"  - Installing: {file_name}")
        subprocess_wrapper.run_as_root_and_verify(generate_copy_arguments(f"{source_folder}/{file_name}", destination_folder), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fix_permissions(destination_folder + "/" + file_name)
    else:
        # Assume it's an individual file, replace as normal
        if Path(destination_folder + "/" + file_name).exists():
            logging.info(f"  - Found existing {file_name}, overwriting...")
            subprocess_wrapper.run_as_root_and_verify(["/bin/rm", f"{destination_folder}/{file_name}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        else:
            logging.info(f"  - Installing: {file_name}")
        subprocess_wrapper.run_as_root_and_verify(generate_copy_arguments(f"{source_folder}/{file_name}", destination_folder), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fix_permissions(destination_folder + "/" + file_name)


def remove_file(destination_folder: Path, file_name: str) -> None:
    """
    Removes a file from the destination folder

    Parameters:
        destination_folder (Path): Path to the destination folder
        file_name           (str): Name of the file to remove
    """

    if Path(destination_folder + "/" + file_name).exists():
        logging.info(f"  - Removing: {file_name}")
        if Path(destination_folder + "/" + file_name).is_dir():
            subprocess_wrapper.run_as_root_and_verify(["/bin/rm", "-R", f"{destination_folder}/{file_name}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        else:
            subprocess_wrapper.run_as_root_and_verify(["/bin/rm", f"{destination_folder}/{file_name}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def fix_permissions(destination_file: Path, source: Path = None) -> None:
    """
    Fix file permissions for a given file or directory

    With `source` (a merge into a folder that already exists), only the entries that came from `source` are
    changed, and never a file with more than one link: a recursive chmod of the destination would also reach
    files that were already there, and on a sealed System many of those are hard links shared with the rest
    of the OS (one CodeResources inode has thousands of links), so their mode would change everywhere.
    """
    if source is not None and Path(source).is_dir():
        paths = [str(destination_file)]
        for root, dirs, files in os.walk(source):
            rel = Path(root).relative_to(source)
            for name in dirs + files:
                if Path(root, name).is_symlink():
                    continue
                target = Path(destination_file) / rel / name
                if target.is_file() and not target.is_symlink() and os.lstat(target).st_nlink > 1:
                    continue
                paths.append(str(target))
        for i in range(0, len(paths), 500):
            subprocess_wrapper.run_as_root_and_verify(["/bin/chmod", "755", *paths[i:i + 500]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            subprocess_wrapper.run_as_root_and_verify(["/usr/sbin/chown", "root:wheel", *paths[i:i + 500]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return

    chmod_args = ["/bin/chmod",      "-Rf", "755", destination_file]
    chown_args = ["/usr/sbin/chown", "-Rf", "root:wheel", destination_file]
    if not Path(destination_file).is_dir():
        # Strip recursive arguments
        chmod_args.pop(1)
        chown_args.pop(1)
    subprocess_wrapper.run_as_root_and_verify(chmod_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    subprocess_wrapper.run_as_root_and_verify(chown_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
