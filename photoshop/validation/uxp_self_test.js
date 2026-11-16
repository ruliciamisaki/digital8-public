"use strict";
// Included only in the local validation package, never in the user package.
const { PreviewSession } = require("./session.js");
const { Settings } = require("./settings.js");
async function run(ps) {
    const fs = require("uxp").storage.localFileSystem;
    const root = await fs.getPluginFolder(), folder = await fs.getDataFolder();
    const result = { started: new Date().toISOString(), stages: [] };
    const write = async () => { const file = await folder.createFile("self-test.json", { overwrite: true }); await file.write(JSON.stringify(result, null, 2)); };
    const record = (stage, details = {}) => { result.stages.push({ stage, ...details }); };
    const requireTrue = (ok, message) => { if (!ok) throw new Error(message); };
    const initial = Array.from(ps.app.documents).map(d => d.id);
    const previous = ps.app.documents.length ? ps.app.activeDocument.id : null;
    let doc = null, session = null;
    async function pixels(layerID) {
      const capture = async () => {
        const image = await ps.imaging.getPixels({ documentID: doc.id, ...(layerID === undefined ? {} : { layerID }), componentSize: 8, colorSpace: "RGB", applyAlpha: false,
            sourceBounds: { left: 0, top: 0, right: doc.width, bottom: doc.height } });
        try { return { bytes: new Uint8Array(await image.imageData.getData()), width: image.imageData.width, height: image.imageData.height, components: image.imageData.components, bounds: image.sourceBounds }; }
        finally { image.imageData.dispose(); }
      };
      return session && session.running ? session.inspect(capture) : ps.core.executeAsModal(capture, { commandName: "Digital8 画素検証" });
    }
    function same(a, b) { return a.bytes.length === b.bytes.length && a.bytes.every((n, i) => n === b.bytes[i]); }
    function sameVisible(a, b) {
        return a.width === b.width && a.height === b.height && a.components === b.components &&
            a.bounds.left === b.bounds.left && a.bounds.top === b.bounds.top &&
            a.bytes.every((value, i) => value === b.bytes[i] ||
                (a.components === 4 && i % 4 !== 3 && !a.bytes[i - i % 4 + 3] && !b.bytes[i - i % 4 + 3]));
    }
    async function checkSplit(settings, hasSelection, hasMask = false) {
        const source = doc.activeLayers[0], original = await pixels(source.id);
        const originalHistory = doc.activeHistoryState.id;
        const selection = JSON.stringify(doc.selection.bounds);
        session = new PreviewSession(ps);
        await session.update(settings.encode());
        const preview = await pixels();
        if (hasSelection) await session.cancel(); // Exercise Apply without a running preview as well.
        const count = await session.commit(settings.encode(), true);
        const group = doc.activeLayers[0], layers = Array.from(group.layers);
        requireTrue(group.kind === ps.constants.LayerKind.GROUP, "Expected a group");
        requireTrue(doc.layers[1].id === source.id && !source.visible, "Source must be preserved and hidden below the group");
        requireTrue(same(original, await pixels(source.id)), "Original layer pixels changed");
        requireTrue(count > 1 && layers.length === count + Number(hasSelection), "Unexpected number of colour layers: " + layers.map(l => l.name).join(", "));
        for (const layer of layers.filter(l => l.name.startsWith("#"))) {
            const { bytes, components } = await pixels(layer.id);
            const rgb = [1, 3, 5].map(i => parseInt(layer.name.slice(i, i + 2), 16));
            let visible = false;
            for (let i = 0; i < bytes.length; i += components) {
                if (components === 4 && !bytes[i + 3]) continue;
                visible = true;
                requireTrue(rgb.every((v, channel) => v === bytes[i + channel]), "Layer " + layer.name + " has different RGB");
            }
            requireTrue(visible, "Empty layer " + layer.name);
        }
        requireTrue(sameVisible(preview, await pixels()), "Group composite differs from preview");
        requireTrue(JSON.stringify(doc.selection.bounds) === selection, "Selection was modified");
        record("colour-layers-composite", { count, hasSelection, hasMask, original_intact: true, preview_matches: true });
        await write();
        await ps.core.executeAsModal(async () => {
            await ps.action.batchPlay([{ _obj: "select", _target: [{ _ref: "historyState", _enum: "ordinal", _value: "previous" }] }], {});
        }, { commandName: "Digital8 色別レイヤーを戻す" });
        const restored = doc.activeHistoryState.id === originalHistory && doc.layers.length === 1 &&
            doc.layers[0].id === source.id && sameVisible(original, await pixels(source.id));
        record("colour-layers-undo", { restored, visible: source.visible });
        requireTrue(restored, "Undo did not restore source");
        // The test machine's History Options may exclude layer visibility.
        if (!source.visible) await ps.core.executeAsModal(() => { source.visible = true; }, { commandName: "Digital8 試験画像を表示" });
        await write();
    }
    try {
        await write();
        await ps.core.executeAsModal(async () => { doc = await ps.app.open(await root.getEntry("fixture.png")); }, { commandName: "Digital8 テスト画像" });
        const baseline = await pixels(); record("input", { width: baseline.width, height: baseline.height, components: baseline.components });
        await write();
        const s = new Settings(2); session = new PreviewSession(ps);
        await session.update(s.encode()); const first = await pixels(); requireTrue(!same(baseline, first), "Preview did not change pixels"); record("preview", { changed: true });
        s.set("brightness", .7); await session.update(s.encode()); const second = await pixels(); requireTrue(!same(first, second), "Changed settings did not change preview"); record("update", { changed: true });
        await session.cancel(); requireTrue(same(baseline, await pixels()), "Cancel did not restore exact pixels"); record("cancel", { exact: true }); await write();
        await checkSplit(s, false);
        await ps.core.executeAsModal(async () => {
            await ps.action.batchPlay([{ _obj: "set", _target: [{ _ref: "channel", _property: "selection" }], to: { _obj: "rectangle",
                top: { _unit: "pixelsUnit", _value: 60 }, left: { _unit: "pixelsUnit", _value: 50 }, bottom: { _unit: "pixelsUnit", _value: 120 }, right: { _unit: "pixelsUnit", _value: 110 } } }], {});
        }, { commandName: "Digital8 選択範囲テスト" });
        session = new PreviewSession(ps); await session.update(s.encode()); const selected = await pixels(); let insideChanged = false;
        requireTrue(selected.components === baseline.components && selected.width === baseline.width, "Pixel buffer shape changed");
        for (let y = 0; y < baseline.height; y++) for (let x = 0; x < baseline.width; x++) for (let c = 0; c < baseline.components; c++) {
            const i = (y * baseline.width + x) * baseline.components + c, inside = x >= 50 && x < 110 && y >= 60 && y < 120;
            if (inside) insideChanged = insideChanged || baseline.bytes[i] !== selected.bytes[i];
            else requireTrue(baseline.bytes[i] === selected.bytes[i], "Pixels outside selection changed at " + x + "," + y);
        }
        requireTrue(insideChanged, "Selection preview did not change"); await session.cancel(); requireTrue(same(baseline, await pixels()), "Selection Cancel failed"); record("selection", { outside_exact: true, inside_changed: true, cancel_exact: true }); await write();
        await checkSplit(s, true);
        const beforeMask = doc.activeHistoryState;
        await ps.core.executeAsModal(async () => {
            await doc.selection.deselect();
            const mask = new Uint8Array(doc.width * doc.height);
            for (let i = 0; i < mask.length; i++) mask[i] = i % doc.width < doc.width / 3 ? 0 : 255;
            const imageData = await ps.imaging.createImageDataFromBuffer(mask, {
                width: doc.width, height: doc.height, components: 1, colorSpace: "Grayscale", colorProfile: "Gray Gamma 2.2"
            });
            try { await ps.imaging.putLayerMask({ documentID: doc.id, layerID: doc.activeLayers[0].id, imageData }); }
            finally { imageData.dispose(); }
            await ps.action.batchPlay([{ _obj: "select", _target: [{ _ref: "channel", _enum: "channel", _value: "RGB" }] }], {});
        }, { commandName: "Digital8 レイヤーマスク試験" });
        const maskedSettings = new Settings(2, s.encode()); maskedSettings.set("max_colours", 4);
        await checkSplit(maskedSettings, false, true);
        await ps.core.executeAsModal(() => { doc.activeHistoryState = beforeMask; }, { commandName: "Digital8 マスク試験前へ復帰" });
        await ps.core.executeAsModal(async () => {
            await ps.action.batchPlay([{ _obj: "set", _target: [{ _ref: "channel", _property: "selection" }], to: { _enum: "ordinal", _value: "none" } }, { _obj: "newPlacedLayer" }], {});
        }, { commandName: "Digital8 スマートフィルタテスト" });
        const smartBaseline = await pixels(), historyCount = doc.historyStates.length;
        session = new PreviewSession(ps);
        try { await session.commit(s.encode(), true); throw new Error("Split unexpectedly accepted smart object"); }
        catch (error) { requireTrue(error.message.includes("画像レイヤー"), error.message); await session.completion; }
        record("history-before", { states: Array.from(doc.historyStates).map(h => ({ id: h.id, name: h.name })) });
        session = new PreviewSession(ps); await session.update(s.encode()); await session.commit(s.encode());
        requireTrue(!same(smartBaseline, await pixels()), "Smart filter did not change pixels");
        requireTrue(doc.historyStates.length === historyCount + 1, "Apply created more than one history entry");
        const descriptor = (await ps.action.batchPlay([{ _obj: "get", _target: [{ _ref: "layer", _id: doc.activeLayers[0].id }] }], {}))[0];
        requireTrue(JSON.stringify(descriptor).includes(s.encode()), "Smart filter settings were not saved");
        record("smart-commit", { single_history: true, snapshot: true });
        const committed = await pixels();
        s.set("brightness", 1.3); session = new PreviewSession(ps); await session.update(s.encode());
        requireTrue(!same(committed, await pixels()), "Existing filter preview did not change pixels");
        await session.cancel(); requireTrue(same(committed, await pixels()), "Existing filter Cancel did not restore pixels");
        session = new PreviewSession(ps); await session.commit(s.encode());
        const replaced = (await ps.action.batchPlay([{ _obj: "get", _target: [{ _ref: "layer", _id: doc.activeLayers[0].id }] }], {}))[0];
        requireTrue(replaced.smartObject.filterFX.length===1, "Existing filter was duplicated");
        requireTrue(JSON.stringify(replaced.smartObject.filterFX[0]).includes(s.encode()), "Existing filter settings were not replaced");
        record("existing-filter", { replaced: true, cancel_exact: true, no_duplicate: true });
        await ps.core.executeAsModal(async () => { await ps.action.batchPlay([{ _obj: "select", _target: [{ _ref: "historyState", _enum: "ordinal", _value: "previous" }] }], {}); }, { commandName: "Digital8 履歴復帰テスト" });
        requireTrue(same(committed, await pixels()), "Undo existing-filter edit did not restore prior result");
        await ps.core.executeAsModal(async () => { await ps.action.batchPlay([{ _obj: "select", _target: [{ _ref: "historyState", _enum: "ordinal", _value: "previous" }] }], {}); }, { commandName: "Digital8 適用前へ復帰" });
        const undone = await pixels(); let differences = 0, visibleDifferences = 0, alphaDifferences = 0;
        for(let i=0;i<undone.bytes.length;i++)if(undone.bytes[i]!==smartBaseline.bytes[i]){differences++;if(i%4===3)alphaDifferences++;else if(smartBaseline.bytes[Math.floor(i/4)*4+3])visibleDifferences++;}
        record("undo-diagnostics", { differences, visibleDifferences, alphaDifferences, states: Array.from(doc.historyStates).map(h => ({ id: h.id, name: h.name })) });
        requireTrue(visibleDifferences===0&&alphaDifferences===0, "Undo did not restore smart object"); record("undo", { visible_rgb_exact: true, alpha_exact: true });
        result.ok = true;
    } catch (error) { result.ok = false; result.error = error.message || String(error); result.stack = error.stack; }
    finally {
        try { if (session && session.running) await session.cancel(); } catch (error) { result.cancel_error = error.message; }
        try { if (doc) await ps.core.executeAsModal(async () => { await doc.closeWithoutSaving(); }, { commandName: "Digital8 テスト終了" }); } catch (error) { result.close_error = error.message; }
        try { if (previous !== null) await ps.core.executeAsModal(async () => { await ps.action.batchPlay([{ _obj: "select", _target: [{ _ref: "document", _id: previous }] }], {}); }, { commandName: "元の文書へ戻る" }); } catch (error) { result.restore_error = error.message; }
        result.remaining = Array.from(ps.app.documents).map(d => d.id); result.original_documents_unchanged = JSON.stringify(initial) === JSON.stringify(result.remaining);
        await write();
        console.log("Digital8 verification: " + JSON.stringify(result));
    }
}
module.exports = { run };
