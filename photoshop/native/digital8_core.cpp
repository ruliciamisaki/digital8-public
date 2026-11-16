#include "digital8_core.h"

#ifdef _MSC_VER
// MSVC's floating-point marker; no CRT initialization is needed by this DLL.
extern "C" { int _fltused = 0; }
#endif

namespace {
constexpr D8Int max_count = 2147483647;
double abs_value(double x) { return x < 0 ? -x : x; }
double low(double a, double b) { return a < b ? a : b; }
double high(double a, double b) { return a > b ? a : b; }
bool finite_nonnegative(double x) { return x >= 0 && x <= 1.7976931348623157e308; }
bool aligned(const void* p) { return (reinterpret_cast<D8Size>(p) & 7) == 0; }
D8Size round8(D8Size n) { return (n + 7) & ~D8Size(7); }
D8Int root(D8Int* parent, D8Int x) {
    while (parent[x] != x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
    }
    return x;
}
bool valid_rgb(const float* rgb, D8Int count) {
    for (D8Size i = 0; i < D8Size(count) * 3; ++i) {
        const double v = rgb[i];
        if (!(v >= -3.4028234663852886e38 && v <= 3.4028234663852886e38)) return false;
    }
    return true;
}
}

D8Int d8_abi_version() { return 1; }
D8Size d8_union_scratch_bytes(D8Int n) {
    if (n <= 0 || sizeof(D8Size) < 8) return 0;
    return round8(D8Size(n) * 8) + D8Size(n) * 48;
}
D8Size d8_cleanup_scratch_bytes(D8Int n) {
    if (n <= 0 || sizeof(D8Size) < 8) return 0;
    return round8(D8Size(n) * 12) + D8Size(n) * 32;
}

D8Int d8_union_regions(const float* rgb, const D8Int* a, const D8Int* b,
    D8Int edge_count, const D8Byte* valid, D8Int n, double tolerance,
    D8Int min_area, void* scratch, D8Size bytes, D8Int* labels) {
    if (!rgb || !valid || !labels || !scratch || !aligned(scratch) || n <= 0
        || edge_count < 0 || (edge_count && (!a || !b))
        || !finite_nonnegative(tolerance) || min_area < 0) return D8InvalidArgument;
    if (bytes < d8_union_scratch_bytes(n)) return D8InsufficientScratch;
    if (!valid_rgb(rgb, n)) return D8InvalidArgument;
    for (D8Int e = 0; e < edge_count; ++e)
        if (a[e] < 0 || a[e] >= n || b[e] < 0 || b[e] >= n
            || !valid[a[e]] || !valid[b[e]]) return D8InvalidArgument;
    auto parent = static_cast<D8Int*>(scratch);
    auto sizes = parent + n;
    auto lo = reinterpret_cast<double*>(static_cast<D8Byte*>(scratch) + round8(D8Size(n) * 8));
    auto hi = lo + D8Size(n) * 3;
    for (D8Int i = 0; i < n; ++i) {
        parent[i] = i; sizes[i] = 1;
        for (D8Size c = 0; c < 3; ++c) lo[D8Size(i)*3+c] = hi[D8Size(i)*3+c] = rgb[D8Size(i)*3+c];
    }
    for (D8Int pass = 0; pass < (min_area > 1 ? 2 : 1); ++pass) {
        const double limit = pass ? tolerance * 1.5 : tolerance;
        for (D8Int e = 0; e < edge_count; ++e) {
            D8Int first = root(parent, a[e]), second = root(parent, b[e]);
            if (first == second || (pass && sizes[first] >= min_area && sizes[second] >= min_area)) continue;
            bool permitted = true;
            for (D8Size c = 0; c < 3; ++c)
                if (high(hi[D8Size(first)*3+c], hi[D8Size(second)*3+c])
                    - low(lo[D8Size(first)*3+c], lo[D8Size(second)*3+c]) > limit) permitted = false;
            if (!permitted) continue;
            if (sizes[first] < sizes[second]) { const auto t = first; first = second; second = t; }
            parent[second] = first; sizes[first] += sizes[second];
            for (D8Size c = 0; c < 3; ++c) {
                lo[D8Size(first)*3+c] = low(lo[D8Size(first)*3+c], lo[D8Size(second)*3+c]);
                hi[D8Size(first)*3+c] = high(hi[D8Size(first)*3+c], hi[D8Size(second)*3+c]);
            }
        }
    }
    for (D8Int i = 0; i < n; ++i) labels[i] = valid[i] ? root(parent, i) : -1;
    return D8Ok;
}

