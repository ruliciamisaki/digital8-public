"use strict";
const { canSplitLayer, captureLayerOutput, writeLayerOutput, createFillTransfer } = require("./colour_layers.js");
const FILTER = "b0757cac-5771-4653-9004-388c1832665f";
async function readTarget(ps) {
    const doc = ps.app.activeDocument;
    if (!doc || doc.activeLayers.length !== 1) throw new Error("画像レイヤーかスマートオブジェクトを1つ選択してください。");
    const layer = doc.activeLayers[0], target = { documentID: doc.id, layerID: layer.id, existing: null };
    if (ps.constants && layer.kind === ps.constants.LayerKind.SMARTOBJECT) {
        const result = (await ps.action.batchPlay([{ _obj: "get", _target: [{ _property: "smartObject" }, { _ref: "layer", _id: layer.id }], _options: { dialogOptions: "silent" } }], {}))[0];
        const filters = result.smartObject && result.smartObject.filterFX || [];
        const matches = filters.map((f, index) => ({ fx: f, index: index + 1 })).filter(f => f.fx.filter && f.fx.filter._obj === FILTER);
        if (matches.length > 1) throw new Error("Digital8が複数あります。再編集するDigital8を1つに整理してください。");
        if (matches.length) {
            const match = matches[0];
            if (match.fx.enabled === false) throw new Error("既存のDigital8が無効です。スマートフィルタを有効にしてから再編集してください。");
            target.existing = { index: match.index, snapshot: match.fx.filter.$d8st };
        }
    }
    return target;
}

// One interactive modal scope owns the input document until Apply/Cancel.
// Each preview is rolled back before rendering the next one, so filters never
// accumulate. Photoshop's history suspension rolls back on errors/Escape too.
class PreviewSession {
    constructor(ps, status = () => {}) {
        this.ps = ps; this.status = status; this.running = false;
        this.queue = []; this.wake = null; this.closing = false;
    }
    request(type, snapshot, layers) {
        if (this.closing) return Promise.reject(new Error("プレビューを終了しています。"));
        if (!this.running && type === "cancel") return Promise.resolve();
        const result = new Promise((resolve, reject) => this.queue.push({ type, snapshot, layers, resolve, reject }));
        if (!this.running) {
            this.running = true;
            this.completion = this.ps.core.executeAsModal(context => this.loop(context), {
                commandName: "Digital8 プレビュー", interactive: true, timeOut: 1000
            }).catch(error => { this.failure = error; this.status("error", error); }).finally(() => {
                const error = this.failure || new Error("プレビューを終了しました。");
                this.queue.splice(0).forEach(c => c.reject(error));
                this.running = false; this.closing = false; this.failure = null;
                this.status("idle");
            });
        }
        if (type === "cancel" || type === "commit") this.closing = true;
        if (this.wake) { const wake = this.wake; this.wake = null; wake(); }
        return result;
    }
    update(snapshot) { return this.request("preview", snapshot); }
    inspect(read) { if (!this.running) throw new Error("No active preview"); return this.request("inspect", read); }
    async commit(snapshot, layers = {}) {
        const grouped = await this.request("commit", snapshot, layers);
        await this.completion;
        return grouped;
    }
    async cancel() { await this.request("cancel"); if (this.completion) await this.completion; }
    async loop(context) {
        let suspension = null, rendered = null, active = null, documentID = null, hiddenSource = null, transfer = null;
        const rollback = async (redraw = false) => {
            if (suspension) { await context.hostControl.resumeHistory(suspension, false); suspension = null; }
            rendered = null;
            if (redraw && documentID !== null) await this.ps.core.redrawDocument({ documentID });
        };
        try {
            const target = await readTarget(this.ps);
            documentID = target.documentID;
            context.onCancel = () => { if (this.wake) { const wake = this.wake; this.wake = null; wake(); } };
            for (;;) {
                if (context.isCancelled) throw new Error("プレビューをキャンセルしました。");
                if (!this.queue.length) { await new Promise(resolve => { this.wake = resolve; }); continue; }
                active = this.queue.shift();
                const current = this.ps.app.activeDocument;
                if (current.id !== documentID || current.activeLayers.length !== 1 || current.activeLayers[0].id !== target.layerID) throw new Error("対象の文書やレイヤーが変わりました。プレビューを取り消します。");
                if (active.type === "inspect") { active.resolve(await active.snapshot()); active = null; continue; }
                if (active.type === "cancel") {
                    await rollback(true); active.resolve(); active = null; return;
                }
                // Preview has no layer-output options. Applying to a pixel layer
                // always writes the patterned group, even with no alternatives.
                const split = active.type === "commit" && canSplitLayer(this.ps, current.activeLayers[0]);
                if (split || active.snapshot !== rendered) {
                    this.status("rendering"); await rollback();
                    suspension = await context.hostControl.suspendHistory({ documentID, name: "Digital8" });
                    const filter = { _obj: FILTER, "$d8st": active.snapshot };
                    if (split) {
                        transfer = await createFillTransfer();
                        filter.$d8fm = transfer.path;
                    }
                    const command = target.existing ? { _obj: "set", _target: [{ _ref: "filterFX", _index: target.existing.index }, { _ref: "layer", _id: target.layerID }], filterFX: { _obj: "filterFX", filter } } : filter;
                    command._options = { dialogOptions: "silent" };
                    const result = await this.ps.action.batchPlay([command], {});
                    if (result[0] && result[0]._obj === "error") throw new Error(result[0].message || result[0].$Msge || "C++フィルタの実行に失敗しました: " + JSON.stringify(result[0]));
                    if (context.isCancelled) throw new Error("プレビューをキャンセルしました。");
                    if (!split) await this.ps.core.redrawDocument({ documentID });
                    rendered = active.snapshot;
                }
                if (split) {
                    this.status("splitting");
                    const fillMap = await transfer.read();
                    await transfer.dispose();
                    transfer = null;
                    const output = await captureLayerOutput(this.ps, current, current.activeLayers[0], active.snapshot);
                    if (context.isCancelled) throw new Error("追加レイヤーの作成をキャンセルしました。");
                    await rollback();
                    suspension = await context.hostControl.suspendHistory({ documentID, name: "Digital8 追加レイヤー" });
                    const source = current.activeLayers[0];
                    await writeLayerOutput(this.ps, context, current, source, output, fillMap, active.layers);
                    if (context.isCancelled) throw new Error("追加レイヤーの作成をキャンセルしました。");
                    // Hide last. Photoshop can exclude visibility from its history.
                    if (source.visible) { source.visible = false; hiddenSource = source; }
                    await context.hostControl.resumeHistory(suspension, true); suspension = null;
                    active.resolve(true); active = null; return;
                }
                if (active.type === "commit") {
                    await context.hostControl.resumeHistory(suspension, true); suspension = null;
                    active.resolve(false); active = null; return;
                }
                this.status("preview"); active.resolve(); active = null;
            }
        } catch (error) {
            // If Photoshop has cancelled the context, throwing lets the host
            // automatically roll back; no forbidden API calls after cancellation.
            if (!context.isCancelled) {
                try {
                    if (hiddenSource) hiddenSource.visible = true;
                    await rollback(true);
                } catch (rollbackError) { error = new Error(error.message + " / 復帰エラー: " + rollbackError.message); }
            }
            if (active) active.reject(error);
            throw error;
        } finally {
            if (transfer) {
                try { await transfer.dispose(); } catch (_) { /* the UXP temporary folder is cleared on unload */ }
            }
        }
    }
}
module.exports = { PreviewSession, FILTER, readTarget };
