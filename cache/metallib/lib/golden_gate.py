"""27.0 compatibility suite, assembled without the frontend's AIR upgrade pass."""
import compatibility
import legacy_sync
import re
import null_compare
import winograd_matrix
import winograd_offsets


def inline_blend_callers(text):
    """Expose constant shader state before the legacy backend resolves externals.

    Large primitive image/gradient helpers otherwise retain dynamic state
    arguments through specialization, including their unreachable blend call.
    Only internal direct callers of this reference receive the inline attribute.
    """
    lines = text.split('\n')
    functions, calls = null_compare.read_functions(lines)
    pending, selected = ['custom_blend.MTL_VISIBLE_FN_REF'], set()
    while pending:
        for caller, _, _ in calls[pending.pop()]:
            if caller not in selected and functions[caller]['internal']:
                selected.add(caller)
                pending.append(caller)
    for name in selected:
        index = functions[name]['header']
        if ' alwaysinline ' not in lines[index]:
            lines[index], count = re.subn(r' (#\d+)', r' alwaysinline \1', lines[index], count=1)
            if count != 1:
                raise ValueError('blend caller lacks an attribute group: ' + name)
    return '\n'.join(lines), sorted(selected)


def defer_renderbox_blend_reference(text):
    """Keep the external call, but let specialization discard an unused blend.

    31001 eagerly resolves air.visible_function_references even when RenderBox's
    shader-state bit disables custom blending and the caller supplies no helper.
    The external declaration/calls still link normally when a helper is supplied.
    Do not remove custom_fn or other references, or change any shader instructions.
    """
    if '_ZN2RB6Shader8Constant13_shader_stateE' not in text:
        return text, 0
    nodes = set(re.findall(
        r'^!(\d+) = !\{!"air.visible_function_reference", [^\n]*'
        r'@custom_blend\.MTL_VISIBLE_FN_REF, !"custom_blend"\}$', text, re.M))
    removed = 0

    def replace(match):
        nonlocal removed
        refs = re.findall(r'!(\d+)', match[1])
        kept = [ref for ref in refs if ref not in nodes]
        removed += len(refs) - len(kept)
        return ('!air.visible_function_references = !{' +
                ', '.join('!' + ref for ref in kept) + '}\n') if kept else ''

    text = re.sub(r'^!air.visible_function_references = !\{([^\n]*)\}\n',
                  replace, text, flags=re.M)
    return text, removed


def rewrite(text):
    text, report = compatibility.rewrite(text)
    text, report['legacy_sync'] = legacy_sync.rewrite(text)
    if re.search(r'^define void @cnnConvWinograd_', text, re.M):
        text, report['winograd_matrix'] = winograd_matrix.rewrite(text)
        text, report['winograd_offsets'] = winograd_offsets.rewrite(text)
    # These motion-vector builders select one of eight neighbors, or the center,
    # through a private offset table and loop phis, just like dilateFlow.
    if re.search(r'^define void @(?:dilateFlow|(?:bbrnet|brnetv3)_build_tensors_low_resolution)\(', text, re.M):
        text, report['dilate_offsets'] = winograd_offsets.rewrite(text, include_private_tables=True)
    text, report['deferred_blend_references'] = defer_renderbox_blend_reference(text)
    if report['deferred_blend_references']:
        text, report['inline_blend_callers'] = inline_blend_callers(text)
    refuse_bad_state(text)
    return text, report


def refuse_bad_state(text):
    compatibility.refuse_bad_state(text)
    legacy_sync.refuse_bad_state(text)