D8Int d8_clean_colour_cells(const D8U16* indices, const float* rgb, const D8Byte* valid,
    D8Int width, D8Int height, double tolerance, D8Int min_area,
    const D8Byte* compatible, D8Int palette_count,
    void* scratch, D8Size bytes, D8U16* output) {
    if (width <= 0 || height <= 0 || width > max_count / height || !indices || !rgb
        || !valid || !output || !scratch || !aligned(scratch)
        || !finite_nonnegative(tolerance) || min_area < 0
        || palette_count <= 0 || palette_count > 65536) return D8InvalidArgument;
    const D8Int n = width * height;
    if (bytes < d8_cleanup_scratch_bytes(n)) return D8InsufficientScratch;
    if (!valid_rgb(rgb, n)) return D8InvalidArgument;
    for (D8Int i = 0; i < n; ++i) if (indices[i] >= palette_count) return D8InvalidArgument;
    auto parent = static_cast<D8Int*>(scratch);
    auto sizes = parent + n;
    auto targets = sizes + n;
    auto sums = reinterpret_cast<double*>(static_cast<D8Byte*>(scratch) + round8(D8Size(n)*12));
    auto best = sums + D8Size(n)*3;
    for (D8Int i = 0; i < n; ++i) {
        parent[i] = targets[i] = i; sizes[i] = 0; best[i] = 1.7976931348623157e308;
        for (D8Size c = 0; c < 3; ++c) sums[D8Size(i)*3+c] = 0;
    }
    for (D8Int y = 0; y < height; ++y) for (D8Int x = 0; x < width; ++x) {
        const D8Int p = y*width+x;
        if (!valid[p]) continue;
        for (D8Int direction = 0; direction < 2; ++direction) {
            if ((!direction && !x) || (direction && !y)) continue;
            const D8Int other = p - (direction ? width : 1);
            if (!valid[other] || indices[p] != indices[other]) continue;
            const D8Int first = root(parent, p), second = root(parent, other);
            if (first != second) parent[first > second ? first : second] = first < second ? first : second;
        }
    }
    for (D8Int p = 0; p < n; ++p) if (valid[p]) {
        const auto r = root(parent, p); parent[p] = r; ++sizes[r];
        for (D8Size c = 0; c < 3; ++c) sums[D8Size(r)*3+c] += rgb[D8Size(p)*3+c];
    }
    for (D8Int r = 0; r < n; ++r) if (sizes[r])
        for (D8Size c = 0; c < 3; ++c) sums[D8Size(r)*3+c] /= sizes[r];
    for (D8Int y = 0; y < height; ++y) for (D8Int x = 0; x < width; ++x) {
        const D8Int p = y*width+x, first = parent[p];
        if (!valid[p] || sizes[first] >= min_area) continue;
        for (D8Int direction = 0; direction < 4; ++direction) {
            if ((direction == 0 && x == 0) || (direction == 1 && x == width-1)
                || (direction == 2 && y == 0) || (direction == 3 && y == height-1)) continue;
            const D8Int other = p + (direction == 0 ? -1 : direction == 1 ? 1 : direction == 2 ? -width : width);
            if (!valid[other]) continue;
            const D8Int second = parent[other];
            if (compatible && !compatible[D8Size(indices[first])*palette_count+indices[second]]) continue;
            if (second == first || sizes[second] < sizes[first]
                || (sizes[second] == sizes[first] && second > first)) continue;
            bool allowed = true; double score = 0;
            for (D8Size c = 0; c < 3; ++c) {
                const double difference = sums[D8Size(first)*3+c] - sums[D8Size(second)*3+c];
                if (abs_value(difference) > tolerance
                    || abs_value(double(rgb[D8Size(p)*3+c]) - rgb[D8Size(other)*3+c]) > tolerance) allowed = false;
                score += difference*difference;
            }
            if (allowed && (score < best[first] || (score == best[first] && second < targets[first]))) {
                best[first] = score; targets[first] = second;
            }
        }
    }
    for (D8Int r = 0; r < n; ++r) if (sizes[r]) targets[r] = root(targets, r);
    for (D8Int p = 0; p < n; ++p) {
        output[p] = indices[p];
        if (!valid[p]) continue;
        const auto first = parent[p], r = targets[first];
        bool allowed = !compatible || compatible[D8Size(indices[first])*palette_count+indices[r]];
        for (D8Size c = 0; c < 3; ++c)
            if (abs_value(sums[D8Size(first)*3+c] - sums[D8Size(r)*3+c]) > tolerance) allowed = false;
        if (allowed) output[p] = indices[r];
    }
    return D8Ok;
}
