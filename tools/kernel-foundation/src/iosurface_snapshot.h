/* Snapshot serialized IOSurface property inputs before the donor parser reads them.
 * Tahoe maps out-of-line inputs with kIOMemoryMapCopyOnWrite. Darwin 27 refuses that
 * mapping for CoW-defeatured clients when the source is, for example, malloc memory.
 * Keep the parser and its validation; give it private bytes through its existing
 * inline-input path. Backend supplies allocation, descriptor I/O and the original.
 */
#ifndef IOSURFACE_SNAPSHOT_H
#define IOSURFACE_SNAPSHOT_H

template<class Backend, class Arguments>
static int iosurface_snapshot(Backend &host, Arguments *arguments) {
    auto descriptor = arguments->structureInputDescriptor;
    if (!descriptor) return host.original(arguments);
    const auto length = host.length(descriptor);
    // The donor parser accepts a uint32_t length and requires its 12-byte header
    // plus at least one serialized byte. Check before narrowing or allocating.
    if (length < 13 || length > 0xffffffffULL) return host.bad_argument();
    auto buffer = static_cast<unsigned char *>(host.allocate(length));
    if (!buffer) return host.no_memory();
    int result = host.prepare(descriptor);
    if (!result) {
        // readBytes requires a prepared descriptor. Bound individual copies, while
        // preserving the full accepted length and the allocator's own limits.
        decltype(length + 0) offset = 0;
        while (offset < length) {
            auto chunk = length - offset;
            if (chunk > 65536) chunk = 65536;
            if (host.read(descriptor, offset, buffer + offset, chunk) != chunk) {
                result = host.copy_error();
                break;
            }
            offset += chunk;
        }
        int complete_result = host.complete(descriptor);
        if (!result) result = complete_result;
        if (!result) {
            Arguments local = *arguments;
            local.structureInputDescriptor = nullptr;
            local.structureInput = buffer;
            local.structureInputSize = static_cast<unsigned>(length);
            result = host.original(&local);
            // Preserve changes to output counts and any other result fields, but
            // never expose the temporary input pointer to the caller.
            local.structureInputDescriptor = descriptor;
            local.structureInput = arguments->structureInput;
            local.structureInputSize = arguments->structureInputSize;
            *arguments = local;
        }
    }
    host.release(buffer, length);
    return result;
}
#endif
