#!/usr/bin/env python3
"""Shared HD 4000 AIR lowering suite and single refusing pattern check."""
import null_compare
import mkfix_half_vec_int
import half2_load
import private_half2
import texture_coords
import read_sampler
import re


def scan(text):
    integer = dict(functions=[], roots=0, ops=0, exits=0)
    if re.search(r'bitcast <(?:[3-9]|\d{2,}) x half>', text):
        _, integer = mkfix_half_vec_int.rewrite(text)
    return dict(sampler_form_reads=read_sampler.scan(text), mixed_texture_reads=texture_coords.scan(text), null_compare=null_compare.scan(text),
                half_vector_integer=integer,
                private_half2_load=half2_load.scan(text))


def refuse_duplicate_ssa(text):
    # Refuse generated SSA collisions before invoking an assembler.
    names = set()
    for line in text.splitlines():
        if line.startswith('define '):
            names = set(re.findall(r'%[-\w.$]+(?=,|\))', line))
        match = re.match(r'^  (%[-\w.$]+) = ', line)
        if match:
            if match[1] in names:
                raise ValueError('duplicate SSA definition: ' + match[1])
            names.add(match[1])


def refuse_bad_state(text):
    refuse_duplicate_ssa(text)
    report = scan(text)
    if report['sampler_form_reads'] or report['mixed_texture_reads'] or report['null_compare'] or report['half_vector_integer']['ops'] or report['private_half2_load']:
        raise ValueError('unsupported HD 4000 shader patterns remain: ' + str(report))
    return report


def rewrite(text):
    text, samplers = read_sampler.rewrite(text)
    text, textures = texture_coords.rewrite(text)
    text, nulls = null_compare.rewrite(text)
    integers = dict(functions=[], roots=0, ops=0, exits=0)
    if re.search(r'bitcast <(?:[3-9]|\d{2,}) x half>', text):
        text, integers = mkfix_half_vec_int.rewrite(text)
    loads = dict(loads=0, tables=0, functions=[])
    if half2_load.scan(text):
        text, loads = half2_load.rewrite(text)
        if half2_load.scan(text):
            text, widened = private_half2.rewrite(text)
            loads['widened'] = widened
        if half2_load.scan(text):
            import inline_private
            text, inlining = inline_private.rewrite(text)
            text, selected = half2_load.rewrite(text)
            text, widened = private_half2.rewrite(text)
            loads['inlining'] = inlining
            loads['after_inline_tables'] = selected
            loads['after_inline_widened'] = widened
    refuse_bad_state(text)
    return text, dict(sampler_form_reads=samplers, mixed_texture_reads=textures, null_compare=nulls, half_vector_integer=integers, private_half2_load=loads)
