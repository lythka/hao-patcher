// Per-build x86_64 SkyLight and QuartzCore bindings, keyed by image UUID.
// Each line is printed by `python3 -I src/derive.py --build BUILD --dsc DSC`, which finds
// every value by symbol name and checks it against the instruction that uses it.
//
// UUID(...) lists the LC_UUID bytes.
// SKYLIGHT_PROFILE(build, uuid, api_table, api_count, display_callbacks, software_update_return)
//   offsets from SkyLight's mach header: _quartzcore_vtable (filled by WSInitialize, api_count
//   entries), metal_core_animation_display_vtable, and the return address of
//   layer_tile_traverse's CARenderUpdateAddedAllContexts call.
// QUARTZCORE_PROFILE(build, uuid, inverse_slot, update_flags)
//   CA::WindowServer::Display vtable slot of actual_inverse_color (prepare_one_filter calls it),
//   and the CA::Render::Update byte holding will_clip_backdrops (1) and
//   backdrop_invalidations_finalized (4).
SKYLIGHT_PROFILE("26A428", UUID(0x02,0xa9,0xa2,0x02,0x2f,0x39,0x32,0x04,0x86,0xde,0xa0,0x9e,0xa6,0xae,0x8d,0xee), 0x398e4a40, 14, 0x398d85b0, 0x361271) // 02A9A202-2F39-3204-86DE-A09EA6AE8DEE
QUARTZCORE_PROFILE("26A428", UUID(0x0a,0xd3,0x3c,0xc9,0x75,0xa1,0x35,0x9d,0xbc,0x41,0x4e,0xe2,0x5b,0xe4,0xf5,0x99), 124, 0x543) // 0AD33CC9-75A1-359D-BC41-4EE25BE4F599
SKYLIGHT_PROFILE("26B131", UUID(0x21,0xac,0xab,0xfa,0xb6,0xcc,0x3e,0xc9,0xa3,0xcf,0x6d,0x4d,0x6b,0x63,0x7f,0x5a), 0x3ddcb470, 14, 0x3ddbb848, 0x366e9b) // 21ACABFA-B6CC-3EC9-A3CF-6D4D6B637F5A
QUARTZCORE_PROFILE("26B131", UUID(0x18,0x5c,0xe9,0x5a,0xbf,0x48,0x3f,0x58,0xa0,0x8a,0x8d,0xc4,0xe5,0x25,0x8b,0x94), 126, 0x553) // 185CE95A-BF48-3F58-A08A-8DC4E5258B94
