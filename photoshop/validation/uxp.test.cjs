const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { runInNewContext } = require('node:vm');
const { Settings, data } = require('../uxp/settings.js');
const { PreviewSession, FILTER, readTarget } = require('../uxp/session.js');
const { collectColours, collectFills, colourPixels, decodeFillMap, writeLayerOutput } = require('../uxp/colour_layers.js');

function host() {
    const h = { pixels: 'source', history: [], renders: [], active: 1, failure: false, context: null };
    h.ps = {
        app: { get activeDocument() { return { id: h.active, activeLayers: [1] }; } },
        action: { async batchPlay(commands) {
            assert.equal(commands[0]._obj, FILTER); assert.equal(commands[0]._options.dialogOptions, 'silent');
            h.renders.push({ input: h.pixels, settings: commands[0]['$d8st'] }); h.pixels += '->' + commands[0]['$d8st'];
            if (h.failure) return [{ _obj: 'error', message: 'native failed' }];
            return [{}];
        } },
        core: { async redrawDocument({ documentID }) { assert.equal(documentID, 1); h.display = h.pixels; }, async executeAsModal(fn, options) {
            assert.equal(options.interactive, true);
            const pending = new Set();
            const ctx = { isCancelled: false, hostControl: {
                async suspendHistory({ documentID, name }) { assert.equal(documentID, 1); const s = { source: h.pixels, name }; pending.add(s); return s; },
                async resumeHistory(s, commit) { assert(pending.delete(s)); if (commit) h.history.push(s.name); else h.pixels = s.source; }
            } }; h.context = ctx;
            try { return await fn(ctx); }
            catch (error) { for (const s of pending) h.pixels = s.source; throw error; }
        } }
    };
    return h;
}
test('C++ exported defaults, palette layout and full snapshot roundtrip', () => {
    for (let m = 0; m < 3; m++) {
        const s = new Settings(m); assert.equal(s.encode(), data.defaults[m]); assert.equal(s.get('machine'), m);
        const p = data.palettes[s.get('palette_index')]; assert.equal(s.get('colour_count'), p.colours.length);
        p.colours.forEach((c, i) => assert.deepEqual(s.colour(i), c));
    }
    const retro = new Settings(1);
    const old = Buffer.from(retro.encode().slice(5), 'hex').subarray(0, data.fields.four_colour.offset);
    old.writeUInt32LE(1, 4);
    const migrated = new Settings(1, 'D8N1:' + old.toString('hex'));
    assert.equal(migrated.encode(), retro.encode());
    retro.set('four_colour', 1); retro.set('five_colour', 1);
    retro.palette(retro.get('palette_index'));
    assert.equal(new Settings(1, retro.encode()).get('four_colour'), 1);
    assert.equal(new Settings(1, retro.encode()).get('five_colour'), 1);
    const s = new Settings(2); s.set('brightness', 1.25); s.set('pixel_size', 3); s.colour(1, [20, 30, 40]);
    const restored = new Settings(2, s.encode()); assert.equal(restored.get('brightness'), 1.25); assert.equal(restored.get('pixel_size'), 3); assert.deepEqual(restored.colour(1), [20, 30, 40]);
    assert.throws(() => s.set('pixel_size', .5)); assert.throws(() => s.colour(1, [-1, 0, 0]));
});
test('UXP snapshot with custom palette is accepted and reproduced exactly by C++', () => {
    const root = path.resolve(__dirname, '..'), s = new Settings(1);
    s.set('brightness', 1.25); s.set('pixel_size', 3); s.colour(0, [20, 30, 40]);
    const input = path.join(root, 'build/photoshop-smoke/input.rgba'), snapshot = path.join(root, 'build/uxp-settings.txt'), result = path.join(root, 'build/uxp-roundtrip.txt');
    fs.writeFileSync(snapshot, s.encode());
    const run = spawnSync(path.join(root, 'build/validation/native_cli.exe'), [input, path.join(root, 'build/uxp-output.rgba'), '1', '--settings', snapshot, '--save-settings', result]);
    assert.equal(run.status, 0, String(run.stderr)); assert.equal(fs.readFileSync(result, 'utf8'), s.encode());
});
function panel(ps = {}, saved = []) {
    class Element extends EventTarget {
        children = [];
        style = {};
        classList = { toggle() {} };
        appendChild(child) { this.children.push(child); }
        set innerHTML(_) { this.children = []; }
        setAttribute() {}
        removeAttribute() {}
        querySelectorAll() { return []; }
    }
    const elements = new Map();
    const document = {
        createElement: () => new Element(),
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new Element());
            return elements.get(id);
        }
    };
    const panelPath = path.resolve(__dirname, '../uxp/main.js');
    const panelRequire = createRequire(panelPath);
    const saves = [], timers = new Map(), intervals = new Map(), storage = new Map(saved);
    let setup;
    runInNewContext(fs.readFileSync(panelPath, 'utf8'), {
        document,
        localStorage: {
            getItem: key => storage.get(key),
            setItem(key, value) { storage.set(key, value); if (key === 'digital8-settings-v1') saves.push(value); }
        },
        setTimeout(fn, delay) { assert.equal(delay, 300); const id = Symbol(); timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
        setInterval(fn, delay) { assert.equal(delay, 400); const id = Symbol(); intervals.set(id, fn); return id; },
        clearInterval(id) { intervals.delete(id); },
        require(name) {
            if (name === 'photoshop') return ps;
            if (name === 'uxp') return { entrypoints: { setup(value) { setup = value; } } };
            return panelRequire(name);
        }
    });
    function fire(id, type = 'click', properties = {}) {
        const el = elements.get(id);
        Object.assign(el, properties);
        el.dispatchEvent(new Event(type));
    }
    return {
        elements, saves, timers, intervals, storage, fire, lifecycle: setup.panels.digital8,
        flush() { for (const [id, fn] of timers) { timers.delete(id); fn(); } }
    };
}

