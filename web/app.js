const get = id => document.getElementById(id);
const pngType = "image/png";
const machineNames = { pc8801: "PC-8801", pc9801: "PC-9801", anime: "アニメ塗り" };
const numericFields = {
  "common-numbers": [
    ["pixel_size", "仮想ピクセル", 1, 4096, 1],
    ["line_threshold", "主線しきい値", 0, 120, 1],
    ["edge_strength", "輪郭抽出", 0, 1, 0.05],
    ["line_growth", "線の太さ", 0, 16, 0.25],
    ["trace_strength", "色トレス明度", 0.1, 0.95, 0.05],
  ],
  "retro-numbers": [["colour_groups", "共通色区分数", 2, 64, 1]],
  "anime-numbers": [
    ["anime_max_colours", "使用色数（0＝全候補）", 0, 256, 1],
    ["anime_tolerance", "面をまとめる色差", 0, 128, 1],
    ["anime_radius", "周囲をならす半径", 0, 8, 1],
    ["anime_min_area", "小さい面の整理", 0, 4096, 1],
  ],
  "colour-numbers": [
    ["brightness", "明度", 0, 10, 0.05], ["saturation", "彩度", 0, 10, 0.05],
    ["contrast", "コントラスト", 0, 10, 0.05], ["red_gain", "赤 (R)", 0, 10, 0.05],
    ["green_gain", "緑 (G)", 0, 10, 0.05], ["blue_gain", "青 (B)", 0, 10, 0.05],
    ["alpha_threshold", "二値化しきい値", 0, 255, 1],
  ],
};

const custom = new Map();
let catalog;
let machine;
let colourIndex = 0;
let source;
let preview;
let original;
let view = "source";
let worker;
let request;
let requestId = 0;
let workerFile;
let active;
let revision = 0;
let timer;
let rerender = false;

function status(message, error = false) {
  get("status").textContent = message;
  get("status").classList.toggle("error", error);
}

function updateControls() {
  get("preview").disabled = !source || !!active;
  get("download").disabled = !source || !!active;
  get("file").disabled = !catalog || !!active;
  get("original").disabled = !!active;
  get("cancel").hidden = !active;
  get("invert-mask").disabled = !get("save-mask").checked;
}

function populateNumbers() {
  for (const [section, fields] of Object.entries(numericFields)) {
    for (const [key, name, min, max, step] of fields) {
      const label = document.createElement("label");
      label.textContent = name;
      const input = document.createElement("input");
      Object.assign(input, { type: "number", min, max, step, required: true });
      input.dataset.setting = key;
      label.append(input);
      get(section).append(label);
    }
  }
}

function selectedPalette() {
  const id = get("palette").value;
  if (id === "custom") return { id, label: "Custom", colours: custom.get(machine) };
  return catalog.palettes[machine].find(palette => palette.id === id);
}

function updatePalettes(id) {
  const palettes = catalog.palettes[machine];
  const options = palettes.map(palette => new Option(palette.label, palette.id));
  if (custom.has(machine)) options.push(new Option("Custom", "custom"));
  get("palette").replaceChildren(...options);
  if (options.some(option => option.value === id)) get("palette").value = id;
  showColours();
}

function toHex(colour) {
  return "#" + colour.map(channel => channel.toString(16).padStart(2, "0")).join("");
}

function showColours() {
  const { colours } = selectedPalette();
  colourIndex = Math.min(colourIndex, colours.length - 1);
  const buttons = colours.map((colour, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.style.backgroundColor = toHex(colour);
    button.dataset.index = String(index);
    button.title = `${index + 1} · ${toHex(colour)}`;
    button.setAttribute("aria-label", button.title);
    return button;
  });
  const swatches = get("swatches");
  const top = swatches.scrollTop;
  swatches.replaceChildren(...buttons);
  swatches.scrollTop = top;
  showSelectedColour();
}

