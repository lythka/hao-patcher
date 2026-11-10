"""Measured host-test specializations. These are not native caller profiles."""

BVH_INPUT = '25a8e71b19d2f59f7cd96f3d3c196fd23b6dbd4a08247f2287e5e51adb44ef66'
BVH_FUNCTIONS = frozenset(
    ['binFragmentsSpatialKernel'] +
    ['dispatchBins%sKernel%d' % (wide, size)
     for wide in ('', 'Wide') for size in (32, 64, 128, 256)] +
    ['splitFragmentsKernel%d' % size for size in (32, 64, 128, 256, 512)])


def controls_for(input_sha256, function):
    controls = {'MPSMaxBatchSize': 1}
    if input_sha256 == BVH_INPUT and function in BVH_FUNCTIONS:
        controls['threadsPerThreadgroup'] = 32
    return controls
