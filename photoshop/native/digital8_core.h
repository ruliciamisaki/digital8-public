#pragma once

// Deliberately independent of Photoshop, Python, the CRT and platform headers.
// The host owns every allocation. All arrays must be contiguous, nonoverlapping
// and have the documented length. Scratch must be aligned to 8 bytes.
using D8Int = signed int;
using D8U16 = unsigned short;
using D8Byte = unsigned char;
using D8Size = decltype(sizeof(0));
static_assert(sizeof(D8Int) == 4 && sizeof(D8U16) == 2 && sizeof(float) == 4);

#ifdef _WIN32
#define D8_API extern "C" __declspec(dllexport)
#else
#define D8_API extern "C" __attribute__((visibility("default")))
#endif

enum D8Status { D8Ok = 0, D8InvalidArgument = 1, D8InsufficientScratch = 2 };

D8_API D8Int d8_abi_version();
// Returns zero for unsupported counts. Count must be in [1, 2147483647].
D8_API D8Size d8_union_scratch_bytes(D8Int count);
D8_API D8Size d8_cleanup_scratch_bytes(D8Int count);

// rgb: count*3 float32; valid: count uint8 (0/1); labels: count int32.
// edges_a/b: edge_count int32, sorted stably by RGB distance by the caller.
// Only edges whose two pixels are valid are accepted. Output is not touched
// on validation errors. tolerance is finite and nonnegative; min_area >= 0.
D8_API D8Int d8_union_regions(
    const float* rgb, const D8Int* edges_a, const D8Int* edges_b,
    D8Int edge_count, const D8Byte* valid, D8Int count, double tolerance,
    D8Int min_area, void* scratch, D8Size scratch_bytes, D8Int* labels);

// indices/output: width*height uint16; RGB: width*height*3 float32.
// compatible: optional palette_count squared uint8 row-major matrix.
// Invisible pixels retain their input index. palette_count in [1, 65536].
D8_API D8Int d8_clean_colour_cells(
    const D8U16* indices, const float* rgb, const D8Byte* valid,
    D8Int width, D8Int height, double tolerance, D8Int min_area,
    const D8Byte* compatible, D8Int palette_count,
    void* scratch, D8Size scratch_bytes, D8U16* output);