function showSelectedColour() {
  const { colours } = selectedPalette();
  const hex = toHex(colours[colourIndex]);
  const selected = get("swatches").querySelector("[aria-pressed=true]");
  selected?.setAttribute("aria-pressed", "false");
  get("swatches").children[colourIndex].setAttribute("aria-pressed", "true");
  get("colour-picker").value = hex;
  get("colour-hex").value = hex.toUpperCase();
  get("add-colour").hidden = get("remove-colour").hidden = machine !== "anime";
  get("add-colour").disabled = colours.length >= 4096;
  get("remove-colour").disabled = colours.length <= 1;
}

function installCustom(colours) {
  const count = machine === "pc8801" ? 8 : 16;
  if (!Array.isArray(colours) || !colours.length || colours.length > 4096 ||
      colours.some(colour => !Array.isArray(colour) || colour.length !== 3 ||
        colour.some(value => !Number.isInteger(value) || value < 0 || value > 255))) {
    throw new Error("各色を0〜255の整数3個 [R, G, B] で指定してください。");
  }
  if (machine !== "anime" && colours.length !== count) {
    throw new Error(`${machineNames[machine]}のパレットは${count}色です。`);
  }
  custom.set(machine, colours);
  updatePalettes("custom");
  changed();
}

function changeColour(hex) {
  if (!/^#?[0-9a-f]{6}$/i.test(hex)) {
    status("色を #RRGGBB で指定してください。", true);
    return;
  }
  const { colours } = selectedPalette();
  const next = [...colours];
  next[colourIndex] = hex.replace("#", "").match(/../g).map(value => parseInt(value, 16));
  if (machine === "pc9801") {
    while (next.length < 16) next.push(next.at(-1));
  }
  installCustom(next);
}

function showSettings(values) {
  machine = values.machine;
  colourIndex = 0;
  const colours = values.retro_palette || values.anime_palette;
  if (values.palette_id === "custom" && colours) custom.set(machine, colours);
  updatePalettes(values.palette_id);
  for (const input of document.querySelectorAll("[data-setting]")) {
    const value = values[input.dataset.setting];
    if (input.type === "checkbox") input.checked = value;
    else input.value = value;
  }
  for (const button of document.querySelectorAll("[data-preset]")) {
    button.setAttribute("aria-pressed", String(button.dataset.preset === machine));
  }
  get("retro-settings").hidden = machine === "anime";
  get("anime-settings").hidden = machine !== "anime";
}

function readSettings() {
  const { id, colours } = selectedPalette();
  const values = { machine, palette_id: id, anime_palette: null, retro_palette: null };
  for (const input of document.querySelectorAll("[data-setting]")) {
    if (input.type === "number") {
      if (input.validity.valueMissing || input.validity.rangeOverflow || input.validity.rangeUnderflow ||
          input.validity.badInput || (Number(input.step) === 1 && input.validity.stepMismatch &&
            !["anime_tolerance"].includes(input.dataset.setting))) {
        throw new Error(`${input.parentElement.textContent.trim()}の値を確認してください。`);
      }
      values[input.dataset.setting] = input.valueAsNumber;
    } else {
      values[input.dataset.setting] = input.type === "checkbox" ? input.checked : input.value;
    }
  }
  if (machine === "anime") values.anime_palette = colours;
  else if (id === "custom") values.retro_palette = colours;
  return values;
}

function present() {
  const current = view === "result" ? preview : source;
  get("source").hidden = view !== "source" || !source;
  get("output").hidden = view !== "result" || !preview;
  get("placeholder").hidden = !!current;
  get("show-result").disabled = !preview;
  get("show-source").disabled = !source;
  get("show-result").setAttribute("aria-pressed", String(view === "result"));
  get("show-source").setAttribute("aria-pressed", String(view === "source"));
  get("dimensions").textContent = current ? `${current.width} × ${current.height} px` : "—";
  if (!current) return;
  const frame = get("frame");
  const image = get(view === "result" ? "output" : "source");
  const zoom = get("zoom").value;
  const scale = zoom === "fit" ? Math.min(1, frame.clientWidth / current.width, frame.clientHeight / current.height) : Number(zoom) / 100;
  image.style.width = `${current.width * scale}px`;
  image.style.height = `${current.height * scale}px`;
}