test('Palette button changes the checked item, displayed name, swatches and persisted RGB together', () => {
    const { elements, saves } = panel();
    for (const machine of [2, 0, 1, 2]) {
        const machineControl = elements.get('machine');
        machineControl.selectedIndex = machine;
        machineControl.dispatchEvent(new Event('change'));
        const presets = data.palettes.map((palette, index) => ({ ...palette, index })).filter(p => p.machine === machine);
        for (const choice of [1, 0]) {
            const previousSaves = saves.length;
            const button = elements.get('palette');
            const options = elements.get('palette-options');
            button.dispatchEvent(new Event('click'));
            assert.equal(options.style.display, 'flex');
            options.children[choice].dispatchEvent(new Event('click'));
            assert.equal(options.style.display, 'none');
            assert.equal(saves.length, previousSaves + 1);
            const preset = presets[choice];
            assert.equal(button.textContent, `${preset.label} ▾`);
            assert.deepEqual(options.children.filter(option => option.textContent.startsWith('✔ ')).map(option => option.textContent), [`✔ ${preset.label}`]);
            const settings = new Settings(machine, saves.at(-1));
            assert.equal(settings.get('palette_index'), preset.index);
            assert.equal(settings.get('colour_count'), preset.colours.length);
            assert.deepEqual(elements.get('swatches').children.map(el => el.style.backgroundColor), preset.colours.map(rgb => `rgb(${rgb.join(',')})`));
            preset.colours.forEach((rgb, index) => assert.deepEqual(settings.colour(index), rgb));
        }
    }
});
const settle = () => new Promise(resolve => setImmediate(resolve));
test('independent layer output options are enabled only for a single pixel layer', () => {
    const h = host();
    h.ps.constants = { LayerKind: { NORMAL: 'pixel', SMARTOBJECT: 'smart' } };
    let layers = [{ id: 1, kind: 'pixel' }];
    h.ps.app = { get activeDocument() { return { id: 1, activeLayers: layers }; } };
    let notify;
    h.ps.action.addNotificationListener = (_, callback) => { notify = callback; };
    const p = panel(h.ps), box = p.elements.get('split-output'), fill = p.elements.get('fill-output');
    assert.equal(box.checked, false);
    assert.equal(box.disabled, false);
    p.fire('split-output', 'change', { checked: true });
    assert.equal(fill.checked, false);
    p.fire('fill-output', 'change', { checked: true });
    layers = [{ id: 2, kind: 'smart' }];
    notify();
    assert.equal(box.disabled, true);
    assert.equal(box.checked, false);
    assert.equal(fill.checked, false);
    assert.equal(fill.disabled, true);
    layers = [{ id: 1, kind: 'pixel' }, { id: 2, kind: 'smart' }];
    notify();
    assert.equal(box.disabled, true);
    layers = [{ id: 1, kind: 'pixel' }];
    notify();
    assert.equal(box.disabled, false);
    assert.equal(box.checked, true);
    assert.equal(fill.disabled, false);
    assert.equal(fill.checked, true);
});

