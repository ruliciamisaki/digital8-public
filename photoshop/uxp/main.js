"use strict";
const ps = require("photoshop");
const { entrypoints } = require("uxp");
const { Settings, data } = require("./settings.js");
const { pages, normalize } = require("./controls.js");
const { PreviewSession, readTarget } = require("./session.js");
const { canSplitLayer } = require("./colour_layers.js");
const $ = id => document.getElementById(id);
let settings = new Settings(), page = 0, colour = 0, busy = false, pendingCancel = false;
let autoTimer = null, autoTarget = null, panelVisible = true, targetTimer = null, lastTarget = null;
const autoPreview = $("auto-preview");
const splitOutputs = { colour: $("split-output"), fill: $("fill-output") };
const splitSelected = { colour: false, fill: false };
const rgbControls = [];
const pickerListeners = new WeakMap();
$("version").textContent = "C++ Native · " + require("./manifest.json").version;
const savedMachines = {};
try { const saved = localStorage.getItem("digital8-settings-v1"); if (saved) settings = new Settings(2, saved); } catch (_) { /* invalid old data uses defaults */ }
autoPreview.checked = localStorage.getItem("digital8-auto-preview") === "true";
function element(tag, parent, text) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; parent.appendChild(el); return el; }
function status(message, error = false) { $("status").textContent = message; $("status").classList.toggle("error", error); }
function reportOperationError(error) {
    const detail = error?.message || error?.$Msge || (typeof error === "string" && error) || "エラーの詳細が返されませんでした。";
    const banner = $("operation-error"), message = "Digital8: " + detail;
    if (banner.textContent !== message) console.error(message);
    status("");
    banner.textContent = message;
    banner.style.display = "block";
}
function clearOperationError() {
    const banner = $("operation-error");
    banner.style.display = "none";
    banner.textContent = "";
}
function remember() { localStorage.setItem("digital8-settings-v1", settings.encode()); }
function clearAutoPreview() {
    if (autoTimer !== null) clearTimeout(autoTimer);
    autoTimer = null;
    autoTarget = null;
}
function selectedTarget() {
    const doc = ps.app.activeDocument;
    if (!doc || doc.activeLayers.length !== 1) return null;
    const layer = doc.activeLayers[0], pixel = canSplitLayer(ps, layer);
    if (ps.constants?.LayerKind && !pixel && layer.kind !== ps.constants.LayerKind.SMARTOBJECT) return null;
    return { documentID: doc.id, layerID: layer.id, pixel, label: doc.name + " / " + layer.name };
}
function sameTarget(a, b) { return a?.documentID === b?.documentID && a?.layerID === b?.layerID; }
function runAutoPreview() {
    if (!autoTarget || autoTimer !== null || busy || pendingCancel) return;
    const target = selectedTarget();
    const unchanged = sameTarget(target, autoTarget);
    clearAutoPreview();
    if (unchanged) void operate("preview");
    else status("文書かレイヤーが変わりました。「プレビュー更新」で選択中のレイヤーに反映します。");
}
function scheduleAutoPreview(target = selectedTarget()) {
    clearAutoPreview();
    if (!panelVisible || pendingCancel) return;
    autoTarget = target;
    if (!autoTarget) { status("画像レイヤーかスマートオブジェクトを1つ選択してください。"); return; }
    if (!busy) status("設定の変更をプレビューへ反映します…");
    // Coalesce slider events and keep just the most recent edit during a render.
    autoTimer = setTimeout(() => { autoTimer = null; runAutoPreview(); }, 300);
}
function dirty() {
    remember();
    if (autoPreview.checked) scheduleAutoPreview();
    else status("設定を変更しました。「プレビュー更新」で反映します。");
}
const session = new PreviewSession(ps, (state, error) => {
    if (state === "rendering") status("C++で計算中… Photoshopの進捗表示から中止できます。");
    if (state === "splitting") status("追加レイヤーを作成中…");
    if (state === "preview") { clearOperationError(); status("メイン画面に表示中。「適用」か「取消」で終了します。"); }
    if (state === "error") { clearAutoPreview(); reportOperationError(error); }
    buttons();
});
function setDisabled(el, disabled) { el.disabled = disabled; if (disabled) el.setAttribute("disabled", ""); else el.removeAttribute("disabled"); }
function syncSplitOutput() {
    const target = selectedTarget(), eligible = !!target?.pixel;
    const label = target?.label || "画像レイヤーかスマートオブジェクトを選択してください。";
    if ($("target").textContent !== label) $("target").textContent = label;
    for (const [mode, box] of Object.entries(splitOutputs)) {
        const checked = !!eligible && splitSelected[mode];
        const disabled = !eligible || busy || pendingCancel;
        if (box.checked !== checked) box.checked = checked;
        if (box.disabled !== disabled) setDisabled(box, disabled);
    }
    return target;
}
function buttons() {
    setDisabled($("preview"), busy || pendingCancel); setDisabled($("apply"), busy || pendingCancel);
    setDisabled($("cancel"), !session.running || pendingCancel || session.closing);
    setDisabled($("read-existing"), session.running || busy);
    syncSplitOutput();
}
function selectedColour() {
    colour = Math.max(0, Math.min(colour, settings.get("colour_count") - 1));
    const rgb = settings.colour(colour), hex = "#" + rgb.map(c => c.toString(16).padStart(2, "0")).join("").toUpperCase();
    $("colour-info").textContent = settings.get("colour_count") + "色 / " + (colour + 1) + "番  RGB " + rgb.join(", ");
    $("hex").value = hex; $("selected-chip").style.backgroundColor = hex;
    rgbControls.forEach((row, i) => { row.input.value = String(rgb[i]); row.slider.value = rgb[i]; });
    Array.from($("swatches").children).forEach((el, i) => el.classList.toggle("selected", i === colour));
}
function paintSwatch(el, index, rgb) {
    const label = (index + 1) + "番 RGB " + rgb.join(", ");
    el.style.backgroundColor = "rgb(" + rgb.join(",") + ")";
    el.setAttribute("title", label);
    el.setAttribute("aria-label", label);
}
function swatches() {
    $("swatches").innerHTML = "";
    for (let i = 0; i < settings.get("colour_count"); i++) {
        const el = element("div", $("swatches")); el.className = "swatch";
        el.setAttribute("role", "button"); el.setAttribute("tabindex", "0");
        paintSwatch(el, i, settings.colour(i));
        el.addEventListener("click", () => { colour = i; selectedColour(); });
        el.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { colour = i; selectedColour(); } });
    }
    selectedColour();
}
function editColour(rgb) {
    settings.colour(colour, rgb);
    paintSwatch($("swatches").children[colour], colour, rgb);
    selectedColour();
    dirty();
}
function picker(el, labels, selected, change) {
    const previousListener = pickerListeners.get(el);
    if (previousListener) el.removeEventListener("change", previousListener);

    el.innerHTML = "";
    const menu = element("sp-menu", el);
    menu.setAttribute("slot", "options");
    labels.forEach((label, i) => {
        const item = element("sp-menu-item", menu, label);
        if (i === selected) item.setAttribute("selected", "");
    });

    const listener = event => {
        const index = event.target.selectedIndex;
        if (Number.isInteger(index) && index >= 0 && index < labels.length) change(index);
    };
    el.addEventListener("change", listener);
    pickerListeners.set(el, listener);
}
function setPaletteOptionsOpen(open) {
    $("palette-options").style.display = open ? "flex" : "none";
    $("palette").setAttribute("aria-expanded", String(open));
}
function palettes() {
    const current = settings.get("palette_index");
    const machine = settings.get("machine");
    const options = $("palette-options");
    const preset = data.palettes[current];
    $("palette").textContent = (preset && preset.machine === machine ? preset.label : "カスタム") + " ▾";
    setPaletteOptionsOpen(false);
    options.innerHTML = "";
    data.palettes.forEach((palette, index) => {
        if (palette.machine !== machine) return;
        const selected = index === current;
        const option = element("sp-action-button", options, (selected ? "✔ " : "") + palette.label);
        option.setAttribute("aria-pressed", String(selected));
        option.addEventListener("click", () => {
            if (!selected) {
                settings.palette(index);
                colour = 0;
                palettes();
                dirty();
            } else {
                setPaletteOptionsOpen(false);
            }
        });
    });
    swatches();
}
function showPage() {
    $("settings").innerHTML = "";
    Array.from($("tabs").children).forEach((el, i) => el.classList.toggle("selected", i === page));
    const spec = pages[page], machine = settings.get("machine");
    for (const field of spec.fields || []) {
        const [name, label, min, max, step] = field;
        const row = element("div", $("settings")); row.className = "setting";
        element("sp-label", row, label); const values = element("div", row); values.className = "value-row";
        const slider = element("sp-slider", values); slider.setAttribute("min", "0"); slider.setAttribute("max", String(Math.round((max - min) / step))); slider.setAttribute("show-value", "false"); slider.setAttribute("aria-label", label);
        const input = element("sp-textfield", values); input.setAttribute("aria-label", label);
        const spin = element("div", values); spin.className = "spin";
        const up = element("sp-action-button", spin, "▲"), down = element("sp-action-button", spin, "▼");
        up.setAttribute("aria-label", label + "を増やす"); down.setAttribute("aria-label", label + "を減らす");
        const sync = () => { const v = settings.get(name); input.value = String(Number(v.toFixed(4))); slider.value = Math.round((v - min) / step); };
        const change = v => { try { settings.set(name, normalize(field, v)); sync(); dirty(); } catch (e) { input.setAttribute("invalid", ""); status(e.message, true); } };
        slider.addEventListener("input", () => change(min + Number(slider.value) * step));
        input.addEventListener("change", () => { input.removeAttribute("invalid"); change(input.value); });
        up.addEventListener("click", () => change(Number(Math.min(max, settings.get(name) + step).toFixed(4))));
        down.addEventListener("click", () => change(Number(Math.max(min, settings.get(name) - step).toFixed(4))));
        if ((name === "max_colours" && machine !== 2) || (name === "groups" && machine === 2)) [slider, input, up, down].forEach(el => setDisabled(el, true));
        sync();
    }
    const checks = element("div", $("settings")); checks.className = "checks";
    for (const [name, label] of spec.checks || []) {
        const box = element("sp-checkbox", checks, label); box.checked = !!settings.get(name);
        box.addEventListener("change", () => { settings.set(name, box.checked ? 1 : 0); dirty(); });
    }
    for (const [name, label, choices] of spec.choices || []) {
        const row = element("div", $("settings")); row.className = "select-row"; element("sp-label", row, label);
        const select = element("sp-dropdown", row); picker(select, choices, settings.get(name), i => { settings.set(name, i); dirty(); }); setDisabled(select, machine === 2);
    }
}
function syncMachine() {
    Array.from($("machine").querySelectorAll("sp-menu-item")).forEach((item, i) => { item.selected = i === settings.get("machine"); });
    palettes(); showPage();
}
async function operate(type) {
    if (busy || pendingCancel) return;
    clearAutoPreview();
    // Validate all edited fields before sending a frozen snapshot to native code.
    for (const el of Array.from($("settings").querySelectorAll("sp-textfield"))) if (el.hasAttribute("invalid")) { status("赤く表示した数値を直してください。", true); return; }
    const target = selectedTarget();
    if (!target) { status("画像レイヤーかスマートオブジェクトを1つ選択してください。", true); return; }
    const snapshot = settings.encode(); busy = true; buttons();
    try {
        if (type === "commit") {
            const split = target.pixel ? { ...splitSelected } : {};
            const grouped = await session.commit(snapshot, split);
            clearOperationError();
            const added = [split.colour && "「使用色ごと」", split.fill && "「塗りごと」"].filter(Boolean).join("・");
            status(added ? added + "を非表示で追加しました。レイヤーパネルで目を切り替えて確認できます。" :
                grouped ? "「タイル模様を保持」グループを作成しました。" : "Digital8を適用しました。");
        } else {
            await session.update(snapshot);
            if (!pendingCancel) status(autoTarget ? "最新の設定をプレビューへ反映します…" :
                snapshot === settings.encode() ? "メイン画面に表示中。「適用」か「取消」で終了します。" :
                "計算中に設定が変わりました。もう一度「プレビュー更新」を押してください。");
        }
    } catch (e) { clearAutoPreview(); if (!pendingCancel) reportOperationError(e); }
    finally {
        if (type === "commit") clearAutoPreview();
        busy = false; buttons(); runAutoPreview();
    }
}
async function cancel() {
    clearAutoPreview();
    if (!session.running) return;
    if (session.closing) return session.completion;
    if (pendingCancel) return; pendingCancel = true; buttons();
    try { await session.cancel(); clearOperationError(); status("プレビューを取り消し、元の状態へ戻しました。"); }
    catch (e) { reportOperationError(e); }
    finally { clearAutoPreview(); pendingCancel = false; buttons(); }
}
pages.forEach((p, i) => { const tab = element("sp-action-button", $("tabs"), p.title); tab.addEventListener("click", () => { page = i; showPage(); }); });
$("machine").addEventListener("change", () => {
    savedMachines[settings.get("machine")] = settings.encode(); const next = $("machine").selectedIndex;
    settings = new Settings(next, savedMachines[next] || data.defaults[next]); colour = 0; syncMachine(); dirty();
});
$("palette").addEventListener("click", () => setPaletteOptionsOpen($("palette-options").style.display === "none"));
$("palette-options").addEventListener("keydown", event => {
    if (event.key === "Escape") setPaletteOptionsOpen(false);
});
$("edit-colour").addEventListener("click", () => {
    const hex = String($("hex").value).trim();
    if (!/^#[0-9a-f]{6}$/i.test(hex)) { status("色は #RRGGBB の形式で入力してください。", true); return; }
    editColour([1, 3, 5].map(i => parseInt(hex.substr(i, 2), 16)));
});
["赤", "緑", "青"].forEach((label, channel) => {
    const row = element("div", $("rgb-editor")); row.className = "setting";
    element("sp-label", row, label); const values = element("div", row); values.className = "value-row";
    const slider = element("sp-slider", values); slider.setAttribute("min", "0"); slider.setAttribute("max", "255"); slider.setAttribute("show-value", "false");
    const input = element("sp-textfield", values); input.setAttribute("aria-label", "パレットの" + label);
    const spin = element("div", values); spin.className = "spin";
    const up = element("sp-action-button", spin, "▲"), down = element("sp-action-button", spin, "▼");
    const change = value => {
        value = Number(value); if (!Number.isInteger(value) || value < 0 || value > 255) { status("RGBは0〜255の整数で指定してください。", true); return; }
        const rgb = settings.colour(colour); rgb[channel] = value; editColour(rgb);
    };
    slider.addEventListener("input", () => change(Number(slider.value)));
    input.addEventListener("change", () => change(input.value));
    up.addEventListener("click", () => change(Math.min(255, settings.colour(colour)[channel] + 1)));
    down.addEventListener("click", () => change(Math.max(0, settings.colour(colour)[channel] - 1)));
    rgbControls.push({ input, slider });
});
$("toggle-rgb").addEventListener("click", () => { const editor = $("rgb-editor"); editor.style.display = editor.style.display === "none" ? "block" : "none"; });
autoPreview.addEventListener("change", () => {
    localStorage.setItem("digital8-auto-preview", String(autoPreview.checked));
    if (autoPreview.checked) scheduleAutoPreview();
    else {
        clearAutoPreview();
        if (!busy) status("「プレビュー更新」で設定を反映します。");
    }
});
for (const [mode, box] of Object.entries(splitOutputs))
    box.addEventListener("change", () => { splitSelected[mode] = box.checked; });
$("layer-output").addEventListener("pointerenter", () => {
    if (!busy && !pendingCancel) syncSplitOutput();
});
$("preview").addEventListener("click", () => operate("preview"));
$("apply").addEventListener("click", () => operate("commit"));
$("cancel").addEventListener("click", cancel);
$("read-existing").addEventListener("click", async () => {
    if (session.running || busy || pendingCancel) return;
    clearAutoPreview();
    busy = true; buttons();
    try {
        const target = await ps.core.executeAsModal(() => readTarget(ps), { commandName: "Digital8 設定読み込み" });
        if (!target.existing || !target.existing.snapshot) { status("このレイヤーにはDigital8のスマートフィルタがありません。"); return; }
        settings = new Settings(2, target.existing.snapshot); colour = 0; syncMachine(); remember();
        status("既存の設定を読み込みました。「適用」でこのDigital8を更新します。");
        if (autoPreview.checked) scheduleAutoPreview();
    } catch (e) { status(e.message, true); }
    finally { busy = false; buttons(); runAutoPreview(); }
});
const targetEvents = ["select", "open", "close", "make", "delete", "newPlacedLayer", "undo", "redo"];
function targetChanged() {
    if (!panelVisible || busy || pendingCancel) return;
    const target = syncSplitOutput();
    if (sameTarget(target, lastTarget)) return;
    lastTarget = target;
    if (target && autoPreview.checked && !session.running) scheduleAutoPreview(target);
}
function showPanel() {
    panelVisible = true;
    targetChanged();
    // Photoshop can report an open/close event before activeLayers has changed.
    if (targetTimer === null) targetTimer = setInterval(targetChanged, 400);
}
function hidePanel() {
    panelVisible = false;
    lastTarget = null;
    if (targetTimer !== null) clearInterval(targetTimer);
    targetTimer = null;
    return cancel();
}
ps.action?.addNotificationListener?.(targetEvents, targetChanged);
entrypoints.setup({
    panels: { digital8: { create() {}, show: showPanel, hide: hidePanel } },
    plugin: { create() {}, destroy() {
        ps.action?.removeNotificationListener?.(targetEvents, targetChanged);
        return hidePanel();
    } }
});
try { syncMachine(); buttons(); } catch (e) { status("パネル初期化エラー: " + e.message + " / " + e.stack, true); }
