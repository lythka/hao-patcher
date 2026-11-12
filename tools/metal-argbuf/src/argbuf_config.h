// argbuf_config.h: the slot contract shared by the iCBE wrapper (shader side) and libargbuf (device side).
// Texture handle h      -> texture slot AGB_TEX_SLOT + h (declared t-range in every rewritten shader).
// Buffer pointer        -> ((AGB_BUF_BTI + h) << 32) | byte offset; buffer bound at slot AGB_BUF_SLOT + h, offset 0.
// Sampler handle h      -> sampler slot AGB_SAMP_SLOT + h (one handle per distinct sampler state object).
#pragma once
#define AGB_TEX_SLOT   16
#define AGB_TEX_COUNT  8
#define AGB_BUF_SLOT   40
#define AGB_BUF_COUNT  8
#define AGB_BUF_BTI    40
#define AGB_SAMP_SLOT  10   // Gen7 and Metal both have 16 sampler slots per stage: the top 6 are shared with clients
#define AGB_SAMP_COUNT 6
