/* BLAKE2b (RFC 7693), unkeyed, any digest length 1..64. */
#pragma once
#include <stddef.h>
#include <stdint.h>
void blake2b(uint8_t *out, size_t outlen, const void *in, size_t inlen);
