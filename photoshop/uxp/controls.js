"use strict";
// Bounds mirror the C++ validator; the binary offsets/defaults are exported by C++.
const pages = [
    { title: "共通", fields: [
        ["pixel_size", "仮想ピクセル", 1, 64, 1], ["threshold", "主線しきい値", 0, 120, 1],
        ["edge", "輪郭抽出", 0, 1, .01], ["growth", "線の太さ", 0, 16, .25],
        ["trace_strength", "色トレスの明るさ", .1, .95, .01], ["alpha_threshold", "透明度のしきい値", 0, 255, 1]
    ], checks: [["tall_dots", "縦長ドット"], ["trace", "色トレス"], ["alpha_mode", "透明度を二値化"], ["white_transparent", "白を透明にする"]] },
    { title: "面の塗り分け", fields: [
        ["tolerance", "面をまとめる色差", 0, 128, 1], ["radius", "周囲をならす半径", 0, 8, 1],
        ["min_area", "小さい面の整理", 0, 4096, 1], ["max_colours", "アニメ塗りの色数", 0, 256, 1], ["groups", "88/98の共通色区分数", 2, 64, 1]
    ] },
    { title: "タイル", choices: [
        ["half", "1:1", ["市松", "縦縞"]], ["quarter", "3:1", ["交互", "片側"]], ["eighth", "7:1", ["従来 4×2", "交互 4×4"]],
        ["three112", "3色 1:1:2", ["なし", "縞 4×1", "交互 4×2", "斜め 4×4"]], ["three111", "3色 1:1:1", ["なし", "縞 3×1", "斜め 3×3"]],
        ["four_colour", "4色 4×2", ["使わない", "使う"]], ["five_colour", "5色 4×4", ["使わない", "使う"]]
    ] },
    { title: "色補正", fields: [
        ["brightness", "明るさ", 0, 10, .01], ["saturation", "彩度", 0, 10, .01], ["contrast", "コントラスト", 0, 10, .01],
        ["red", "赤", 0, 10, .01], ["green", "緑", 0, 10, .01], ["blue", "青", 0, 10, .01]
    ] }
];
function normalize(spec, value) {
    const [, label, min, max, step] = spec;
    value = Number(value);
    if (!Number.isFinite(value) || value < min || value > max) throw new Error(label + "は" + min + "〜" + max + "で指定してください。");
    // Integers stay integers; fractional controls use their displayed precision.
    if (step === 1 && !Number.isInteger(value)) throw new Error(label + "は整数で指定してください。");
    return Number(value.toFixed(4));
}
module.exports = { pages, normalize };
