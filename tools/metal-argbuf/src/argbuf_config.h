// argbuf_config.h: the slot contract shared by the iCBE wrapper (shader side) and libargbuf (device side).
// Texture handle h      -> texture slot AGB_TEX_SLOT + h (declared t-range in every rewritten shader).
// Buffer pointer        -> ((AGB_BUF_BTI + h) << 32) | byte offset; buffer bound at slot AGB_BUF_SLOT + h, offset 0.
// Sampler handle h      -> sampler slot AGB_SAMP_SLOT + h (one handle per distinct sampler state object).
#pragma once
// Textures and buffers sit above Metal's per-stage API range (0..30); textures bind up to slot 127 on this driver.
// AGB_BUF_BTI must exceed constant buffers + the shader's own textures + AGB_TEX_COUNT + its own UAVs (the binding
// table is ordered CBs, textures, UAVs), and stay below Gen7's 253 usable entries.
#define AGB_TEX_SLOT   32
#define AGB_TEX_COUNT  96
#define AGB_BUF_SLOT   40
#define AGB_BUF_COUNT  24
#define AGB_BUF_BTI    160
#define AGB_SAMP_SLOT  10   // Gen7 and Metal both have 16 sampler slots per stage: the top 6 are shared with clients
#define AGB_SAMP_COUNT 6
