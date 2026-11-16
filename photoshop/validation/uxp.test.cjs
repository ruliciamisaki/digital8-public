const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { runInNewContext } = require('node:vm');
const { Settings, data } = require('../uxp/settings.js');
const { PreviewSession, FILTER, readTarget } = require('../uxp/session.js');
const { collectColours, colourPixels } = require('../uxp/colour_layers.js');

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
function panel(ps = {}) {
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
    const saves = [], timers = new Map(), storage = new Map();
    let setup;
    runInNewContext(fs.readFileSync(panelPath, 'utf8'), {
        document,
        localStorage: {
            getItem: key => storage.get(key),
            setItem(key, value) { storage.set(key, value); if (key === 'digital8-settings-v1') saves.push(value); }
        },
        setTimeout(fn, delay) { assert.equal(delay, 300); const id = Symbol(); timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
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
        elements, saves, timers, storage, fire, lifecycle: setup.panels.digital8,
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
test('colour output is enabled only for a single pixel layer', () => {
    const h = host();
    h.ps.constants = { LayerKind: { NORMAL: 'pixel', SMARTOBJECT: 'smart' } };
    let layers = [{ id: 1, kind: 'pixel' }];
    h.ps.app = { get activeDocument() { return { id: 1, activeLayers: layers }; } };
    let notify;
    h.ps.action.addNotificationListener = (_, callback) => { notify = callback; };
    const p = panel(h.ps), box = p.elements.get('split-output');
    assert.equal(box.checked, false);
    assert.equal(box.disabled, false);
    p.fire('split-output', 'change', { checked: true });
    layers = [{ id: 2, kind: 'smart' }];
    notify();
    assert.equal(box.disabled, true);
    assert.equal(box.checked, false);
    layers = [{ id: 1, kind: 'pixel' }, { id: 2, kind: 'smart' }];
    notify();
    assert.equal(box.disabled, true);
    layers = [{ id: 1, kind: 'pixel' }];
    notify();
    assert.equal(box.disabled, false);
    assert.equal(box.checked, true);
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