function displayImage(id, metadata, blob) {
  const image = get(id);
  if (image.src) URL.revokeObjectURL(image.src);
  image.src = URL.createObjectURL(blob);
  return metadata;
}

function post(message, bytes) {
  if (!worker) {
    worker = new Worker("./worker.js");
    worker.onmessage = ({ data }) => {
      if (request?.id !== data.id) return;
      const pending = request;
      request = null;
      if (data.error) pending.reject(new Error(data.error));
      else pending.resolve(data);
    };
    worker.onerror = () => stopWorker(new Error("変換エンジンが停止しました。画像を縮小して再試行できます。"));
  }
  return new Promise((resolve, reject) => {
    request = { id: ++requestId, resolve, reject };
    worker.postMessage({ id: request.id, ...message, bytes }, bytes ? [bytes] : []);
  });
}

function stopWorker(error) {
  worker?.terminate();
  worker = workerFile = null;
  request?.reject(error);
  request = null;
}

async function openInWorker(file, token, expected) {
  const bytes = await file.arrayBuffer();
  if (token.cancelled) throw new Error("処理を中止しました。");
  const { result } = await post({ action: "open", name: file.name, original: expected }, bytes);
  workerFile = file;
  return result;
}

async function run(message, operation) {
  if (active) return;
  const token = { cancelled: false };
  active = token;
  updateControls();
  status(message);
  try {
    await operation(token);
  } catch (error) {
    status(token.cancelled ? "処理を中止しました。" : error.message, !token.cancelled);
  } finally {
    active = null;
    updateControls();
    if (rerender && get("auto-preview").checked) schedulePreview();
  }
}

function openFile(file, expected = null) {
  if (!file || !catalog) return;
  run(worker ? "画像を読み込み中…" : "変換エンジンを読み込み中…", async token => {
    const result = await openInWorker(file, token, expected);
    if (result.settings) {
      showSettings(result.settings);
      get("save-mask").checked = !!result.export.alpha_mask;
      get("invert-mask").checked = !!result.export.invert_alpha_mask;
    }
    original = !expected && result.original?.name && result.original?.sha256 ? result.original : null;
    get("original-label").hidden = !original;
    source = displayImage("source", { ...result, file }, file);
    preview = null;
    get("file-meta").textContent = `${file.name} · ${result.width} × ${result.height} px`;
    view = "source";
    present();
    revision++;
    rerender = true;
    status(result.settings ? "PNGの変換設定を復元しました。" : "画像を読み込みました。");
  });
}

function changed() {
  revision++;
  if (!source) return;
  rerender = true;
  if (!active) status("設定を変更しました。");
  schedulePreview();
}

function schedulePreview() {
  clearTimeout(timer);
  if (source && !active && get("auto-preview").checked) {
    timer = setTimeout(() => convert(false), 300);
  }
}

