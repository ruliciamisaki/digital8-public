"use strict";
const { Settings } = require("./settings.js");
const OTHER_PIXELS = -1;
const LINE_FILL = 0xffff;

function canSplitLayer(ps, layer) {
    return !!(layer && ps.constants && layer.kind === ps.constants.LayerKind.NORMAL);
}

function rgbKey(r, g, b) { return (r << 16) | (g << 8) | b; }

function selectionCoverage(selection, x, y) {
    if (!selection) return 255;
    const { left, top, right, bottom } = selection.bounds;
    if (x < left || x >= right || y < top || y >= bottom) return 0;
    return selection.bytes ? selection.bytes[(y - top) * (right - left) + x - left] : 255;
}

function collectBuckets(image, classify) {
    const { width, height, components, bytes, left, top } = image;
    const colours = new Map();
    // Store one link per pixel. Writing a layer touches only that colour's pixels.
    const next = new Int32Array(width * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const pixel = y * width + x, at = pixel * components;
            if (components === 4 && bytes[at + 3] === 0) continue;
            const rgb = rgbKey(bytes[at], bytes[at + 1], bytes[at + 2]);
            const key = classify(rgb, left + x, top + y);
            let colour = colours.get(key);
            if (!colour) {
                colour = { key, minX: x, maxX: x, minY: y, maxY: y, head: -1 };
                colours.set(key, colour);
            }
            colour.minX = Math.min(colour.minX, x);
            colour.maxX = Math.max(colour.maxX, x);
            colour.maxY = y;
            next[pixel] = colour.head;
            colour.head = pixel;
        }
    }
    return { image, next, colours: Array.from(colours.values()).sort((a, b) => a.key - b.key) };
}

function collectColours(image, palette, selection = null) {
    return collectBuckets(image, (rgb, x, y) =>
        palette.has(rgb) && selectionCoverage(selection, x, y) ? rgb : OTHER_PIXELS);
}

function decodeFillMap(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < 24 || view.getUint32(0, true) !== 0x31463844)
        throw new Error("塗りの情報を受け取れませんでした。C++フィルターも最新版に差し替えてください。");
    const left = view.getInt32(4, true), top = view.getInt32(8, true);
    const width = view.getUint32(12, true), height = view.getUint32(16, true);
    const count = view.getUint32(20, true), offset = 24 + count * 4;
    if (!width || !height || width * height > 8000000 || !count || count >= LINE_FILL ||
        offset + width * height * 2 !== view.byteLength) throw new Error("塗りの情報が壊れています。");
    const styles = Array.from({length: count}, (_, i) => {
        const at = 24 + 4 * i;
        return { rgb: [view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2)],
            mixed: view.getUint8(at + 3) !== 0 };
    });
    return { left, top, width, height, styles, ids: new Uint16Array(buffer, offset) };
}

async function createFillTransfer() {
    const { localFileSystem, formats } = require("uxp").storage;
    const folder = await localFileSystem.getTemporaryFolder();
    const nonce = Date.now().toString(16) + Math.random().toString(16).slice(2);
    const file = await folder.createFile("digital8-fill-" + nonce + ".tmp", { overwrite: false });
    try {
        await file.write("D8RQ:" + nonce);
        return {
            path: encodeURIComponent(localFileSystem.getNativePath(file)),
            async read() { return decodeFillMap(await file.read({ format: formats.binary })); },
            async dispose() { await file.delete(); },
        };
    } catch (error) {
        await file.delete();
        throw error;
    }
}

function collectFills(image, map, selection, flatten) {
    const result = collectBuckets(image, (_rgb, x, y) => {
        const dx = x - map.left, dy = y - map.top;
        // Keep outside and partially selected pixels at their actual Photoshop RGBA.
        if (dx < 0 || dy < 0 || dx >= map.width || dy >= map.height ||
            selectionCoverage(selection, x, y) !== 255) return OTHER_PIXELS;
        const id = map.ids[dy * map.width + dx];
        if (id !== LINE_FILL && id >= map.styles.length) throw new Error("塗りの情報が壊れています。");
        return id;
    });
    for (const bucket of result.colours) {
        if (bucket.key === LINE_FILL) bucket.name = "主線";
        else if (bucket.key !== OTHER_PIXELS) {
            const style = map.styles[bucket.key];
            bucket.name = (style.mixed ? "タイル " : "単色 ") + hex(rgbKey(...style.rgb));
            if (flatten && style.mixed) bucket.rgb = style.rgb;
        }
    }
    return result;
}

function hex(rgb) { return "#" + rgb.toString(16).padStart(6, "0").toUpperCase(); }

function colourPixels(result, colour) {
    const { image, next } = result;
    const width = colour.maxX - colour.minX + 1, height = colour.maxY - colour.minY + 1;
    const rgba = new Uint8Array(width * height * 4);
    for (let pixel = colour.head; pixel !== -1; pixel = next[pixel]) {
        const y = Math.floor(pixel / image.width), x = pixel - y * image.width;
        const at = pixel * image.components;
        const dst = ((y - colour.minY) * width + x - colour.minX) * 4;
        rgba[dst] = colour.rgb ? colour.rgb[0] : image.bytes[at];
        rgba[dst + 1] = colour.rgb ? colour.rgb[1] : image.bytes[at + 1];
        rgba[dst + 2] = colour.rgb ? colour.rgb[2] : image.bytes[at + 2];
        rgba[dst + 3] = image.components === 4 ? image.bytes[at + 3] : 255;
    }
    return { rgba, width, height, left: image.left + colour.minX, top: image.top + colour.minY };
}

