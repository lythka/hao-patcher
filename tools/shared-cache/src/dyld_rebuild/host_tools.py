"""Compile small host adapters against a source-built dyld toolchain."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BUILDER_OBJECTS = 'objects/dyld.build/Release/libslc_builder.dylib.build'


def toolchain_arch(base):
    """The architecture toolchain/make built BASE for (arm64 on Apple silicon, x86_64 on Intel): the one folder
    under the builder's Objects-normal."""
    normal = Path(base) / BUILDER_OBJECTS / 'Objects-normal'
    found = [p.name for p in normal.iterdir() if p.is_dir()] if normal.is_dir() else []
    if len(found) != 1:
        raise RuntimeError(f'toolchain {base}: expected one architecture under Objects-normal, found {sorted(found)}')
    return found[0]


def builder_objects(base):
    """The cache builder's object folder of toolchain BASE, for its architecture."""
    return Path(base) / BUILDER_OBJECTS / 'Objects-normal' / toolchain_arch(base)


def compile_adapter(directory, name, sources, replacements=()):
    from .artifacts import helper
    pinned = helper(name, Path(directory) / name)
    return pinned if pinned is not None else _compile_adapter(directory, name, sources, replacements)


def _compile_adapter(directory, name, sources, replacements=()):
    base = Path(os.environ['CACHE_TOOLCHAIN']).resolve()
    tree = base / 'source'
    directory = Path(directory)
    directory.mkdir(parents=True)
    objects = base / BUILDER_OBJECTS
    arch = toolchain_arch(base)
    flags = ['xcrun', 'clang++', '-arch', arch, '-std=c++20', '-fblocks', '-fno-exceptions', '-fno-rtti',
             '-Wl,-reproducible', '-g0', '-ffile-prefix-map=' + str(directory) + '=/build',
             '-DINTERNAL_BUILD=1', '-DBUILDING_CACHE_BUILDER=1', '-DBUILDING_MRM_CACHE_BUILDER=1',
             '-DBUILDING_MACHO_WRITER=1', '-DBOM_SUPPORT=1', '-DOBJC_OLD_DISPATCH_PROTOTYPES=0']
    flags += ['-I' + str(tree / p) for p in ('include', 'include/mach-o', 'dyld', 'common',
                                         'mach_o', 'cache_builder', 'lsl', 'ld', 'libdyld', 'framework')]
    flags += ['-I' + str(base / 'products/Release/include'), '-I' + str(objects / 'DerivedSources')]
    compiled = []
    for source in sources:
        target = directory / (Path(source).stem + '.o')
        subprocess.run([*flags, '-c', str(source), '-o', str(target)], check=True)
        compiled.append(str(target))
    linked = [str(p) for p in sorted(builder_objects(base).glob('*.o'))
              if p.name not in replacements]
    linked += [str(base / 'products/Release' / n) for n in ('libmach_o.a', 'libmach_o_writer.a')]
    sdk = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    binary = directory / name
    subprocess.run(['xcrun', 'clang++', '-arch', arch, '-Wl,-dead_strip', '-Wl,-reproducible', *compiled, *linked,
                    '-framework', 'Foundation', '-framework', 'Bom',
                    '-F' + sdk + '/System/Library/PrivateFrameworks', '-o', str(binary)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none', str(binary)], check=True)
    return binary


def loader(directory):
    from .artifacts import helper
    pinned = helper('generator', Path(directory) / 'build/generator')
    return pinned if pinned is not None else _build_loader(directory)


def _build_loader(directory):
    from .loader_generation import prepare_adapter, prepare_cache_path_adapter
    tree = Path(os.environ['CACHE_TOOLCHAIN']) / 'source'
    directory = Path(directory)
    directory.mkdir(parents=True)
    jit = directory / 'JustInTimeLoader.cpp'
    jit.write_text(prepare_adapter((tree / 'dyld/JustInTimeLoader.cpp').read_text()))
    config = directory / 'DyldProcessConfig.cpp'
    config.write_text(prepare_cache_path_adapter((tree / 'dyld/DyldProcessConfig.cpp').read_text()))
    return _compile_adapter(directory / 'build', 'generator',
                           [ROOT / 'tools/loader-generator/generator.cpp', jit, config],
                           ('JustInTimeLoader.o', 'DyldProcessConfig.o'))


def objc(directory):
    return compile_adapter(directory, 'objc-writer', [ROOT / 'tools/objc-table-writers/writer.cpp'])
