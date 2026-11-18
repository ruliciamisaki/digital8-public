#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace digital8 {
constexpr uint32_t settings_magic = 0x44384631;
constexpr int max_palette = 600;
struct RGB { uint8_t r, g, b; };
struct Palette { int machine; const char* id; const char* label; std::vector<RGB> colours; };
// Fixed-layout, versioned snapshot. No pointers or paths are persisted in PSDs.
struct Settings {
    uint32_t magic = settings_magic, version = 1;
    int32_t machine = 2, palette_index = 0, colour_count = 0;
    int32_t pixel_size = 1, tall_dots = 0, threshold = 58, radius = 3, min_area = 12;
    int32_t max_colours = 24, groups = 48, alpha_mode = 0, alpha_threshold = 128;
    int32_t half = 0, quarter = 0, eighth = 0, three112 = 0, three111 = 0;
    int32_t trace = 0, white_transparent = 0;
    float tolerance = 128, edge = 1, growth = 0, trace_strength = .55f;
    float brightness = 1, saturation = 1, contrast = 1, red = 1, green = 1, blue = 1;
    RGB colours[max_palette]{};
};
static_assert(sizeof(Settings)==1924,"Bump the snapshot version when changing the settings layout");
struct Image { int width = 0, height = 0; std::vector<uint8_t> rgba; };
struct Hooks { std::function<bool()> cancelled; std::function<void(int)> progress; };
constexpr uint16_t line_fill_id = 0xffff;
struct FillStyle { RGB colour; uint8_t mixed; };
struct FillMap {
    std::vector<FillStyle> styles;
    std::vector<uint16_t> ids;
};
const std::vector<Palette>& palettes();
bool observed_pc88(int a,int b);
Settings defaults(int machine = 2);
void set_palette(Settings&, int index);
void validate(const Settings&);
Image render(const Image&, const Settings&, const Hooks& = {}, FillMap* = nullptr);
std::string encode(const Settings&);
Settings decode(const std::string&);
}
