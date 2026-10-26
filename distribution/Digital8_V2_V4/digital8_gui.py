#!/usr/bin/env python3
"""Single GUI for PC-8801/PC-9801 and the adopted frequent/two-colour methods."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

import digital8_unified as converter

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_FILE_DROP = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAS_FILE_DROP = False


IMAGE_TYPES = (("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*"))
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MACHINE_LABELS = {"PC-8801": "pc8801", "PC-9801": "pc9801"}
FREQUENT_LABELS = {
    "pc8801": {
        "通常4×2": "normal-42", "通常4×4": "normal-44",
        "通常Mix": "normal-mix", "グラデーション": "gradient",
    },
    "pc9801": {
        "通常セル": "cell", "頻出Mix": "reference", "グラデーション": "gradient",
    },
}
HALF_LABELS = {"市松 AB/BA": "checker", "縦縞 AB": "vertical"}
QUARTER_LABELS = {"交互 AABA/BAAA": "quarter_staggered", "片側 AB/AA": "quarter_single"}
ALPHA_LABELS = {"そのまま保持": "preserve", "二値化": "binary"}


def _label_for(mapping: dict[str, str], value: str) -> str:
    return next(label for label, key in mapping.items() if key == value)


def _palette_filename_suffix(palette: converter.PaletteSpec) -> str:
    parts = palette.label.strip().split()
    return "_".join(parts[1:] if parts[0].isdigit() else parts)


def _palette_output_path(path: Path, palette_name: str, previous_name: str) -> Path:
    stem = path.stem
    if stem.endswith(f"_{palette_name}"):
        return path
    if previous_name and stem.endswith(f"_{previous_name}"):
        stem = stem[:-(len(previous_name) + 1)]
    return path.with_name(f"{stem}_{palette_name}{path.suffix or '.png'}")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master = master
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.machine_label = tk.StringVar(value="PC-8801")
        self.palette_label = tk.StringVar()
        self.tall_dots = tk.BooleanVar(value=True)
        self.resize_640 = tk.BooleanVar(value=False)
        self.pixel_size = tk.IntVar(value=1)
        self.line_threshold = tk.IntVar(value=72)
        self.edge_strength = tk.DoubleVar(value=0.0)
        self.brightness = tk.DoubleVar(value=1.0)
        self.saturation = tk.DoubleVar(value=1.0)
        self.red_gain = tk.DoubleVar(value=1.0)
        self.green_gain = tk.DoubleVar(value=1.0)
        self.blue_gain = tk.DoubleVar(value=1.0)
        self.contrast = tk.DoubleVar(value=1.0)
        self.alpha_label = tk.StringVar(value="そのまま保持")
        self.alpha_threshold = tk.IntVar(value=128)
        self.export_alpha_mask = tk.BooleanVar(value=True)
        self.invert_alpha_mask = tk.BooleanVar(value=False)
        self.frequent_label = tk.StringVar()
        self.colour_coherence = tk.IntVar(value=12)
        self.fill_stability = tk.DoubleVar(value=0.0)
        self.half_label = tk.StringVar(value="市松 AB/BA")
        self.quarter_label = tk.StringVar(value="交互 AABA/BAAA")
        self.colour_groups = tk.IntVar(value=48)
        self.status = tk.StringVar(value="画像を選んでね。")
        self.size_label = tk.StringVar(value="変換対象: 未選択")
        self.source_image: Image.Image | None = None
        self.source_photo: ImageTk.PhotoImage | None = None
        self.result_photo: ImageTk.PhotoImage | None = None
        self.palette_map: dict[str, converter.PaletteSpec] = {}
        self._output_palette_name = ""
        self._source_after: str | None = None
        self._build()
        self.apply_preset(converter.pc8801_standard())
        for variable in (
            self.brightness, self.saturation, self.red_gain, self.green_gain,
            self.blue_gain, self.contrast, self.resize_640, self.tall_dots,
        ):
            variable.trace_add("write", lambda *_args: self.schedule_source_preview())

    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="入力画像").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.input_path, width=85).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="選択…", command=self.pick_input).grid(row=0, column=2)
        ttk.Label(self, text="出力PNG").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(self, textvariable=self.output_path).grid(row=1, column=1, sticky="ew", padx=6, pady=(5, 0))
        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=2, pady=(5, 0))
        ttk.Button(buttons, text="保存先…", command=self.pick_output).pack(fill="x")
        ttk.Button(buttons, text="出力先を開く", command=self.open_output_folder).pack(fill="x", pady=(2, 0))

        presets = ttk.Frame(self)
        presets.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        ttk.Label(presets, text="標準プリセット").pack(side="left")
        ttk.Button(presets, text="PC-8801標準", command=lambda: self.apply_preset(
            converter.pc8801_standard())).pack(side="left", padx=(8, 4))
        ttk.Button(presets, text="PC-9801標準", command=lambda: self.apply_preset(
            converter.pc9801_standard())).pack(side="left")

        platform = ttk.LabelFrame(self, text="機種・パレット・ドット形状", padding=8)
        platform.grid(row=3, column=0, columnspan=3, sticky="ew")
        ttk.Label(platform, text="機種").grid(row=0, column=0, sticky="w")
        machine_picker = ttk.Combobox(platform, textvariable=self.machine_label,
                                      values=tuple(MACHINE_LABELS), state="readonly", width=14)
        machine_picker.grid(row=0, column=1, sticky="w", padx=6)
        machine_picker.bind("<<ComboboxSelected>>", self.change_machine)
        ttk.Label(platform, text="固定パレット").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.palette_picker = ttk.Combobox(platform, textvariable=self.palette_label,
                                           state="readonly", width=20)
        self.palette_picker.grid(row=0, column=3, sticky="w", padx=6)
        self.palette_picker.bind("<<ComboboxSelected>>", self.update_palette_swatch)
        self.palette_canvas = tk.Canvas(platform, width=360, height=30, background="#d9d9d9",
                                        highlightthickness=1, highlightbackground="#777777")
        self.palette_canvas.grid(row=0, column=4, padx=(12, 0))
        ttk.Checkbutton(platform, text="縦長ドット（論理行を半分にして各行を2回描画）",
                        variable=self.tall_dots).grid(row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))
        ttk.Checkbutton(platform, text="横幅640pxへ調整（縦横比を維持）",
                        variable=self.resize_640).grid(row=1, column=4, sticky="w", pady=(7, 0))

        common = ttk.LabelFrame(self, text="共通設定（色補正は1.00＝無補正）", padding=8)
        common.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(7, 4))
        self._spin(common, 0, 0, "仮想ピクセル", self.pixel_size, 1, 8, 1)
        self._spin(common, 1, 0, "主線しきい値", self.line_threshold, 0, 120, 1)
        self._spin(common, 2, 0, "輪郭抽出", self.edge_strength, 0, 1, 0.05)
        self._spin(common, 0, 2, "赤 (R)", self.red_gain, 0.5, 1.5, 0.05)
        self._spin(common, 1, 2, "緑 (G)", self.green_gain, 0.5, 1.5, 0.05)
        self._spin(common, 2, 2, "青 (B)", self.blue_gain, 0.5, 1.5, 0.05)
        self._spin(common, 0, 4, "明度", self.brightness, 0.5, 1.5, 0.05)
        self._spin(common, 1, 4, "彩度", self.saturation, 0, 2, 0.05)
        self._spin(common, 2, 4, "コントラスト", self.contrast, 0.5, 1.5, 0.05)
        ttk.Label(common, text="透明度").grid(row=3, column=0, sticky="w", pady=(7, 0))
        ttk.Combobox(common, textvariable=self.alpha_label, values=tuple(ALPHA_LABELS),
                     state="readonly", width=16).grid(row=3, column=1, sticky="w", padx=5, pady=(7, 0))
        self._spin(common, 3, 2, "二値化しきい値", self.alpha_threshold, 0, 255, 1)
        ttk.Checkbutton(common, text="透明マスクも保存", variable=self.export_alpha_mask).grid(
            row=3, column=4, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Checkbutton(common, text="マスクを反転", variable=self.invert_alpha_mask).grid(
            row=3, column=6, sticky="w", pady=(7, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(5, 4))
        frequent = ttk.Frame(self.notebook, padding=9)
        two_colour = ttk.Frame(self.notebook, padding=9)
        self.notebook.add(frequent, text="頻出パターン式")
        self.notebook.add(two_colour, text="2色パターン式")
        ttk.Label(frequent, text="パターン").grid(row=0, column=0, sticky="w")
        self.frequent_picker = ttk.Combobox(frequent, textvariable=self.frequent_label,
                                            state="readonly", width=22)
        self.frequent_picker.grid(row=0, column=1, sticky="w", padx=6)
        self._spin(frequent, 0, 2, "色のまとまり幅", self.colour_coherence, 1, 32, 1)
        self.stability_label = ttk.Label(frequent, text="塗り判定の安定化")
        self.stability_label.grid(row=0, column=4, sticky="w", padx=(10, 0))
        self.stability_spin = ttk.Spinbox(frequent, from_=0, to=1, increment=0.05,
                                          textvariable=self.fill_stability, width=6)
        self.stability_spin.grid(row=0, column=5, sticky="w", padx=5)
        ttk.Label(frequent, text="V2の近いRGBを揃える値。9801だけ安定化を追加できます。").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))
        ttk.Label(two_colour, text="1:1 混色").grid(row=0, column=0, sticky="w")
        ttk.Combobox(two_colour, textvariable=self.half_label, values=tuple(HALF_LABELS),
                     state="readonly", width=21).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(two_colour, text="3:1 混色").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Combobox(two_colour, textvariable=self.quarter_label, values=tuple(QUARTER_LABELS),
                     state="readonly", width=22).grid(row=0, column=3, sticky="w", padx=6)
        self._spin(two_colour, 0, 4, "近い色をまとめる数", self.colour_groups, 2, 64, 1)
        ttk.Label(two_colour, text="少ないほど同じタイルを広い範囲で維持します。").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))

        action = ttk.Frame(self)
        action.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 5))
        action.columnconfigure((0, 1), weight=1)
        self.save_button = ttk.Button(action, text="PNGへ変換・保存", command=self.start_save)
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.preview_button = ttk.Button(action, text="プレビュー（保存しない・実寸）", command=self.start_preview)
        self.preview_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        previews = ttk.Frame(self)
        previews.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(7, weight=1)
        previews.columnconfigure((0, 1), weight=1)
        previews.rowconfigure(0, weight=1)
        self.source_label = ttk.Label(previews, text="入力画像", anchor="center")
        self.result_label = ttk.Label(previews, text="変換結果", anchor="center")
        self.source_label.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.result_label.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Label(previews, textvariable=self.size_label).grid(row=1, column=0, columnspan=2)
        ttk.Label(self, textvariable=self.status).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        if HAS_FILE_DROP:
            for target in (self, self.source_label):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self.drop_input)

    def _spin(self, parent: ttk.Frame, row: int, column: int, label: str,
              variable: tk.Variable, lower: float, upper: float, step: float) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(8 if column else 0, 0), pady=2)
        ttk.Spinbox(parent, from_=lower, to=upper, increment=step,
                    textvariable=variable, width=7).grid(row=row, column=column + 1, sticky="w", padx=5, pady=2)

    def apply_preset(self, settings: converter.ConversionOptions) -> None:
        self.machine_label.set(_label_for(MACHINE_LABELS, settings.machine))
        self._set_machine_choices(settings.machine, settings.palette_id, settings.frequent_mode)
        for variable, value in (
            (self.tall_dots, settings.tall_dots), (self.pixel_size, settings.pixel_size),
            (self.line_threshold, settings.line_threshold), (self.edge_strength, settings.edge_strength),
            (self.resize_640, settings.resize_640), (self.brightness, settings.brightness),
            (self.saturation, settings.saturation), (self.red_gain, settings.red_gain),
            (self.green_gain, settings.green_gain), (self.blue_gain, settings.blue_gain),
            (self.contrast, settings.contrast), (self.alpha_threshold, settings.alpha_threshold),
            (self.colour_coherence, settings.colour_coherence), (self.fill_stability, settings.fill_stability),
            (self.colour_groups, settings.colour_groups),
        ):
            variable.set(value)
        self.alpha_label.set(_label_for(ALPHA_LABELS, settings.alpha_mode))
        self.half_label.set(_label_for(HALF_LABELS, settings.half_tone))
        self.quarter_label.set(_label_for(QUARTER_LABELS, settings.quarter_tone))
        self.notebook.select(0 if settings.method == "frequent" else 1)
        self.update_palette_swatch()
        self.refresh_source_preview()
        self.status.set(f"{self.machine_label.get()}標準設定を選んだよ。")

    def change_machine(self, _event: tk.Event | None = None) -> None:
        machine = MACHINE_LABELS[self.machine_label.get()]
        self.apply_preset(converter.pc8801_standard() if machine == "pc8801" else converter.pc9801_standard())

    def _set_machine_choices(self, machine: str, palette_id: str, frequent_mode: str) -> None:
        self.palette_map = {item.label: item for item in converter.palettes_for(machine)}
        self.palette_picker.configure(values=tuple(self.palette_map))
        self.palette_label.set(next(label for label, item in self.palette_map.items() if item.id == palette_id))
        modes = FREQUENT_LABELS[machine]
        self.frequent_picker.configure(values=tuple(modes))
        self.frequent_label.set(_label_for(modes, frequent_mode))
        visible = machine == "pc9801"
        for widget in (self.stability_label, self.stability_spin):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def update_palette_swatch(self, _event: tk.Event | None = None) -> None:
        palette = self.palette_map.get(self.palette_label.get())
        if palette is None:
            return
        current_output = self.output_path.get().strip()
        if current_output:
            palette_name = _palette_filename_suffix(palette)
            updated = _palette_output_path(Path(current_output), palette_name,
                                           self._output_palette_name)
            self.output_path.set(str(updated))
            self._output_palette_name = palette_name
        self.palette_canvas.delete("all")
        swatch = 22
        self.palette_canvas.configure(width=max(360, len(palette.colours) * swatch + 4))
        for index, (red, green, blue) in enumerate(palette.colours):
            x = 2 + index * swatch
            self.palette_canvas.create_rectangle(x, 3, x + swatch - 1, 27,
                                                  fill=f"#{red:02x}{green:02x}{blue:02x}", outline="#555555")

    def pick_input(self) -> None:
        filename = filedialog.askopenfilename(filetypes=IMAGE_TYPES)
        if filename:
            self.set_input(Path(filename))

    def drop_input(self, event: tk.Event) -> None:
        files = self.master.tk.splitlist(event.data)
        if files:
            self.set_input(Path(files[0]))

    def set_input(self, path: Path) -> None:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            messagebox.showerror("入力エラー", "対応する画像ファイルを指定して。")
            return
        self.input_path.set(str(path))
        palette = self.palette_map[self.palette_label.get()]
        self._output_palette_name = _palette_filename_suffix(palette)
        self.output_path.set(str(path.with_name(f"{path.stem}_{self._output_palette_name}.png")))
        with Image.open(path) as source:
            self.source_image = source.convert("RGB")
        self.refresh_source_preview()

    def pick_output(self) -> None:
        filename = filedialog.asksaveasfilename(defaultextension=".png", filetypes=(("PNG", "*.png"),))
        if filename:
            self.output_path.set(filename)

    def open_output_folder(self) -> None:
        text = self.output_path.get().strip()
        folder = Path(text).parent if text else Path.cwd()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def schedule_source_preview(self) -> None:
        if self._source_after is not None:
            self.master.after_cancel(self._source_after)
        self._source_after = self.master.after(100, self.refresh_source_preview)

    def refresh_source_preview(self) -> None:
        self._source_after = None
        if self.source_image is None:
            return
        image = converter._resize_source(self.source_image.copy(), self.resize_640.get())
        logical_height = (image.height + 1) // 2 if self.tall_dots.get() else image.height
        self.size_label.set(f"論理: {image.width}×{logical_height}px  →  出力: {image.width}×{image.height}px")
        image.thumbnail((480, 310), Image.Resampling.LANCZOS)
        adjusted = converter.pc98_base.adjust_colours(
            np.asarray(image, dtype=np.uint8), self.brightness.get(), self.saturation.get(),
            self.red_gain.get(), self.green_gain.get(), self.blue_gain.get(), self.contrast.get(),
        )
        self.source_photo = ImageTk.PhotoImage(Image.fromarray(adjusted, "RGB"))
        self.source_label.configure(image=self.source_photo, text="")

    def _settings(self) -> converter.ConversionOptions:
        machine = MACHINE_LABELS[self.machine_label.get()]
        method = "frequent" if self.notebook.index(self.notebook.select()) == 0 else "two_colour"
        palette = self.palette_map[self.palette_label.get()]
        if palette.machine != machine:
            raise ValueError("選択したパレットが機種と一致しない。")
        return converter.ConversionOptions(
            machine=machine, method=method, palette_id=palette.id,
            tall_dots=self.tall_dots.get(), pixel_size=self.pixel_size.get(),
            line_threshold=self.line_threshold.get(), edge_strength=self.edge_strength.get(),
            brightness=self.brightness.get(), saturation=self.saturation.get(),
            red_gain=self.red_gain.get(), green_gain=self.green_gain.get(),
            blue_gain=self.blue_gain.get(), contrast=self.contrast.get(),
            resize_640=self.resize_640.get(), alpha_mode=ALPHA_LABELS[self.alpha_label.get()],
            alpha_threshold=self.alpha_threshold.get(),
            frequent_mode=FREQUENT_LABELS[machine][self.frequent_label.get()],
            colour_coherence=self.colour_coherence.get(), fill_stability=self.fill_stability.get(),
            half_tone=HALF_LABELS[self.half_label.get()],
            quarter_tone=QUARTER_LABELS[self.quarter_label.get()], colour_groups=self.colour_groups.get(),
        )

    def start_preview(self) -> None:
        self._start(save=False)

    def start_save(self) -> None:
        self._start(save=True)

    def _start(self, save: bool) -> None:
        source = Path(self.input_path.get())
        destination = Path(self.output_path.get())
        if not source.is_file() or (save and not destination.name):
            messagebox.showerror("入力不足", "入力画像と出力先を指定して。")
            return
        try:
            settings = self._settings()
        except (tk.TclError, KeyError, StopIteration, ValueError) as error:
            messagebox.showerror("設定エラー", str(error))
            return
        alpha_mask = (
            destination.with_name(f"{destination.stem}_alpha_mask.png")
            if save and self.export_alpha_mask.get() else None
        )
        existing = [path for path in (destination, alpha_mask)
                    if path is not None and path.exists()] if save else []
        if existing and not messagebox.askyesno(
            "上書き確認",
            "次のファイルは既に存在します。上書きする？\n\n" + "\n".join(str(path) for path in existing),
        ):
            return
        self.save_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.status.set("変換中…")
        threading.Thread(target=self._worker, args=(source, destination, settings, save, alpha_mask,
                                                    self.invert_alpha_mask.get()), daemon=True).start()

    def _worker(self, source: Path, destination: Path, settings: converter.ConversionOptions,
                save: bool, alpha_mask: Path | None, invert: bool) -> None:
        try:
            if save:
                converter.convert(source, destination, settings, alpha_mask, invert)
                with Image.open(destination) as saved:
                    result = saved.copy()
            else:
                result, _alpha = converter.convert_image(source, settings)
        except Exception as error:
            self.master.after(0, lambda error=error: self._finished(None, error, save, destination))
        else:
            self.master.after(0, lambda: self._finished(result, None, save, destination))

    def _finished(self, result: Image.Image | None, error: Exception | None,
                  saved: bool, destination: Path) -> None:
        self.save_button.configure(state="normal")
        self.preview_button.configure(state="normal")
        if error is not None:
            self.status.set("変換に失敗した。")
            messagebox.showerror("変換エラー", str(error))
            return
        assert result is not None
        thumbnail = result.convert("RGB")
        thumbnail.thumbnail((480, 310), Image.Resampling.NEAREST)
        self.result_photo = ImageTk.PhotoImage(thumbnail)
        self.result_label.configure(image=self.result_photo, text="")
        if saved:
            self.status.set(f"保存完了: {destination}")
        else:
            self.status.set(f"プレビュー: {result.width}×{result.height}px（未保存）")
            self._show_full_preview(result)

    def _show_full_preview(self, image: Image.Image) -> None:
        window = tk.Toplevel(self.master)
        window.title(f"実寸プレビュー 1:1  |  {image.width}×{image.height}px  |  未保存")
        window.geometry(f"{min(image.width + 40, 1100)}x{min(image.height + 70, 800)}")
        frame = ttk.Frame(window)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, background="#777777", highlightthickness=0)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        photo = ImageTk.PhotoImage(image.convert("RGBA"), master=window)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.configure(scrollregion=(0, 0, image.width, image.height))
        window.preview_photo = photo


def main() -> None:
    root = TkinterDnD.Tk() if HAS_FILE_DROP else tk.Tk()
    root.title("Digital 8 / PC-9801 統合コンバーター")
    root.minsize(1040, 820)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
