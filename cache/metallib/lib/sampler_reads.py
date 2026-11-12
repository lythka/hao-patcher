"""The sampler-form read repair alone: AIR 2.8 implicit-sampler 2D reads to direct reads, then i16 coordinates
widened where the module also calls the i32 overload (read_sampler.py, then texture_coords.py).

Why only these two passes: on the HD 4000 route the 31001 request downgrader (MTLCompiler 13.6 / GPUCompiler 13.2.1,
OCLP's `metal_3802.py`) lowers every module to AIR 2.0 for the 3802 backend. It crashes (libLLVM_real+0x7f02a9) on
every depth, cube and 1D sampler-form read (the forms without an offset operand, and depth) and writes invalid IR
("Inserted value type doesn't match aggregate type") when one module calls both coordinate widths of the same
sampler-form texture read; 2D array and 3D sampler-form reads it converts itself. The whole suite (compatibility.py) also runs the null-compare and
half-vector passes, which change bytes the downgrader handles; a patch for this one root cause must not.
"""
import compatibility
import read_sampler
import texture_coords


def scan(text):
    return dict(sampler_form_reads=read_sampler.scan(text), mixed_texture_reads=texture_coords.scan(text))


def refuse_bad_state(text):
    compatibility.refuse_duplicate_ssa(text)
    report = scan(text)
    if report['sampler_form_reads'] or report['mixed_texture_reads']:
        raise ValueError('sampler-form or mixed-width reads remain: ' + str(report))
    return report


def rewrite(text):
    text, samplers = read_sampler.rewrite(text)
    text, textures = texture_coords.rewrite(text)
    return text, dict(sampler_form_reads=samplers, mixed_texture_reads=textures)
