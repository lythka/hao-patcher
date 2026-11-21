"""Generate a source-built witness lookup using the target's native policy state."""
import json
from pathlib import Path
import struct
import subprocess
import uuid

from .exports import require
from .runtime_probes import cached_symbols, disk_symbols, unique_symbol, Disassembler
from .swift_metadata import ROOT, pointer_entries, provenance, write_json
from .witness_consumer import gum_devkit, GUM_VERSION


def policy_profile(read, symbols, dis):
    name,metadata=unique_symbol(symbols,'28getLibPrespecializedMetadataEP')
    _,changed=unique_symbol(symbols,'28libPrespecializedImageLoadedEv')
    _,callback=unique_symbol(symbols,'LazyI22LibPrespecializedStateE19defaultInitCallback')
    initializer=symbols[name+'.cold.1']
    state=symbols['__ZL17LibPrespecialized']
    def instructions(address):
        end=min(value for value in symbols.values() if value>address)
        return dis.interval(read(address,end-address),address)[0]
    notifications=instructions(changed)
    disables=[i for i in notifications if i.data[:2]==b'\xc7\x05' and i.data[-4:]==b'\x04\0\0\0']
    require(len(disables)==1,'unsupported Swift image-override mode update')
    mode=disables[0].rip_target
    body=instructions(metadata)
    checks=[i for i in body if i.data[:3]==b'\x48\x83\x3d' and i.data[-1:]==b'\xff']
    require(len(checks)==1,'unsupported Swift lazy initialization check')
    once=checks[0].rip_target
    require(any(i.rip_target==mode and i.data[:2]==b'\x8b\x05' for i in body),
            'Swift metadata lookup does not read the image-override mode')
    init=instructions(initializer)
    # The target's outlined Lazy::get initializer passes token, storage, and
    # constructor callback to once_f. Reuse it without reconstructing Lazy<T>.
    arguments={i.data[:3]:i.rip_target for i in init if i.data[:2]==b'\x48\x8d'}
    require(arguments=={b'\x48\x8d\x3d':once,b'\x48\x8d\x35':state,b'\x48\x8d\x15':callback},
            'unsupported Swift outlined lazy initializer arguments')
    require(any(i.direct_target==initializer for i in body),'Swift metadata lookup does not call its initializer')
    return dict(once=once,mode=mode,initialize=initializer,state=state,
                metadata_lookup=metadata,override_callback=changed)


def profile(family):
    _,roots=pointer_entries(family)  # This backport's policy ABI is the v1.4 runtime.
    symbols=cached_symbols(family,'/usr/lib/swift/libswiftCore.dylib')
    dyld=cached_symbols(family,'/usr/lib/system/libdyld.dylib')
    dis=Disassembler()
    try:
        result=policy_profile(family.read,symbols,dis)
        _,entry=unique_symbol(symbols,'16_getWitnessTableEP')
        return dict(schema=1,kind='swift-witness-runtime',base=roots['base'],
            cache_uuid=str(uuid.UUID(bytes=bytes(family.main.data[88:104]))),
            entry=entry,metatype_entry=symbols['_swift_getMetatypeMetadata'],lookup=dyld['__dyld_find_pointer_hash_table_entry'],**result)
    finally:dis.close()


def prepare(devkit,output):
    from .artifacts import helper
    relocator = helper('relocate')
    obj = helper('runtime-object')
    if relocator is None or obj is None:
        require(devkit is not None, 'developer runtime production requires a Frida devkit')
        return produce(devkit, output)
    import shutil
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(relocator, output/'relocate')
    shutil.copy2(obj, output/'runtime.o')
    write_json(output/'build.json', provenance([relocator, obj], operation='swift-witness-runtime-toolchain'))
    return output


def produce(devkit,output):
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    gum=gum_devkit(devkit,output)
    # The amalgamated devkit already contains these public declarations. The
    # unchanged upstream relocator header references their standalone names.
    for name in ('gumx86writer.h','capstone.h'):
        (gum/name).write_text('#include "frida-gum.h"\n')
    vendor=ROOT/'vendor/frida-gum'
    relocate=ROOT/'tools/swift-witness-relocate.c'
    runtime=ROOT/'tools/swift-witness-runtime.c'
    flags=['xcrun','clang','-arch','x86_64','-mmacosx-version-min=14.0','-O2','-Wall','-Wextra','-Werror']
    commands=[flags+['-Wl,-reproducible','-ffile-prefix-map='+str(output)+'=/build','-I',str(gum),'-I',str(vendor),str(relocate),'-L',str(gum),'-lfrida-gum','-lresolv','-o',str(output/'relocate')],
        ['codesign','-s','-',str(output/'relocate')],
        flags+['-ffreestanding','-fno-stack-protector','-fno-asynchronous-unwind-tables','-fvisibility=hidden',
               '-c',str(runtime),'-o',str(output/'runtime.o')]]
    with (output/'compile.log').open('w') as log:
        for command in commands:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    write_json(output/'build.json',provenance([devkit,relocate,runtime,ROOT/'tools/swift-witness-runtime.h',
        vendor/'gumx86relocator.h',vendor/'COPYING',Path(__file__),Path(__file__).with_name('witness_consumer.py')],
        operation='swift-witness-runtime-toolchain',architecture='x86_64',frida_gum=GUM_VERSION,commands=commands))
    return output


def link(toolchain,code_address,data_address,output):
    from .artifacts import helper
    from .witness_template import emit
    template = helper('witness-template')
    if template is not None:
        return emit(template, toolchain / 'runtime.o', code_address, data_address)
    return developer_link(toolchain, code_address, data_address, output)


