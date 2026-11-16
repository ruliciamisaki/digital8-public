"use strict";
const { Settings } = require("./settings.js");
const OTHER_PIXELS = -1;

function canSplitLayer(ps, layer) {
    return !!(layer && ps.constants && layer.kind === ps.constants.LayerKind.NORMAL);
}

function rgbKey(r, g, b) { return (r << 16) | (g << 8) | b; }

function insideSelection(selection, x, y) {
    if (!selection) return true;
    const { left, top, right, bottom } = selection.bounds;
    if (x < left || x >= right || y < top || y >= bottom) return false;
    return !selection.bytes || selection.bytes[(y - top) * (right - left) + x - left] !== 0;
}

function collectColours(image, palette, selection = null) {
    const { width, height, components, bytes, left, top } = image;
    const colours = new Map();
    // Store one link per pixel. Writing a layer touches only that colour's pixels.
    const next = new Int32Array(width * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const pixel = y * width + x, at = pixel * components;
            if (components === 4 && bytes[at + 3] === 0) continue;
            const rgb = rgbKey(bytes[at], bytes[at + 1], bytes[at + 2]);
            const key = palette.has(rgb) && insideSelection(selection, left + x, top + y) ? rgb : OTHER_PIXELS;
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

function colourPixels(result, colour) {
    const { image, next } = result;
    const width = colour.maxX - colour.minX + 1, height = colour.maxY - colour.minY + 1;
    const rgba = new Uint8Array(width * height * 4);
    for (let pixel = colour.head; pixel !== -1; pixel = next[pixel]) {
        const y = Math.floor(pixel / image.width), x = pixel - y * image.width;
        const at = pixel * image.components;
        const dst = ((y - colour.minY) * width + x - colour.minX) * 4;
        rgba[dst] = image.bytes[at];
        rgba[dst + 1] = image.bytes[at + 1];
        rgba[dst + 2] = image.bytes[at + 2];
        rgba[dst + 3] = image.components === 4 ? image.bytes[at + 3] : 255;
    }
    return { rgba, width, height, left: image.left + colour.minX, top: image.top + colour.minY };
}

async function captureColourLayers(ps, doc, layer, snapshot) {
    const bounds = layer.boundsNoEffects;
    if (bounds.right <= bounds.left || bounds.bottom <= bounds.top) return { colours: [] };
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
    const result = collectColours(image, palette, selection);
    return { ...result, hasSelection: !!selection };
}

async function layerCommand(ps, descriptor) {
    const [result] = await ps.action.batchPlay([{ ...descriptor, _options: { dialogOptions: "silent" } }], {});
    if (result?._obj === "error") throw new Error(result.message || "色別レイヤーの処理に失敗しました。");
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

async function writeColourLayers(ps, context, doc, source, result) {
    const { ElementPlacement, BlendMode } = ps.constants;
    const { fillOpacity, isClippingMask } = source;
    const group = await doc.createLayerGroup({
        name: "Digital8 色別 — " + source.name,
        blendMode: source.blendMode || BlendMode.NORMAL,
        opacity: source.opacity
    });
    group.move(source, ElementPlacement.PLACEBEFORE);
    if (isClippingMask) group.isClippingMask = true;
    let count = 0;
    for (const colour of result.colours) {
        if (context.isCancelled) throw new Error("色別レイヤーの作成をキャンセルしました。");
        const { rgba, width, height, left, top } = colourPixels(result, colour);
        const name = colour.key >= 0 ? "#" + colour.key.toString(16).padStart(6, "0").toUpperCase() :
            result.hasSelection ? "選択範囲外・境界" : "その他の画素";
        const layer = await doc.createPixelLayer({ name, opacity: fillOpacity });
        layer.move(group, ElementPlacement.PLACEINSIDE);
        const imageData = await ps.imaging.createImageDataFromBuffer(rgba, {
            width, height, components: 4, colorSpace: "RGB", colorProfile: result.image.colorProfile
        });
        try { await ps.imaging.putPixels({ documentID: doc.id, layerID: layer.id, imageData, targetBounds: { left, top } }); }
        finally { imageData.dispose(); }
        if (colour.key >= 0) count++;
    }
    if (context.isCancelled) throw new Error("色別レイヤーの作成をキャンセルしました。");
    await copyPixelMask(ps, doc, source, group);
    await layerCommand(ps, { _obj: "select", _target: [{ _ref: "layer", _id: group.id }], makeVisible: false });
    return count;
}

module.exports = { canSplitLayer, captureColourLayers, writeColourLayers, collectColours, colourPixels };