test('a stale Photoshop notification cannot leave the extra-layer checkboxes disabled', async () => {
    const h = host();
    h.ps.constants = { LayerKind: { NORMAL: 'pixel', GROUP: 'group' } };
    let layer = { id: 1, kind: 'group' };
    h.ps.app = { get activeDocument() { return { id: 1, activeLayers: [layer] }; } };
    let notify;
    h.ps.action.addNotificationListener = (_, callback) => { notify = callback; };
    const p = panel(h.ps), colour = p.elements.get('split-output'), fill = p.elements.get('fill-output');
    p.lifecycle.show();
    p.lifecycle.show();
    assert.equal(p.intervals.size, 1);
    notify(); // Open arrived before Photoshop exposed the new active layer.
    layer = { id: 2, kind: 'pixel' };
    assert.equal(fill.disabled, true);
    for (const poll of p.intervals.values()) poll();
    assert.equal(colour.disabled, false);
    assert.equal(fill.disabled, false);
    layer = { id: 3, kind: 'group' };
    notify();
    layer = { id: 4, kind: 'pixel' };
    p.fire('layer-output', 'pointerenter');
    assert.equal(fill.disabled, false);
    await p.lifecycle.hide();
    assert.equal(p.intervals.size, 0);
});

test('colour buckets handle transparency, offset layers and selection boundary colours', () => {
    const red = [255, 0, 0], blue = [0, 0, 255], mixed = [60, 70, 90];
    const image = { left: -2, top: 3, width: 4, height: 2, components: 4, bytes: new Uint8Array([
        ...red, 255, ...blue, 128, ...blue, 0, ...red, 255,
        ...red, 0, ...blue, 27, ...mixed, 255, ...red, 255
    ]) };
    const selection = {
        bounds: { left: -1, top: 3, right: 2, bottom: 5 },
        bytes: new Uint8Array([255, 0, 255, 0, 128, 255])
    };
    const result = collectColours(image, new Set([0xFF0000, 0x0000FF, 0xFFFFFF]), selection);
    assert.deepEqual(result.colours.map(c => c.key), [-1, 0x0000FF, 0xFF0000]);
    const [other, bluePixels, redPixels] = result.colours.map(c => colourPixels(result, c));
    assert.deepEqual(bluePixels, { rgba: new Uint8Array([...blue, 128]), left: -1, top: 3, width: 1, height: 1 });
    assert.deepEqual(redPixels, { rgba: new Uint8Array([...red, 255, ...red, 255]), left: 1, top: 3, width: 1, height: 2 });
    assert.equal(other.left, -2);
    assert.deepEqual(Array.from(other.rgba), [
        ...red, 255, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, ...blue, 27, ...mixed, 255
    ]);
});

test('native fill ids keep pattern and flat layers aligned except for selected mixed tile pixels', () => {
    const yellow = [255, 255, 0], blue = [0, 0, 255], grey = [128, 128, 128], black = [0, 0, 0];
    const buffer = new ArrayBuffer(24 + 4 + 5 * 2), view = new DataView(buffer);
    [0x31463844, -2, 3, 5, 1, 1].forEach((value, i) => view.setInt32(i * 4, value, true));
    new Uint8Array(buffer, 24, 4).set([...grey, 1]);
    new Uint16Array(buffer, 28).set([0, 0, 0, 0xffff, 0]);
    const map = decodeFillMap(buffer);
    const image = { left: -2, top: 3, width: 5, height: 1, components: 4, bytes: new Uint8Array([
        ...yellow, 255, ...blue, 90, ...blue, 145, ...black, 255, ...yellow, 0
    ]) };
    const selection = { bounds: { left: -2, right: 3, top: 3, bottom: 4 },
        bytes: new Uint8Array([255, 255, 160, 255, 255]) };
    const patterned = collectFills(image, map, selection, false);
    const flat = collectFills(image, map, selection, true);
    assert.deepEqual(flat.colours.map(bucket => bucket.key), [-1, 0, 0xffff]);
    const pixels = res => res.colours.map(bucket => [...colourPixels(res, bucket).rgba]);
    assert.deepEqual(pixels(patterned), [[...blue, 145], [...yellow, 255, ...blue, 90], [...black, 255]]);
    assert.deepEqual(pixels(flat), [[...blue, 145], [...grey, 255, ...grey, 90], [...black, 255]]);
    assert.throws(() => decodeFillMap(buffer.slice(0, -2)), /塗りの情報/);
});