def developer_link(toolchain,code_address,data_address,output):
    """Let the platform linker resolve all code-to-configuration references."""
    binary=output/'runtime.dylib'
    command=['xcrun','clang','-arch','x86_64','-dynamiclib','-mmacosx-version-min=14.0',
             '-Wl,-no_fixup_chains','-Wl,-no_uuid',f'-Wl,-segaddr,__TEXT,{code_address:#x}',
             f'-Wl,-segaddr,__DATA,{data_address:#x}','-Wl,-install_name,/swift-witness-runtime',
             str(toolchain/'runtime.o'),'-o',str(binary)]
    subprocess.run(command,check=True,capture_output=True)
    raw=binary.read_bytes();position=32;segments={}
    for _ in range(struct.unpack_from('<I',raw,16)[0]):
        kind,size=struct.unpack_from('<II',raw,position)
        if kind==0x19:
            name=raw[position+8:position+24].rstrip(b'\0').decode()
            segments[name]=struct.unpack_from('<4Q',raw,position+24)
        if kind==0x80000022:
            require(not any(struct.unpack_from('<8I',raw,position+8)),
                    'witness runtime unexpectedly needs dynamic binding or rebasing')
        position+=size
    text,text_size,file_offset,file_size=segments['__TEXT']
    data=segments['__DATA'][0]
    symbols,_=disk_symbols(binary)
    require((text,data)==(code_address,data_address),'linker changed witness runtime placement')
    return raw[file_offset:file_offset+file_size].ljust(text_size,b'\0'),symbols,command


def recipe(family,toolchain,layout,output,*,metatypes=False,metadata_shapes=None):
    output.mkdir(parents=True,exist_ok=False)
    p=profile(family)
    code_address=layout['swift_runtime_code']['address']
    data_address=layout['swift_runtime_data']['address']
    blob,symbols,command=link(toolchain,code_address,data_address,output)
    from .artifacts import helper
    relocator = helper('relocate') or toolchain/'relocate'
    patches,relocations,pointers=[],{},[]
    data_size=0
    families=[('witness',p['entry'],layout['swift_witness_table']['address'])]
    if metatypes:
        families.append(('metatype',p['metatype_entry'],layout['swift_metatype_table']['address']))
    if metadata_shapes:
        families.append(('metadata',p['metadata_lookup'],layout['swift_normalized_table']['address']))
    shape_address=data_address+4096
    scalar_fields=[]
    for name,entry,table in families:
        wrapper=symbols['_hao_'+name+'_lookup']
        config=symbols['_hao_'+name+'_runtime']
        blob+=bytes(-len(blob)%16)
        trampoline=code_address+len(blob)
        source=output/(name+'-entry.bin');source.write_bytes(family.read(entry,256))
        def relocate(slide,suffix):
            patch=output/(name+'-entry-patch'+suffix+'.bin');fallback=output/(name+'-fallback'+suffix+'.bin')
            result=subprocess.run([str(relocator),str(source),hex(entry+slide),hex(wrapper+slide),
                hex(trampoline+slide),str(patch),str(fallback)],check=True,capture_output=True,text=True)
            return patch.read_bytes(),fallback.read_bytes(),json.loads(result.stdout)
        patch,fallback,relocation=relocate(0,'')
        shifted_patch,shifted_fallback,_=relocate(0x100000,'-slid')
        require((patch,fallback)==(shifted_patch,shifted_fallback),'relocated '+name+' entry contains absolute addresses')
        blob+=fallback
        fields=[p['once'],p['initialize'],p['mode'],table,p['lookup']]
        if name=='metatype':fields.append(entry)
        fields.append(trampoline)
        if name=='metadata':
            fields=[p['mode'],table,p['lookup'],trampoline,shape_address]
            scalar_fields.append((config-data_address+40,struct.pack('<Q',len(metadata_shapes))))
        require(config>=data_address,'runtime configuration precedes data reservation')
        data_size=max(data_size,config-data_address+len(fields)*8)
        pointers.extend(dict(address=config+8*i,target=target) for i,target in enumerate(fields))
        patches.append(dict(address=entry,before=source.read_bytes()[:len(patch)].hex(),after=patch.hex()))
        relocations[name]=dict(wrapper=wrapper,trampoline=trampoline,relocation=relocation)
    if metadata_shapes:
        require(data_size<=4096,'Swift runtime configuration overlaps metadata shapes')
        for i,(descriptor,(types,count)) in enumerate(sorted(metadata_shapes.items())):
            address=shape_address+i*16
            pointers.append(dict(address=address,target=p['base']+descriptor))
            scalar_fields.append((address-data_address+8,struct.pack('<II',types,count)))
        data_size=4096+len(metadata_shapes)*16
    require(len(blob)<=layout['swift_runtime_code']['reserve'] and data_size<=layout['swift_runtime_data']['reserve'],
            'Swift runtime exceeds producer reservation')
    data=bytearray(data_size)
    for offset,value in scalar_fields:data[offset:offset+len(value)]=value
    result=dict(schema=1,profile=p,link_command=command,families=relocations,
        blobs=[dict(reservation='swift_runtime_code',address=code_address,data=blob.hex()),
               dict(reservation='swift_runtime_data',address=data_address,data=data.hex())],
        new_pointer_fields=pointers,byte_edits=patches,pointer_edits=[])
    # make runtime-probes --runtime-recipe reads this to probe the redirected lookups (cache-use measurement).
    write_json(output/'recipe.json',result)
    return result
