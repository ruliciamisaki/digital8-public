#!/usr/bin/env python3
"""Digital8 Anime V4 desktop application."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from queue import Empty, SimpleQueue
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

import digital8_converter as converter
import png_settings
from palette_editor import PaletteEditor
from display_support import enable_native_pixels
from layer_export import PSD_EXTRA_GROUPS
from preview_window import PreviewWindow

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_FILE_DROP = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAS_FILE_DROP = False


IMAGE_TYPES = (("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*"))
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MACHINE_LABELS = {"PC-8801": "pc8801", "PC-9801": "pc9801", "アニメ塗り": "anime"}
HALF_LABELS = {"市松 AB/BA": "checker", "縦縞 AB": "vertical"}
QUARTER_LABELS = {"交互 AABA/BAAA": "quarter_staggered", "片側 AB/AA": "quarter_single"}
ALPHA_LABELS = {"そのまま保持": "preserve", "二値化": "binary"}
EIGHTH_LABELS={'従来 4×2':'eighth','交互 4×4':'eighth_staggered'}
THREE_112_LABELS={'使わない':'none','縞 4×1':'three_112_stripes','交互 4×2':'three_112_alternate','斜め 4×4':'three_112_diagonal'}
THREE_111_LABELS={'使わない':'none','縞 3×1':'three_111_stripes','斜め 3×3':'three_111_diagonal'}


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


def _checker_preview(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    yy, xx = np.indices((rgba.height, rgba.width))
    values = np.where(((xx // 10 + yy // 10) % 2) == 0, 208, 240).astype(np.uint8)
    background = Image.fromarray(np.repeat(values[:, :, None], 3, axis=2), "RGB")
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


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
        self.smooth_2x = tk.BooleanVar(value=False)
        self.eighth_label=tk.StringVar(value='従来 4×2')
        self.three_112_label=tk.StringVar(value='使わない')
        self.three_111_label=tk.StringVar(value='使わない')
        self.four_colour = tk.BooleanVar(value=False)
        self.five_colour = tk.BooleanVar(value=False)
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
        self.half_label = tk.StringVar(value="市松 AB/BA")
        self.quarter_label = tk.StringVar(value="交互 AABA/BAAA")
        self.colour_groups = tk.IntVar(value=48)
        self.anime_tolerance = tk.DoubleVar(value=128)
        self.anime_radius = tk.IntVar(value=3)
        self.anime_min_area = tk.IntVar(value=12)
        self.anime_max_colours = tk.IntVar(value=24)
        self.line_growth = tk.DoubleVar(value=0)
        self.colour_trace = tk.BooleanVar(value=False)
        self.trace_strength = tk.DoubleVar(value=.55)
        self.white_transparent = tk.BooleanVar(value=False)
        self.output_format = tk.StringVar(value="PNG")
        self.psd_extra_layers = {mode: tk.BooleanVar(value=False) for mode in PSD_EXTRA_GROUPS}
        self.auto_preview = tk.BooleanVar(value=False)
        self.anime_colours = converter.anime_paint.default_palette()
        self.retro_colours = {}
        for machine, count in (("pc8801", 8), ("pc9801", 16)):
            colours = converter.palettes_for(machine)[0].colours
            self.retro_colours[machine] = colours + (colours[-1],) * (count - len(colours))
        self.status = tk.StringVar(value="画像を選んでね。")
        self.size_label = tk.StringVar(value="変換対象: 未選択")
        self.source_image: Image.Image | None = None
        self.source_photo: ImageTk.PhotoImage | None = None
        self.result_photo: ImageTk.PhotoImage | None = None
        self.palette_map: dict[str, converter.PaletteSpec] = {}
        self._output_palette_name = ""
        self._source_after: str | None = None
        self._preview_window: PreviewWindow | None = None
        self._preview_after: str | None = None
        self._preview_due = False
        self._preview_revision = 0
        self._busy = False
        self._job_after: str | None = None
        self._results = SimpleQueue()
        self._build()
        self.apply_preset(converter.anime_standard())
        source_controls = (
            self.brightness, self.saturation, self.red_gain, self.green_gain,
            self.blue_gain, self.contrast, self.resize_640, self.tall_dots, self.smooth_2x,
        )
        for variable in source_controls:
            variable.trace_add("write", lambda *_args: self.schedule_source_preview())
        for variable in source_controls + (
            self.input_path, self.machine_label, self.palette_label,
            self.half_label, self.quarter_label, self.eighth_label, self.three_112_label,
            self.three_111_label, self.four_colour, self.five_colour, self.pixel_size, self.line_threshold, self.edge_strength,
            self.alpha_label, self.alpha_threshold, self.colour_groups, self.anime_tolerance,
            self.anime_radius, self.anime_min_area, self.anime_max_colours,
            self.line_growth, self.colour_trace, self.trace_strength, self.white_transparent,
        ):
            variable.trace_add("write", self._schedule_preview)
        self.auto_preview.trace_add("write", self._schedule_preview)
        self.output_format.trace_add("write", self._update_psd_controls)
        self.output_path.trace_add("write", self._update_psd_controls)

    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="入力画像").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.input_path, width=85).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="選択…", command=self.pick_input).grid(row=0, column=2)
        ttk.Label(self, text="出力ファイル").grid(row=1, column=0, sticky="w", pady=(5, 0))
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
        ttk.Button(presets, text="アニメ塗り標準", command=lambda: self.apply_preset(
            converter.anime_standard())).pack(side="left", padx=4)

        platform = ttk.LabelFrame(self, text="機種・パレット・ドット形状", padding=8)
        platform.grid(row=3, column=0, columnspan=3, sticky="ew")
        ttk.Label(platform, text="機種").grid(row=0, column=0, sticky="w")
        machine_picker = ttk.Combobox(platform, textvariable=self.machine_label,
                                      values=tuple(MACHINE_LABELS), state="readonly", width=14)
        machine_picker.grid(row=0, column=1, sticky="w", padx=6)
        machine_picker.bind("<<ComboboxSelected>>", self.change_machine)
        ttk.Label(platform, text="パレット").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.palette_picker = ttk.Combobox(platform, textvariable=self.palette_label,
                                           state="readonly", width=20)
        self.palette_picker.grid(row=0, column=3, sticky="w", padx=6)
        self.palette_picker.bind("<<ComboboxSelected>>", self.update_palette_swatch)
        self.palette_canvas = tk.Canvas(platform, width=360, height=30, background="#d9d9d9",
                                        highlightthickness=1, highlightbackground="#777777")
        self.palette_canvas.grid(row=0, column=4, padx=(12, 0))
        self.edit_palette_button = ttk.Button(platform, text="パレット編集…", command=self.edit_palette)
        self.edit_palette_button.grid(row=0, column=5, padx=5)
        ttk.Checkbutton(platform, text="縦長ドット（論理行を半分にして各行を2回描画）",
                        variable=self.tall_dots).grid(row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))
        ttk.Checkbutton(platform, text="横幅640pxへ調整（縦横比を維持）",
                        variable=self.resize_640).grid(row=1, column=4, sticky="w", pady=(7, 0))
        ttk.Label(platform, text="出力形式").grid(row=2, column=0, sticky="w", pady=(6, 0))
        format_picker = ttk.Combobox(platform, textvariable=self.output_format,
                                    values=("PNG", "PSD", "ZIP"), state="readonly", width=14)
        format_picker.grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        format_picker.bind("<<ComboboxSelected>>", self.change_output_format)
        ttk.Label(platform, text="PSDの追加レイヤー").grid(row=2, column=2, sticky="w", pady=(6, 0))
        self.psd_layer_checkboxes = []
        for column, (mode, label) in enumerate(PSD_EXTRA_GROUPS.items(), start=3):
            checkbox = ttk.Checkbutton(platform, text=label, variable=self.psd_extra_layers[mode], state="disabled")
            checkbox.grid(row=2, column=column, sticky="w", padx=6, pady=(6, 0))
            self.psd_layer_checkboxes.append(checkbox)
        ttk.Checkbutton(platform, text="2倍拡大・1/2縮小（LANCZOS）", variable=self.smooth_2x).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(platform, text="640px ON：変換後に適用 / OFF：2倍で変換して縮小").grid(
            row=3, column=4, columnspan=2, sticky="w", pady=(6, 0))
        common = ttk.LabelFrame(self, text="共通設定（色補正は1.00＝無補正）", padding=8)
        common.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(7, 4))
        self._spin(common, 0, 0, "仮想ピクセル", self.pixel_size, 1, 64, 1)
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
        self._spin(common, 4, 0, "線を太くする(px)", self.line_growth, 0, 16, .25)
        ttk.Checkbutton(common, text="色トレス", variable=self.colour_trace).grid(row=4, column=2, columnspan=2, sticky="w")
        self._spin(common, 4, 4, "色トレス明度", self.trace_strength, .1, .95, .05)
        self._spin(common, 5, 0, "面をまとめる色差", self.anime_tolerance, 0, 128, 1)
        self._spin(common, 5, 2, "周囲をならす半径", self.anime_radius, 0, 8, 1)
        self._spin(common, 5, 4, "小さい面の整理(px)", self.anime_min_area, 0, 4096, 1)
        ttk.Checkbutton(common, text="白(#FFFFFF)を透明にする", variable=self.white_transparent).grid(
            row=5, column=6, sticky="w")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(5, 4))
        two_colour = ttk.Frame(self.notebook, padding=9)
        self.two_colour_frame = two_colour
        self.anime_frame = ttk.Frame(self.notebook, padding=9)
        self.notebook.add(two_colour, text="V4 タイルパターン式")
        ttk.Label(two_colour, text="1:1 混色").grid(row=0, column=0, sticky="w")
        ttk.Combobox(two_colour, textvariable=self.half_label, values=tuple(HALF_LABELS),
                     state="readonly", width=21).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(two_colour, text="3:1 混色").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Combobox(two_colour, textvariable=self.quarter_label, values=tuple(QUARTER_LABELS),
                     state="readonly", width=22).grid(row=0, column=3, sticky="w", padx=6)
        self._spin(two_colour, 0, 4, "共通色区分数", self.colour_groups, 2, 64, 1)
        ttk.Label(two_colour, text="少ないほど同じタイルを広い範囲で維持します。").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))
        for row,column,title,variable,choices in (
            (2,0,'7:1 混色',self.eighth_label,EIGHTH_LABELS),
            (2,2,'1:1:2 混色',self.three_112_label,THREE_112_LABELS),
            (3,0,'1:1:1 混色',self.three_111_label,THREE_111_LABELS)):
            ttk.Label(two_colour,text=title).grid(row=row,column=column,sticky='w',pady=(5,0))
            ttk.Combobox(two_colour,textvariable=variable,values=tuple(choices),state='readonly',width=22).grid(
                row=row,column=column+1,sticky='w',padx=6,pady=(5,0))
        ttk.Checkbutton(two_colour, text='4色 4×2 を使う', variable=self.four_colour).grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(5, 0))
        ttk.Checkbutton(two_colour, text='5色 4×4 を使う', variable=self.five_colour).grid(
            row=4, column=2, columnspan=2, sticky='w', pady=(5, 0))
        self.notebook.add(self.anime_frame, text="アニメ塗り（ベタ塗り）")
        self._spin(self.anime_frame, 0, 0, "使用色数(0=全色)", self.anime_max_colours, 0, 256, 1)
        ttk.Label(self.anime_frame, text="全画面共通の色区分で塗り分けます。").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))

        action = ttk.Frame(self)
        action.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 5))
        action.columnconfigure((0, 1), weight=1)
        self.save_button = ttk.Button(action, text="変換・保存", command=self.start_save)
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.preview_button = ttk.Button(action, text="プレビュー更新", command=self.start_preview)
        self.preview_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Checkbutton(action, text="プレビューを自動更新する", variable=self.auto_preview).grid(
            row=0, column=2, padx=(10, 0), sticky="w")
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
        if settings.machine != "anime" and settings.retro_palette is not None:
            colours = converter.validate_retro_palette(settings.machine, settings.retro_palette)
            self.retro_colours[settings.machine] = colours
            settings = replace(settings, palette_id="custom")
        if settings.machine == "anime" and settings.palette_id == "custom" and settings.anime_palette is not None:
            self.anime_colours = settings.anime_palette
        self.machine_label.set(_label_for(MACHINE_LABELS, settings.machine))
        self._set_machine_choices(settings.machine, settings.palette_id)
        for variable, value in (
            (self.tall_dots, settings.tall_dots), (self.pixel_size, settings.pixel_size),
            (self.line_threshold, settings.line_threshold), (self.edge_strength, settings.edge_strength),
            (self.resize_640, settings.resize_640), (self.brightness, settings.brightness),
            (self.smooth_2x, settings.smooth_2x),
            (self.saturation, settings.saturation), (self.red_gain, settings.red_gain),
            (self.green_gain, settings.green_gain), (self.blue_gain, settings.blue_gain),
            (self.contrast, settings.contrast), (self.alpha_threshold, settings.alpha_threshold),
            (self.colour_groups, settings.colour_groups),
            (self.anime_tolerance, settings.anime_tolerance), (self.anime_radius, settings.anime_radius),
            (self.anime_min_area, settings.anime_min_area),
            (self.anime_max_colours, settings.anime_max_colours), (self.line_growth, settings.line_growth),
            (self.colour_trace, settings.colour_trace), (self.trace_strength, settings.trace_strength),
            (self.white_transparent, settings.white_transparent),
        ):
            variable.set(value)
        self.alpha_label.set(_label_for(ALPHA_LABELS, settings.alpha_mode))
        self.half_label.set(_label_for(HALF_LABELS, settings.half_tone))
        self.quarter_label.set(_label_for(QUARTER_LABELS, settings.quarter_tone))
        self.eighth_label.set(_label_for(EIGHTH_LABELS,settings.eighth_tone))
        self.three_112_label.set(_label_for(THREE_112_LABELS,settings.three_112))
        self.three_111_label.set(_label_for(THREE_111_LABELS,settings.three_111))
        self.four_colour.set(settings.four_colour)
        self.five_colour.set(settings.five_colour)
        self.notebook.select(self.anime_frame if settings.machine == "anime" else self.two_colour_frame)
        self.update_palette_swatch()
        self.refresh_source_preview()
        self.status.set(f"{self.machine_label.get()}標準設定を選んだよ。")

    def change_machine(self, _event: tk.Event | None = None) -> None:
        machine = MACHINE_LABELS[self.machine_label.get()]
        presets = {"pc8801": converter.pc8801_standard, "pc9801": converter.pc9801_standard,
                   "anime": converter.anime_standard}
        self.apply_preset(presets[machine]())

    def _set_machine_choices(self, machine: str, palette_id: str) -> None:
        self.palette_map = {item.label: item for item in converter.palettes_for(machine)}
        if machine == "anime":
            custom = converter.PaletteSpec("anime", "custom", "02  Custom", self.anime_colours)
        else:
            custom = converter.PaletteSpec(machine, "custom", "Custom（PC88）" if machine == "pc8801"
                                           else "Custom（PC98）", self.retro_colours[machine])
        self.palette_map[custom.label] = custom
        self.palette_picker.configure(values=tuple(self.palette_map))
        self.palette_label.set(next(label for label, item in self.palette_map.items() if item.id == palette_id))
        for frame in (self.two_colour_frame, self.anime_frame):
            self.notebook.tab(frame, state="normal" if (frame == self.anime_frame) == (machine == "anime") else "hidden")

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
        swatch = (6 if len(palette.colours) > 256 else 12) if palette.machine == "anime" else 22
        columns = (60 if len(palette.colours) > 256 else 30) if palette.machine == "anime" else len(palette.colours)
        rows = (len(palette.colours) + columns - 1) // columns
        self.palette_canvas.configure(width=max(360, columns * swatch + 4), height=max(30, rows * swatch + 4))
        for index, (red, green, blue) in enumerate(palette.colours):
            x, y = 2 + (index % columns) * swatch, 2 + (index // columns) * swatch
            self.palette_canvas.create_rectangle(x, y, x + swatch - 1, y + (swatch if rows > 1 else 25),
                                                  fill=f"#{red:02x}{green:02x}{blue:02x}", outline="#555555")

    def edit_palette(self) -> None:
        palette = self.palette_map[self.palette_label.get()]
        if palette.machine == "anime":
            PaletteEditor(self.master, palette.colours, self.set_anime_palette)
        else:
            count = 8 if palette.machine == "pc8801" else 16
            colours = palette.colours + (palette.colours[-1],) * (count - len(palette.colours))
            PaletteEditor(self.master, colours,
                          lambda colours: self.set_retro_palette(palette.machine, colours),
                          fixed_count=count,
                          palette_name="PC-8801" if palette.machine == "pc8801" else "PC-9801")

    def set_retro_palette(self, machine: str, colours: tuple) -> None:
        self.retro_colours[machine] = converter.validate_retro_palette(machine, colours)
        if MACHINE_LABELS[self.machine_label.get()] == machine:
            self._set_machine_choices(machine, "custom")
            self.update_palette_swatch()

    def set_anime_palette(self, colours: tuple) -> None:
        self.anime_colours = converter.anime_paint.validate_palette(colours)
        self.machine_label.set("アニメ塗り")
        self._set_machine_choices("anime", "custom")
        self.notebook.select(self.anime_frame)
        self.update_palette_swatch()

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
        restored = None
        metadata_error = None
        try:
            recipe = png_settings.read_recipe(path)
            if recipe is not None:
                restored = png_settings.decode_settings(recipe)
        except (ValueError, OSError) as error:
            metadata_error = str(error)
        if restored is not None:
            self.apply_preset(restored)
            self.output_format.set("PNG")
            self.export_alpha_mask.set(recipe.get("export", {}).get("alpha_mask", False))
            self.invert_alpha_mask.set(recipe.get("export", {}).get("invert_alpha_mask", False))
            original = png_settings.find_original(path, recipe)
            if original is not None:
                path = original
                restore_message = f"PNGの設定と元画像を復元したよ: {path.name}"
            else:
                restore_message = "PNGの設定を復元したよ。変換済み画像を選択中なので、再変換には元画像を選んでね。"
            if recipe.get("renderer") != png_settings.RENDERER:
                restore_message += "（変換ロジックの版が違うため、結果が変わる場合があります）"
        self.input_path.set(str(path))
        palette = self.palette_map[self.palette_label.get()]
        self._output_palette_name = _palette_filename_suffix(palette)
        self.output_path.set(str(path.with_name(f"{path.stem}_{self._output_palette_name}.{self.output_format.get().lower()}")))
        with Image.open(path) as source:
            self.source_image = source.convert("RGBA")
        self.refresh_source_preview()
        if restored is not None:
            self.status.set(restore_message)
        elif metadata_error is not None:
            self.status.set(f"画像を開いたよ。埋め込み設定は読み込めなかった: {metadata_error}")
        else:
            self.status.set(f"画像を選んだよ: {path.name}")

    def pick_output(self) -> None:
        filename = filedialog.asksaveasfilename(defaultextension="." + self.output_format.get().lower(),
                                               filetypes=(("PNG", "*.png"), ("Layered PSD", "*.psd"), ("Layer PNG ZIP", "*.zip")))
        if filename:
            self.output_path.set(filename)
            extension = Path(filename).suffix.lower().lstrip(".").upper()
            if extension in ("PNG", "PSD", "ZIP"):
                self.output_format.set(extension)

    def change_output_format(self, _event: tk.Event | None = None) -> None:
        current = self.output_path.get().strip()
        if current:
            self.output_path.set(str(Path(current).with_suffix("." + self.output_format.get().lower())))

    def _is_psd_output(self) -> bool:
        path = self.output_path.get().strip()
        return Path(path).suffix.lower() == ".psd" if path else self.output_format.get() == "PSD"

    def _update_psd_controls(self, *_args) -> None:
        state = "!disabled" if self._is_psd_output() else "disabled"
        for checkbox in self.psd_layer_checkboxes:
            checkbox.state([state])

    def open_output_folder(self) -> None:
        text = self.output_path.get().strip()
        folder = Path(text).parent if text else Path.cwd()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def schedule_source_preview(self) -> None:
        if self._source_after is not None:
            self.master.after_cancel(self._source_after)
        self._source_after = self.master.after(100, self._refresh_source_from_timer)

    def _refresh_source_from_timer(self) -> None:
        self._source_after = None
        self.refresh_source_preview()

    def refresh_source_preview(self) -> None:
        if self._source_after is not None:
            self.master.after_cancel(self._source_after)
            self._source_after = None
        if self.source_image is None:
            return
        try:
            colour_controls = (self.brightness.get(), self.saturation.get(), self.red_gain.get(),
                               self.green_gain.get(), self.blue_gain.get(), self.contrast.get())
        except tk.TclError:
            return  # The spinbox can temporarily be empty while typing.
        image = converter._resize_source(self.source_image.copy(), self.resize_640.get())
        factor = 2 if self.smooth_2x.get() and not self.resize_640.get() else 1
        logical_height = (image.height * factor + 1) // 2 if self.tall_dots.get() else image.height * factor
        self.size_label.set(f"論理: {image.width * factor}×{logical_height}px  →  出力: {image.width}×{image.height}px")
        image.thumbnail((480, 310), Image.Resampling.LANCZOS)
        adjusted = converter.image_processing.adjust_colours(
            np.asarray(image.convert("RGB"), dtype=np.uint8), *colour_controls,
        )
        adjusted_image = Image.fromarray(adjusted, "RGB")
        adjusted_image.putalpha(image.getchannel("A"))
        self.source_photo = ImageTk.PhotoImage(_checker_preview(adjusted_image))
        self.source_label.configure(image=self.source_photo, text="")

    def _settings(self) -> converter.ConversionOptions:
        machine = MACHINE_LABELS[self.machine_label.get()]
        palette = self.palette_map[self.palette_label.get()]
        if palette.machine != machine:
            raise ValueError("選択したパレットが機種と一致しない。")
        return converter.ConversionOptions(
            machine=machine, palette_id=palette.id,
            tall_dots=self.tall_dots.get(), pixel_size=self.pixel_size.get(),
            line_threshold=self.line_threshold.get(), edge_strength=self.edge_strength.get(),
            brightness=self.brightness.get(), saturation=self.saturation.get(),
            red_gain=self.red_gain.get(), green_gain=self.green_gain.get(),
            blue_gain=self.blue_gain.get(), contrast=self.contrast.get(),
            resize_640=self.resize_640.get(), alpha_mode=ALPHA_LABELS[self.alpha_label.get()],
            smooth_2x=self.smooth_2x.get(),
            eighth_tone=EIGHTH_LABELS[self.eighth_label.get()],
            three_112=THREE_112_LABELS[self.three_112_label.get()],three_111=THREE_111_LABELS[self.three_111_label.get()],
            four_colour=self.four_colour.get(), five_colour=self.five_colour.get(),
            alpha_threshold=self.alpha_threshold.get(),
            half_tone=HALF_LABELS[self.half_label.get()],
            quarter_tone=QUARTER_LABELS[self.quarter_label.get()], colour_groups=self.colour_groups.get(),
            anime_tolerance=self.anime_tolerance.get(), anime_radius=self.anime_radius.get(),
            anime_min_area=self.anime_min_area.get(), anime_palette=palette.colours if machine == "anime" else None,
            retro_palette=palette.colours if machine != "anime" and palette.id == "custom" else None,
            anime_max_colours=self.anime_max_colours.get(), line_growth=self.line_growth.get(),
            colour_trace=self.colour_trace.get(), trace_strength=self.trace_strength.get(),
            white_transparent=self.white_transparent.get(),
        )

    def start_preview(self) -> None:
        self._start(save=False)

    def start_save(self) -> None:
        self._start(save=True)

    def _clear_preview_request(self) -> None:
        if self._preview_after is not None:
            self.master.after_cancel(self._preview_after)
            self._preview_after = None
        self._preview_due = False

    def _schedule_preview(self, *_args) -> None:
        self._preview_revision += 1
        self._clear_preview_request()
        if self.auto_preview.get():
            self._preview_after = self.master.after(300, self._auto_preview_ready)

    def _auto_preview_ready(self) -> None:
        self._preview_after = None
        if self._busy:
            self._preview_due = True
        else:
            self._start(save=False, automatic=True)

    def _start(self, save: bool, automatic: bool = False) -> None:
        if self._busy:
            return
        self._clear_preview_request()
        source = Path(self.input_path.get())
        destination = Path(self.output_path.get())
        if not source.is_file() or (save and not destination.name):
            if not automatic:
                messagebox.showerror("入力不足", "入力画像と出力先を指定して。")
            return
        try:
            settings = self._settings()
            converter._validate_options(settings)
        except (tk.TclError, KeyError, StopIteration, ValueError) as error:
            if automatic:
                self.status.set("設定値を確認してください。")
            else:
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
        self._busy = True
        self.save_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.status.set("保存用に変換中…" if save else "プレビューを計算中…")
        started = time.perf_counter()
        revision = self._preview_revision
        psd_layers = tuple(mode for mode, enabled in self.psd_extra_layers.items() if enabled.get()) if save else ()
        threading.Thread(
            target=self._worker,
            args=(self._results, source, destination, settings, save, alpha_mask, self.invert_alpha_mask.get(), psd_layers),
            daemon=True,
        ).start()
        self._job_after = self.master.after(40, self._poll_result, save, destination, automatic, revision, started)

    @staticmethod
    def _worker(results: SimpleQueue, source: Path, destination: Path, settings: converter.ConversionOptions,
                save: bool, alpha_mask: Path | None, invert: bool, psd_layers: tuple[str, ...]) -> None:
        try:
            if save:
                result = converter.convert(source, destination, settings, alpha_mask, invert, psd_layers=psd_layers)
            else:
                result, _alpha = converter.convert_image(source, settings)
        except Exception as error:
            results.put((None, error))
        else:
            results.put((result, None))

    def _poll_result(self, saved: bool, destination: Path, automatic: bool, revision: int, started: float) -> None:
        self._job_after = None
        try:
            result, error = self._results.get_nowait()
        except Empty:
            self._job_after = self.master.after(40, self._poll_result, saved, destination, automatic, revision, started)
            return
        self._busy = False
        self.save_button.configure(state="normal")
        self.preview_button.configure(state="normal")
        if saved or revision == self._preview_revision:
            self._finished(result, error, saved, destination, automatic, time.perf_counter() - started)
        elif not self.auto_preview.get():
            self.status.set("「プレビュー更新」で現在の設定を反映できます。")
        if self._preview_due:
            self._start(save=False, automatic=True)

    def _finished(self, result: Image.Image | None, error: Exception | None,
                  saved: bool, destination: Path, automatic: bool, elapsed: float) -> None:
        if error is not None:
            self.status.set(f"変換エラー: {error}")
            if not automatic:
                messagebox.showerror("変換エラー", str(error))
            return
        assert result is not None
        thumbnail = result.convert("RGBA")
        thumbnail.thumbnail((480, 310), Image.Resampling.NEAREST)
        self.result_photo = ImageTk.PhotoImage(_checker_preview(thumbnail))
        self.result_label.configure(image=self.result_photo, text="")
        if saved:
            self.status.set(f"保存完了（{elapsed:.2f}秒）: {destination}")
        else:
            self.status.set(f"プレビュー: {result.width}×{result.height}px（未保存・{elapsed:.2f}秒）")
            self._show_full_preview(result)

    def _show_full_preview(self, image: Image.Image) -> None:
        if self._preview_window is None:
            self._preview_window = PreviewWindow(self, image)
            self._preview_window.protocol("WM_DELETE_WINDOW", self._hide_full_preview)
        else:
            self._preview_window.set_image(image)
            if self._preview_window.state() == "withdrawn":
                self._preview_window.deiconify()

    def _hide_full_preview(self) -> None:
        self.auto_preview.set(False)
        self._preview_window.withdraw()

    def destroy(self) -> None:
        self._clear_preview_request()
        for timer in (self._job_after, self._source_after):
            if timer is not None:
                self.master.after_cancel(timer)
        super().destroy()


def main() -> None:
    enable_native_pixels()
    root = TkinterDnD.Tk() if HAS_FILE_DROP else tk.Tk()
    root.title("Digital8 Anime V4")
    root.minsize(1040, 820)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
