// igil_rewrite.h: argument-buffer rewrite of HD 4000 IGIL text (see igil_rewrite.c)
#pragma once
typedef struct { int tex_slot, tex_count, buf_slot, buf_count, buf_bti; } igil_config;
typedef struct { int textures, buffers, tex_base, buf_base; } igil_stats;
// Returns a malloc'd rewritten program, or NULL when the program needs no change.
char *igil_rewrite(const char *in, const igil_config *cfg, igil_stats *st);