async function captureLayerOutput(ps, doc, layer, snapshot) {
    const bounds = layer.boundsNoEffects;
    if (bounds.right <= bounds.left || bounds.bottom <= bounds.top) return { image: null };
    const s = new Settings(2, snapshot);
    const palette = new Set();
    for (let i = 0; i < s.get("colour_count"); i++) palette.add(rgbKey(...s.colour(i)));
    const { imageData, sourceBounds } = await ps.imaging.getPixels({
        documentID: doc.id, layerID: layer.id, componentSize: 8, colorSpace: "RGB", applyAlpha: false
    });
    let image;
    try {
        const { width, height, components, colorProfile } = imageData;
        if (components !== 3 && components !== 4) throw new Error("色別出力にはRGB 8-bitの画像レイヤーが必要です。");
        image = { width, height, components, colorProfile,
            left: sourceBounds.left, top: sourceBounds.top, bytes: await imageData.getData({ chunky: true }) };
    } finally { imageData.dispose(); }
    let selection = null;
    const selectionBounds = doc.selection.bounds;
    if (selectionBounds) {
        selection = { bounds: selectionBounds };
        if (!doc.selection.solid) {
            const mask = await ps.imaging.getSelection({ documentID: doc.id, sourceBounds: selectionBounds });
            try { selection = { bounds: mask.sourceBounds, bytes: await mask.imageData.getData() }; }
            finally { mask.imageData.dispose(); }
        }
    }
    return { image, palette, selection };
}

async function layerCommand(ps, descriptor) {
    const [result] = await ps.action.batchPlay([{ ...descriptor, _options: { dialogOptions: "silent" } }], {});
    if (result?._obj === "error") throw new Error(result.message || "追加レイヤーの処理に失敗しました。");
    return result;
}

async function copyPixelMask(ps, doc, source, group) {
    const descriptor = await layerCommand(ps, { _obj: "get", _target: [{ _ref: "layer", _id: source.id }] });
    if (!descriptor.hasUserMask || descriptor.userMaskEnabled === false) return;
    const { imageData, sourceBounds } = await ps.imaging.getLayerMask({ documentID: doc.id, layerID: source.id });
    try {
        const { left, top } = sourceBounds;
        await ps.imaging.putLayerMask({ documentID: doc.id, layerID: group.id, imageData, targetBounds: { left, top } });
    } finally { imageData.dispose(); }
    if (source.layerMaskDensity !== 100) group.layerMaskDensity = source.layerMaskDensity;
    if (source.layerMaskFeather !== 0) group.layerMaskFeather = source.layerMaskFeather;
}

async function writeLayerOutput(ps, context, doc, source, output, fillMap, options) {
    const { ElementPlacement, BlendMode } = ps.constants;
    const { fillOpacity, isClippingMask } = source;
    const group = await doc.createLayerGroup({
        name: "Digital8 — " + source.name,
        blendMode: source.blendMode || BlendMode.NORMAL,
        opacity: source.opacity
    });
    group.move(source, ElementPlacement.PLACEBEFORE);
    if (isClippingMask) group.isClippingMask = true;
    const modes = [
        ...(options.fill ? [["塗りごと", "fill"]] : []),
        ...(options.colour ? [["使用色ごと", "colour"]] : []),
        ["タイル模様を保持", "pattern"],
    ];
    const { image, palette, selection } = output;
    for (const [name, mode] of modes) {
        const subgroup = await doc.createLayerGroup({ name });
        subgroup.move(group, ElementPlacement.PLACEINSIDE);
        subgroup.visible = mode === "pattern";
        if (!image) continue;
        const result = mode === "colour" ? collectColours(image, palette, selection) :
            collectFills(image, fillMap, selection, mode === "fill");
        for (const bucket of result.colours) {
            if (context.isCancelled) throw new Error("追加レイヤーの作成をキャンセルしました。");
            const { rgba, width, height, left, top } = colourPixels(result, bucket);
            const name = bucket.name || (bucket.key >= 0 ? hex(bucket.key) :
                selection ? "選択範囲外・境界" : "その他の画素");
            const layer = await doc.createPixelLayer({ name, opacity: fillOpacity });
            layer.move(subgroup, ElementPlacement.PLACEINSIDE);
            const imageData = await ps.imaging.createImageDataFromBuffer(rgba, {
                width, height, components: 4, colorSpace: "RGB", colorProfile: image.colorProfile
            });
            try { await ps.imaging.putPixels({ documentID: doc.id, layerID: layer.id, imageData, targetBounds: { left, top } }); }
            finally { imageData.dispose(); }
        }
    }
    if (context.isCancelled) throw new Error("追加レイヤーの作成をキャンセルしました。");
    await copyPixelMask(ps, doc, source, group);
    await layerCommand(ps, { _obj: "select", _target: [{ _ref: "layer", _id: group.id }], makeVisible: false });
}

module.exports = { canSplitLayer, captureLayerOutput, writeLayerOutput, createFillTransfer,
    collectColours, colourPixels, collectFills, decodeFillMap };