function saveFile(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function convert(save) {
  if (!source || active) return;
  clearTimeout(timer);
  let values;
  try {
    values = readSettings();
  } catch (error) {
    status(error.message, true);
    return;
  }
  const current = revision;
  const format = get("format").value;
  const mask = get("save-mask").checked;
  const invert = mask && get("invert-mask").checked;
  const file = source.file;
  const stem = file.name.replace(/\.[^.]+$/, "");
  const palette = selectedPalette().label.trim().replace(/^\d+\s+/, "").replace(/\s+/g, "_");
  const basename = `${stem}_${palette}`;
  rerender = false;
  run(worker ? "変換中…" : "変換エンジンを読み込み中…", async token => {
    if (workerFile !== file) await openInWorker(file, token);
    if (token.cancelled) throw new Error("処理を中止しました。");
    status("変換中…");
    const render = kind => post({ action: "render", kind, values, mask, invert });
    const { result, data } = await render(save && format === "png" ? "png" : "preview");
    const image = new Blob([data], { type: pngType });
    if (current === revision) {
      preview = displayImage("output", result, image);
      view = "result";
      present();
    }
    if (save) {
      let fileData = image;
      if (format !== "png") {
        const { data: bytes } = await render(format);
        fileData = new Blob([bytes], { type: format === "zip" ? "application/zip" : "image/vnd.adobe.photoshop" });
      }
      let maskData;
      if (mask) {
        const { data: bytes } = await render("mask");
        maskData = new Blob([bytes], { type: pngType });
      }
      saveFile(fileData, `${basename}.${format}`);
      if (maskData) saveFile(maskData, `${basename}_alpha_mask.png`);
    }
    status(save ? "ダウンロードを開始しました。" : `変換完了 · ${result.width} × ${result.height} px`);
  });
}

async function init() {
  updateControls();
  const response = await fetch("./catalog.json");
  if (!response.ok) throw new Error("設定データを読み込めません。");
  catalog = await response.json();
  populateNumbers();
  showSettings(catalog.presets.pc8801);
  document.querySelectorAll("[data-preset]").forEach(button => button.addEventListener("click", () => {
    showSettings(catalog.presets[button.dataset.preset]);
    changed();
  }));
  document.querySelectorAll("[data-setting]").forEach(input => input.addEventListener("input", changed));
  get("palette").addEventListener("change", () => {
    colourIndex = 0;
    showColours();
    changed();
  });
  get("swatches").addEventListener("click", event => {
    const button = event.target.closest("[data-index]");
    if (!button) return;
    colourIndex = Number(button.dataset.index);
    showSelectedColour();
  });
  get("colour-picker").addEventListener("change", event => changeColour(event.target.value));
  get("colour-hex").addEventListener("change", event => changeColour(event.target.value));
  get("add-colour").addEventListener("click", () => {
    const { colours } = selectedPalette();
    const colour = colours[colourIndex];
    colourIndex = colours.length;
    installCustom([...colours, colour]);
    get("swatches").lastElementChild.scrollIntoView({ block: "nearest" });
  });
  get("remove-colour").addEventListener("click", () => {
    installCustom(selectedPalette().colours.filter((_, index) => index !== colourIndex));
  });
  get("palette-file").addEventListener("change", async event => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const currentMachine = machine;
    try {
      const palette = JSON.parse(await file.text());
      if (machine === currentMachine) installCustom(palette?.colours ?? palette?.colors ?? palette);
    } catch (error) {
      status(error.message, true);
    }
  });
  get("save-palette").addEventListener("click", () => {
    const { label: name, colours } = selectedPalette();
    const text = JSON.stringify({ name, colours }, null, 2);
    saveFile(new Blob([text], { type: "application/json" }), `Digital8_${machine}.json`);
  });
  for (const id of ["file", "original"]) {
    get(id).addEventListener("change", event => {
      openFile(event.target.files[0], id === "original" ? original : null);
      event.target.value = "";
    });
  }
  const dropzone = get("dropzone");
  dropzone.addEventListener("dragover", event => {
    event.preventDefault();
    dropzone.classList.add("dragging");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
  dropzone.addEventListener("drop", event => {
    event.preventDefault();
    dropzone.classList.remove("dragging");
    openFile(event.dataTransfer.files[0]);
  });
  get("save-mask").addEventListener("change", updateControls);
  get("auto-preview").addEventListener("change", schedulePreview);
  get("preview").addEventListener("click", () => convert(false));
  get("download").addEventListener("click", () => convert(true));
  get("cancel").addEventListener("click", () => {
    if (!active) return;
    active.cancelled = true;
    rerender = false;
    stopWorker(new Error("処理を中止しました。"));
  });
  get("show-source").addEventListener("click", () => { view = "source"; present(); });
  get("show-result").addEventListener("click", () => { view = "result"; present(); });
  get("zoom").addEventListener("change", present);
  new ResizeObserver(present).observe(get("frame"));
  updateControls();
  status("画像を選んでください。");
}

init().catch(error => status(error.message, true));
