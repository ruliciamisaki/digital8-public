"use strict";
const data = require("./native-data.json");
function bytes(snapshot) {
    if (!/^D8N1:[0-9a-f]+$/.test(snapshot) || snapshot.length !== 5 + data.size * 2)
        throw new Error("Digital8設定データの形式が違います。");
    const result = new Uint8Array(data.size);
    for (let i = 0; i < result.length; i++) result[i] = parseInt(snapshot.substr(5 + i * 2, 2), 16);
    const view = new DataView(result.buffer);
    if (view.getUint32(0, true) !== 0x44384631 || view.getUint32(4, true) !== 1) throw new Error("未対応の設定バージョンです。");
    return result;
}
class Settings {
    constructor(machine = 2, snapshot = data.defaults[machine]) { this.bytes = bytes(snapshot); }
    get(name) {
        const f = data.fields[name]; if (!f) throw new Error("Unknown field: " + name);
        return new DataView(this.bytes.buffer)[f.type === "int" ? "getInt32" : "getFloat32"](f.offset, true);
    }
    set(name, value) {
        const f = data.fields[name]; if (!f) throw new Error("Unknown field: " + name);
        if (!Number.isFinite(value) || (f.type === "int" && !Number.isInteger(value))) throw new Error("有効な数値を入力してください。");
        new DataView(this.bytes.buffer)[f.type === "int" ? "setInt32" : "setFloat32"](f.offset, value, true);
    }
    palette(index) {
        const p = data.palettes[index];
        if (!p || p.machine !== this.get("machine")) throw new Error("機種とパレットが一致しません。");
        this.set("palette_index", index); this.set("colour_count", p.colours.length);
        this.bytes.fill(0, data.paletteOffset);
        p.colours.forEach((c, i) => this.colour(i, c));
    }
    colour(index, rgb) {
        if (!Number.isInteger(index) || index < 0 || index >= this.get("colour_count")) throw new Error("色番号が不正です。");
        const offset = data.paletteOffset + index * 3;
        if (rgb) {
            if (rgb.length !== 3 || rgb.some(c => !Number.isInteger(c) || c < 0 || c > 255)) throw new Error("RGBは0〜255で指定してください。");
            this.bytes.set(rgb, offset);
        }
        return Array.from(this.bytes.slice(offset, offset + 3));
    }
    encode() { return "D8N1:" + Array.from(this.bytes, c => c.toString(16).padStart(2, "0")).join(""); }
}
module.exports = { Settings, data };