test('layer output retains patterned pixels and appends only the requested hidden groups', async () => {
    const yellow = [255, 255, 0], blue = [0, 0, 255], grey = [128, 128, 128];
    const original = new Uint8Array([...yellow, 255, ...blue, 90]);
    const output = {
        image: { left: 3, top: 4, width: 2, height: 1, components: 4, bytes: original },
        palette: new Set([0xFFFF00, 0x0000FF]), selection: null
    };
    const map = { left: 3, top: 4, width: 2, height: 1, ids: new Uint16Array([0, 0]),
        styles: [{ rgb: grey, mixed: true }] };
    for (const options of [{ fill: true }, { colour: true }, { fill: true, colour: true }]) {
        const layers = [];
        const make = async attrs => {
            const layer = { id: layers.length, visible: true, children: [], ...attrs,
                move(parent, placement) {
                    if (placement === 'inside') parent.children.push(this);
                } };
            layers.push(layer);
            return layer;
        };
        const doc = { id: 1, createPixelLayer: make, createLayerGroup: make };
        const ps = {
            constants: { BlendMode: { NORMAL: 'normal' }, ElementPlacement: { PLACEINSIDE: 'inside' } },
            action: { batchPlay: async () => [{}] },
            imaging: {
                createImageDataFromBuffer: async bytes => ({ bytes, dispose() {} }),
                async putPixels({ layerID, imageData }) { layers[layerID].pixels = [...imageData.bytes]; }
            }
        };
        await writeLayerOutput(ps, {}, doc, { name: 'source', fillOpacity: 100 }, output, map, options);
        const groups = new Map(layers[0].children.map(layer => [layer.name, layer]));
        assert.equal(groups.size, 1 + Number(!!options.fill) + Number(!!options.colour));
        assert.deepEqual(groups.get('タイル模様を保持').children[0].pixels, [...original]);
        assert.equal(groups.get('タイル模様を保持').visible, true);
        if (options.fill) {
            assert.equal(groups.get('塗りごと').visible, false);
            assert.deepEqual(groups.get('塗りごと').children[0].pixels, [...grey, 255, ...grey, 90]);
        }
        if (options.colour) {
            assert.equal(groups.get('使用色ごと').visible, false);
            assert.deepEqual(groups.get('使用色ごと').children.map(layer => layer.name), ['#0000FF', '#FFFF00']);
        }
    }
});

