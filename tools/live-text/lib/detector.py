"""Copy TextRecognition's text detector and rewrite only its input binding.

Apple's cr_td_model_v3_e5 takes a 480x480 BGRA pixel_buffer. On the HD 4000 that binding fails, so the copy takes
the same bytes as a uint8 tensor_buffer [480, 480, 4] (row bytes 1920) and transposes it to the [4, 480, 480]
tensor the rest of the model already uses. Weights, casts, channel selection and normalization are unchanged:
every other file is copied byte for byte, and model.mil changes in exactly these two statements.
"""
import hashlib
from pathlib import Path

PIXEL_INPUT = 'pixel_buffer<FMT_32BGRA, shape=[480, 480]> img_input'
TENSOR_INPUT = 'tensor_buffer<uint8, shape=[480, 480, 4], strides=[1920, 4, 1], interleave_factors=[1, 1, 1]> img_input'
PIXEL_TO_TENSOR = 'tensor<uint8, [4, 480, 480]> img_input_tensor_raw = pixel_buffer_to_tensor(input=img_input);'
TRANSPOSE = '''tensor<uint8, [480, 480, 4]> diagnostic_bgra_hwc = tensor_buffer_to_tensor(input=img_input);
        tensor<int32, [3]> diagnostic_bgra_perm = const()[name=string("diagnostic_bgra_perm"), val=tensor<int32, [3]>([2, 0, 1])];
        tensor<uint8, [4, 480, 480]> img_input_tensor_raw = transpose(x=diagnostic_bgra_hwc, perm=diagnostic_bgra_perm);'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rewrite(text):
    for old in (PIXEL_INPUT, PIXEL_TO_TENSOR):
        if text.count(old) != 1:
            raise ValueError('MISSING/unsupported: detector model.mil does not contain exactly one %r' % old)
    return text.replace(PIXEL_INPUT, TENSOR_INPUT).replace(PIXEL_TO_TENSOR, TRANSPOSE)


def prepare(source, output):
    """Copy SOURCE (a cr_td_model_v3_e5.mlmodelc.bundle) to the new folder OUTPUT; return the input record."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or source in output.parents:
        raise ValueError('detector output must be outside its source')
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for original in sorted(source.rglob('*')):
        target = output/original.relative_to(source)
        if original.is_symlink():
            raise ValueError('unexpected symlink in detector bundle: %s' % original)
        if original.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        files[str(original.relative_to(source))] = sha(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    mil = output/'model.mil'
    mil.write_text(rewrite(mil.read_text()))
    return dict(path=str(source), files=files, model_mil_sha256=sha(mil))