test('auto-preview merges edits and uses the latest settings if a render is in progress', async () => {
    const h = host(), p = panel(h.ps);
    const firstSetting = () => p.elements.get('settings').children[0].children[1].children[0];
    function setPixelSize(value) {
        const slider = firstSetting();
        slider.value = value - 1;
        slider.dispatchEvent(new Event('input'));
    }
    const render = h.ps.action.batchPlay;
    let release;
    const blocked = new Promise(resolve => { release = resolve; });
    h.ps.action.batchPlay = async commands => { await render(commands); await blocked; return [{}]; };

    setPixelSize(2);
    assert.equal(p.timers.size, 0);
    p.fire('auto-preview', 'change', { checked: true });
    p.fire('machine', 'change', { selectedIndex: 1 });
    p.elements.get('palette-options').children[1].dispatchEvent(new Event('click'));
    setPixelSize(4);
    assert.equal(p.timers.size, 1);
    assert.equal(h.renders.length, 0);
    p.flush();
    await settle();
    assert.equal(h.renders.length, 1);
    let used = new Settings(2, h.renders[0].settings);
    assert.equal(used.get('machine'), 1);
    assert.equal(used.get('palette_index'), new Settings(2, p.saves.at(-1)).get('palette_index'));
    assert.equal(used.get('pixel_size'), 4);

    setPixelSize(6);
    p.flush();
    assert.equal(h.renders.length, 1);
    setPixelSize(7);
    p.flush();
    release();
    await settle();
    assert.equal(h.renders.length, 2);
    assert.equal(h.renders[1].input, 'source');
    used = new Settings(2, h.renders[1].settings);
    assert.equal(used.get('pixel_size'), 7);
    assert.equal(h.display, 'source->' + h.renders[1].settings);
    await p.lifecycle.hide();
    assert.equal(h.display, 'source');
});
test('a panel opened with auto-preview on renders immediately, and layer checkboxes do not rerender', async () => {
    const h = host(), layer = { id: 2, kind: 'pixel', name: 'source' };
    h.ps.app = { activeDocument: { id: 1, name: 'test image', activeLayers: [layer] } };
    h.ps.constants = { LayerKind: { NORMAL: 'pixel', SMARTOBJECT: 'smart' } };
    const p = panel(h.ps, [['digital8-auto-preview', 'true']]);
    p.lifecycle.show();
    assert.equal(p.timers.size, 1);
    p.flush();
    await settle();
    assert.equal(h.renders.length, 1);
    assert.equal(h.display, 'source->' + h.renders[0].settings);
    p.fire('split-output', 'change', { checked: true });
    p.fire('fill-output', 'change', { checked: true });
    for (const poll of p.intervals.values()) poll();
    p.flush();
    assert.equal(p.timers.size, 0);
    assert.equal(h.renders.length, 1);
    assert.deepEqual(h.history, []);
    await p.lifecycle.hide();
    assert.equal(h.display, 'source');
});
test('auto-preview discards scheduled work on disable, cancel, hide and target switch', async () => {
    const h = host(), p = panel(h.ps);
    const toggle = checked => p.fire('auto-preview', 'change', { checked });
    toggle(true);
    toggle(false);
    p.flush();
    assert.equal(p.storage.get('digital8-auto-preview'), 'false');

    toggle(true);
    p.fire('cancel');
    p.flush();
    toggle(true);
    await p.lifecycle.hide();
    p.flush();
    p.lifecycle.show();
    toggle(true);
    h.active = 2;
    p.flush();
    await settle();
    assert.equal(h.renders.length, 0);
    assert.match(p.elements.get('status').textContent, /レイヤーが変わりました/);

    h.active = 1;
    p.fire('preview');
    await settle();
    assert.equal(h.renders.length, 1);
    p.fire('hex', 'change', { value: '#123456' });
    p.fire('edit-colour');
    assert.equal(p.timers.size, 1);
    p.fire('cancel');
    p.flush();
    await settle();
    assert.equal(h.renders.length, 1);
    assert.equal(h.display, 'source');
});
test('preview renders from baseline every time and Cancel restores pixels/history', async () => {
    const h = host(), s = new PreviewSession(h.ps);
    await s.update('one'); assert.equal(h.pixels, 'source->one'); assert.equal(h.display, h.pixels);
    await s.update('two'); assert.equal(h.pixels, 'source->two'); assert.equal(h.renders[1].input, 'source');
    await s.cancel(); assert.equal(h.pixels, 'source'); assert.equal(h.display, 'source'); assert.deepEqual(h.history, []); assert.equal(s.running, false);
});
test('a preview error remains visible while editing and clears after a successful retry', async () => {
    const h = host(), p = panel(h.ps);
    const render = h.ps.action.batchPlay;
    h.ps.action.batchPlay = async () => { throw { $Msge: 'Photoshop error -1715' }; };
    p.fire('preview');
    await settle();

    const banner = p.elements.get('operation-error');
    assert.equal(banner.style.display, 'block');
    assert.match(banner.textContent, /Photoshop error -1715/);
    p.fire('machine', 'change', { selectedIndex: 0 });
    assert.match(banner.textContent, /Photoshop error -1715/);

    h.ps.action.batchPlay = render;
    p.fire('preview');
    await settle();
    assert.equal(banner.style.display, 'none');
    assert.equal(h.renders.length, 1);
    await p.lifecycle.hide();
});
test('apply always groups a pixel layer, while preview never requests layer output', async () => {
    const sourceCode = fs.readFileSync(path.resolve(__dirname, '../uxp/session.js'), 'utf8');
    for (const extras of [{}, { colour: true, fill: true }]) {
        const h = host(), layer = { id: 2, kind: 'pixel', visible: true };
        h.ps.app = { activeDocument: { id: 1, activeLayers: [layer] } };
        const commands = [], writes = [];
        const batchPlay = h.ps.action.batchPlay;
        h.ps.action.batchPlay = async actions => {
            commands.push(actions[0]);
            return batchPlay(actions);
        };
        const module = { exports: {} };
        runInNewContext(sourceCode, { module, require: () => ({
            canSplitLayer: (_, target) => target.kind === 'pixel',
            createFillTransfer: async () => ({
                path: 'temporary-file', read: async () => 'fill-map', async dispose() {}
            }),
            captureLayerOutput: async () => 'rendered-layer',
            async writeLayerOutput(_ps, _ctx, _doc, source, output, map, selected) {
                writes.push({ source, output, map, selected });
            },
        }) });
        const session = new module.exports.PreviewSession(h.ps);
        await session.update('one');
        assert.equal(h.display, 'source->one');
        assert.equal(commands[0].$d8fm, undefined);
        assert.equal(writes.length, 0);
        assert.equal(await session.commit('one', extras), true);
        assert.equal(commands[1].$d8fm, 'temporary-file');
        assert.equal(h.renders[1].input, 'source');
        assert.equal(writes.length, 1);
        assert.equal(writes[0].source, layer);
        assert.equal(writes[0].output, 'rendered-layer');
        assert.equal(writes[0].map, 'fill-map');
        assert.equal(writes[0].selected, extras);
        assert.equal(layer.visible, false);
        assert.deepEqual(h.history, ['Digital8 追加レイヤー']);
    }
});
test('Apply reuses current preview, or recalculates changed parameters, with one undo state', async () => {
    const h = host(), s = new PreviewSession(h.ps); await s.update('one'); await s.commit('one');
    assert.equal(h.renders.length, 1); assert.deepEqual(h.history, ['Digital8']);
    const h2 = host(), s2 = new PreviewSession(h2.ps); await s2.update('one'); await s2.commit('two');
    assert.equal(h2.pixels, 'source->two'); assert.equal(h2.renders.length, 2); assert.deepEqual(h2.history, ['Digital8']);
});
test('native failure rolls back all preview changes', async () => {
    const h = host(), s = new PreviewSession(h.ps); await s.update('one'); h.failure = true;
    await assert.rejects(s.update('two'), /native failed/); await s.completion;
    assert.equal(h.pixels, 'source'); assert.deepEqual(h.history, []); assert.equal(s.running, false);
});
test('Escape while waiting rolls back via Photoshop modal cleanup', async () => {
    const h = host(), s = new PreviewSession(h.ps); await s.update('one');
    h.context.isCancelled = true; h.context.onCancel(); await s.completion;
    assert.equal(h.pixels, 'source'); assert.deepEqual(h.history, []);
});
test('queued Cancel after an update and repeated preview do not accumulate filters', async () => {
    const h = host(), s = new PreviewSession(h.ps);
    const one = s.update('one'), two = s.update('two'), cancel = s.cancel(); await Promise.all([one, two, cancel]);
    assert.equal(h.pixels, 'source'); assert.equal(h.renders.length, 2); assert.deepEqual(h.history, []);
});
test('Apply without Preview and no-document failures settle their promises', async () => {
    const h = host(), s = new PreviewSession(h.ps); await s.commit('one'); assert.equal(h.pixels, 'source->one');
    h.ps.app = { activeDocument: null }; await assert.rejects(s.update('two'), /1つ選択/); await s.completion;
});
test('Existing Digital8 is edited at its original filter index, preserving other filters', async () => {
    const h = host(); let setCommand = null;
    h.ps.constants = { LayerKind: { SMARTOBJECT: 'smart' } };
    h.ps.app = { activeDocument: { id: 1, activeLayers: [{ id: 7, kind: 'smart' }] } };
    const render = h.ps.action.batchPlay;
    h.ps.action.batchPlay = async commands => {
        const c = commands[0];
        if (c._obj === 'get') return [{ smartObject: { filterFX: [{ filter: { _obj: 'gaussianBlur' } }, { enabled: true, filter: { _obj: FILTER, '$d8st': 'old' } }] } }];
        assert.equal(c._obj, 'set'); setCommand = c;
        return render([{ ...c.filterFX.filter, _options: c._options }]);
    };
    assert.deepEqual((await readTarget(h.ps)).existing, { index: 2, snapshot: 'old' });
    const s = new PreviewSession(h.ps); await s.commit('new');
    assert.deepEqual(setCommand._target, [{ _ref: 'filterFX', _index: 2 }, { _ref: 'layer', _id: 7 }]);
    assert.equal(setCommand.filterFX._obj, 'filterFX'); assert.equal(setCommand.filterFX.filter['$d8st'], 'new');
});
test('Ambiguous or disabled existing Digital8 filters are rejected before editing', async () => {
    const h = host(); h.ps.constants = { LayerKind: { SMARTOBJECT: 'smart' } };
    h.ps.app = { activeDocument: { id: 1, activeLayers: [{ id: 7, kind: 'smart' }] } };
    h.ps.action.batchPlay = async () => [{ smartObject: { filterFX: [{ filter: { _obj: FILTER } }, { filter: { _obj: FILTER } }] } }];
    await assert.rejects(readTarget(h.ps), /複数/);
    h.ps.action.batchPlay = async () => [{ smartObject: { filterFX: [{ enabled: false, filter: { _obj: FILTER } }] } }];
    await assert.rejects(readTarget(h.ps), /無効/);
});
