#!/usr/bin/env python3
"""Local GUI for the PC-9801 fixed-palette 16-colour converter."""
from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk
from pc9801_16_v1 import adjust_colours, convert, load_reference, resize_input

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_FILE_DROP = True
except ImportError:
    DND_FILES = None; TkinterDnD = None; HAS_FILE_DROP = False

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PATTERN_LABELS = {
    "通常セル（4/16刻み）": "cell",
    "頻出Mix（資料由来・1/16刻み）": "reference",
    "グラデーション（分散型・1/16刻み）": "gradient",
}
ALPHA_MODE_LABELS = {
    "そのまま保持（階調あり）": "preserve",
    "二値化（透明／不透明）": "binary",
}
PALETTE_DESCRIPTIONS = {
    "palette_01": "DokiDokiバケーション-MAP",
    "palette_02": "DokiDokiバケーション-AYAKA",
    "palette_03": "水晶物語CQ版",
    "palette_04": "水晶物語Dante98II完全版",
    "palette_05": "同級生2-01",
    "palette_06": "同級生2-02",
    "palette_07": "白き魔女-山道",
    "palette_08": "白き魔女-湖",
    "digital8_half": "デジタル8色+明るさ半分",
    "ED3": "白き魔女・村",
    "16color_同級生2": "同級生2-03",
    "16colorきゃんプルミ": "きゃんプルミ-01",
    "16colorきゃんプルミ2": "きゃんプルミ-02",
    "16color_同級生1": "同級生1",
}


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.input_path, self.output_path = tk.StringVar(), tk.StringVar()
        self.pixel_size, self.line_threshold = tk.IntVar(value=1), tk.IntVar(value=58)
        self.red_gain, self.green_gain, self.blue_gain = (
            tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)
        )
        self.brightness, self.saturation = tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)
        self.contrast, self.edge_ink = tk.DoubleVar(value=1.0), tk.DoubleVar(value=0.0)
        self.resize_640 = tk.BooleanVar(value=False)
        self.alpha_mode = tk.StringVar(value="そのまま保持（階調あり）")
        self.alpha_threshold = tk.IntVar(value=128)
        self.export_alpha_mask = tk.BooleanVar(value=True)
        self.invert_alpha_mask = tk.BooleanVar(value=False)
        self.fill_stability = tk.DoubleVar(value=0.20)
        self.pattern_mode = tk.StringVar(value="頻出Mix（資料由来・1/16刻み）")
        self.palette, self.status = tk.StringVar(), tk.StringVar(value="画像を選んでね。")
        self.source_size = tk.StringVar(value="変換対象: 未選択")
        self.photos: list[ImageTk.PhotoImage | None] = [None, None]
        self.source_image: Image.Image | None = None
        self._preview_after: str | None = None
        self._scale_entry_values: list[tk.StringVar] = []
        reference = load_reference()
        self.palette_colours = {item["id"]: item["colours"] for item in reference["palettes"]}
        self.palette_labels = {
            f"{item['id']}（{PALETTE_DESCRIPTIONS.get(item['id'], ', '.join(item['sources']))}）": item["id"]
            for item in reference["palettes"]
        }
        self.palette.set(next(iter(self.palette_labels))); self.build()

    def build(self) -> None:
        self.grid(sticky="nsew"); self.master.columnconfigure(0, weight=1); self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text="入力画像").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.input_path, width=68).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="選択…", command=self.pick_input).grid(row=0, column=2)
        ttk.Label(self, text="出力PNG").grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(self, textvariable=self.output_path).grid(row=1, column=1, sticky="ew", padx=6, pady=(7, 0))
        output_buttons = ttk.Frame(self); output_buttons.grid(row=1, column=2, pady=(7, 0))
        ttk.Button(output_buttons, text="保存先…", command=self.pick_output).pack(fill="x")
        ttk.Button(output_buttons, text="出力フォルダを開く", command=self.open_output_folder).pack(fill="x", pady=(3, 0))
        controls = ttk.LabelFrame(self, text="PC-9801 16色変換（RGB・彩度・明度・コントラストは1.00＝無補正）", padding=8)
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 8)); controls.columnconfigure(2, weight=1)
        ttk.Label(controls, text="固定パレット").grid(row=0, column=0, sticky="w")
        self.palette_picker = ttk.Combobox(controls, textvariable=self.palette, values=tuple(self.palette_labels), state="readonly", width=62)
        self.palette_picker.grid(row=0, column=1, columnspan=3, sticky="ew", padx=6)
        self.palette_picker.bind("<<ComboboxSelected>>", self.update_palette_preview)
        ttk.Label(controls, text="パレット内容").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.palette_canvas = tk.Canvas(controls, width=404, height=30, background="#d9d9d9", highlightthickness=1, highlightbackground="#666666")
        self.palette_canvas.grid(row=1, column=1, columnspan=3, sticky="w", padx=6, pady=(6, 0))
        self.update_palette_preview()
        ttk.Label(controls, text="仮想ピクセル").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(controls, from_=1, to=8, textvariable=self.pixel_size, width=5).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Checkbutton(
            controls,
            text="入力を横幅640pxへ縮小（縦は比率維持・幅640以下は原寸）",
            variable=self.resize_640,
            command=self.schedule_source_preview,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.add_scale(controls, 4, "主線しきい値", self.line_threshold, 0, 120)
        self.add_scale(controls, 5, "赤 (R)", self.red_gain, 0.5, 1.5, preview=True)
        self.add_scale(controls, 6, "緑 (G)", self.green_gain, 0.5, 1.5, preview=True)
        self.add_scale(controls, 7, "青 (B)", self.blue_gain, 0.5, 1.5, preview=True)
        self.add_scale(controls, 8, "彩度", self.saturation, 0.0, 2.0, preview=True)
        self.add_scale(controls, 9, "明度", self.brightness, 0.5, 1.5, preview=True)
        self.add_scale(controls, 10, "コントラスト", self.contrast, 0.5, 1.5, preview=True)
        self.add_scale(controls, 11, "輪郭抽出", self.edge_ink, 0.0, 1.0)
        self.add_scale(controls, 12, "塗り判定の安定化", self.fill_stability, 0.0, 1.0)
        ttk.Label(controls, text="パターン方式").grid(row=13, column=0, sticky="w", pady=(7, 0))
        ttk.Combobox(controls, textvariable=self.pattern_mode, values=tuple(PATTERN_LABELS), state="readonly", width=34).grid(row=13, column=1, columnspan=2, sticky="w", padx=6, pady=(7, 0))
        ttk.Label(controls, text="アルファ処理").grid(row=14, column=0, sticky="w", pady=(7, 0))
        ttk.Combobox(controls, textvariable=self.alpha_mode, values=tuple(ALPHA_MODE_LABELS), state="readonly", width=27).grid(row=14, column=1, columnspan=2, sticky="w", padx=6, pady=(7, 0))
        self.add_scale(controls, 15, "アルファしきい値（二値化時）", self.alpha_threshold, 0, 255)
        ttk.Checkbutton(controls, text="透明マスクも書き出す（白＝不透明／黒＝透明）", variable=self.export_alpha_mask).grid(row=16, column=0, columnspan=3, sticky="w", pady=(7, 0))
        ttk.Checkbutton(controls, text="マスクを白黒反転", variable=self.invert_alpha_mask).grid(row=16, column=3, sticky="w", pady=(7, 0))
        self.convert_button = ttk.Button(self, text="PC-9801風16色へ変換", command=self.start)
        self.convert_button.grid(row=3, column=0, columnspan=3, sticky="ew")
        previews = ttk.Frame(self); previews.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=8)
        previews.columnconfigure((0, 1), weight=1); previews.rowconfigure(0, weight=1); self.rowconfigure(4, weight=1)
        self.source_label = ttk.Label(previews, text="入力（ここへドロップ）", anchor="center")
        self.result_label = ttk.Label(previews, text="出力", anchor="center")
        self.source_label.grid(row=0, column=0, sticky="nsew", padx=4); self.result_label.grid(row=0, column=1, sticky="nsew", padx=4)
        ttk.Label(previews, textvariable=self.source_size, anchor="center").grid(row=1, column=0, sticky="ew", padx=4)
        ttk.Label(self, textvariable=self.status).grid(row=5, column=0, columnspan=3, sticky="w")
        if HAS_FILE_DROP:
            for target in (self, self.source_label):
                target.drop_target_register(DND_FILES); target.dnd_bind("<<Drop>>", self.drop_input)
            self.status.set("画像を選ぶか、ここへドラッグ＆ドロップして。")

    def add_scale(
        self,
        parent: ttk.Frame,
        row: int,
        text: str,
        variable: tk.Variable,
        start: float,
        end: float,
        preview: bool = False,
    ) -> None:
        ttk.Label(parent, text=text).grid(row=row, column=0, sticky="w", pady=(6, 0))
        scale = ttk.Scale(parent, from_=start, to=end, variable=variable, orient="horizontal", length=240)
        is_integer = isinstance(variable, tk.IntVar)
        format_value = lambda value: str(int(round(value))) if is_integer else f"{value:.2f}"
        entry_value = tk.StringVar(value=format_value(float(variable.get())))
        self._scale_entry_values.append(entry_value)
        syncing = False

        def scale_changed(raw_value: str) -> None:
            nonlocal syncing
            value = int(round(float(raw_value))) if is_integer else round(float(raw_value), 2)
            syncing = True
            variable.set(value)
            entry_value.set(format_value(float(value)))
            syncing = False
            if preview:
                self.schedule_source_preview()

        def entry_changed(*_args: object) -> None:
            if syncing:
                return
            try:
                value = float(entry_value.get())
            except ValueError:
                return
            if start <= value <= end:
                variable.set(int(round(value)) if is_integer else round(value, 2))
                if preview:
                    self.schedule_source_preview()

        def normalise_entry(_event: tk.Event) -> None:
            try:
                value = float(entry_value.get())
            except ValueError:
                value = float(variable.get())
            value = min(end, max(start, value))
            variable.set(int(round(value)) if is_integer else round(value, 2))
            entry_value.set(format_value(float(variable.get())))
            if preview:
                self.schedule_source_preview()

        scale.configure(command=scale_changed)
        scale.grid(row=row, column=1, columnspan=2, sticky="w", padx=6, pady=(6, 0))
        entry = ttk.Entry(parent, textvariable=entry_value, width=7, justify="right")
        entry.grid(row=row, column=3, padx=6, pady=(6, 0))
        entry.bind("<Return>", normalise_entry)
        entry.bind("<FocusOut>", normalise_entry)
        entry_value.trace_add("write", entry_changed)

    def update_palette_preview(self, _event: tk.Event | None = None) -> None:
        palette_id = self.palette_labels[self.palette.get()]
        colours = self.palette_colours[palette_id]
        swatch = 25
        self.palette_canvas.delete("all")
        self.palette_canvas.configure(width=len(colours) * swatch + 4)
        for index, colour in enumerate(colours):
            red, green, blue = map(int, colour)
            fill = f"#{red:02x}{green:02x}{blue:02x}"
            x0 = 2 + index * swatch
            self.palette_canvas.create_rectangle(x0, 3, x0 + swatch - 1, 27, fill=fill, outline="#555555")

    def set_input(self, path: Path) -> None:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            messagebox.showerror("入力エラー", "対応する画像ファイルを指定して。"); return
        self.input_path.set(str(path)); self.output_path.set(str(path.with_name(path.stem + "_pc9801_v1.png")))
        self.source_image = Image.open(path).convert("RGB")
        self.refresh_source_preview()

    def pick_input(self) -> None:
        name = filedialog.askopenfilename(filetypes=(("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),))
        if name: self.set_input(Path(name))

    def drop_input(self, event: tk.Event) -> None:
        names = self.master.tk.splitlist(event.data)
        if names: self.set_input(Path(names[0]))

    def pick_output(self) -> None:
        name = filedialog.asksaveasfilename(defaultextension=".png", filetypes=(("PNG", "*.png"),))
        if name: self.output_path.set(name)

    def open_output_folder(self) -> None:
        text = self.output_path.get().strip(); folder = Path(text).parent if text else Path.cwd()
        folder.mkdir(parents=True, exist_ok=True); os.startfile(str(folder))

    def preview(self, path: Path, label: ttk.Label, slot: int) -> None:
        image = Image.open(path).convert("RGB"); image.thumbnail((500, 430), Image.Resampling.NEAREST)
        self.photos[slot] = ImageTk.PhotoImage(image); label.configure(image=self.photos[slot], text="")

    def schedule_source_preview(self, _value: str | None = None) -> None:
        if self._preview_after is not None:
            self.master.after_cancel(self._preview_after)
        self._preview_after = self.master.after(80, self.refresh_source_preview)

    def refresh_source_preview(self) -> None:
        self._preview_after = None
        if self.source_image is None:
            return
        image = resize_input(self.source_image.copy(), self.resize_640.get())
        self.source_size.set(f"変換対象: {image.width}×{image.height}px")
        image.thumbnail((500, 430), Image.Resampling.LANCZOS)
        adjusted = adjust_colours(
            np.asarray(image, dtype=np.uint8),
            self.brightness.get(), self.saturation.get(),
            self.red_gain.get(), self.green_gain.get(), self.blue_gain.get(),
            self.contrast.get(),
        )
        self.photos[0] = ImageTk.PhotoImage(Image.fromarray(adjusted, "RGB"))
        self.source_label.configure(image=self.photos[0], text="")

    def start(self) -> None:
        source, destination = Path(self.input_path.get()), Path(self.output_path.get())
        if not source.is_file() or not destination.name:
            messagebox.showerror("入力不足", "入力画像と出力先を指定して。"); return
        settings = {
            "palette_id": self.palette_labels[self.palette.get()],
            "pixel_size": self.pixel_size.get(),
            "line_threshold": self.line_threshold.get(),
            "brightness": self.brightness.get(),
            "saturation": self.saturation.get(),
            "yellow_bias": 0.0,
            "edge_strength": self.edge_ink.get(),
            "fill_stability": self.fill_stability.get(),
            "pattern_mode": PATTERN_LABELS[self.pattern_mode.get()],
            "red_gain": self.red_gain.get(),
            "green_gain": self.green_gain.get(),
            "blue_gain": self.blue_gain.get(),
            "contrast": self.contrast.get(),
            "resize_640": self.resize_640.get(),
            "alpha_mask": destination.with_name(f"{destination.stem}_alpha_mask.png") if self.export_alpha_mask.get() else None,
            "invert_alpha_mask": self.invert_alpha_mask.get(),
            "alpha_mode": ALPHA_MODE_LABELS[self.alpha_mode.get()],
            "alpha_threshold": self.alpha_threshold.get(),
        }
        self.convert_button.configure(state="disabled"); self.status.set("変換中…")
        threading.Thread(target=self.worker, args=(source, destination, settings), daemon=True).start()

    def worker(self, source: Path, destination: Path, settings: dict[str, object]) -> None:
        try: convert(source, destination, **settings)
        except Exception as error: self.master.after(0, lambda: self.finished(error, None))
        else: self.master.after(0, lambda: self.finished(None, destination))

    def finished(self, error: Exception | None, destination: Path | None) -> None:
        self.convert_button.configure(state="normal")
        if error: self.status.set("変換失敗"); messagebox.showerror("変換エラー", str(error)); return
        assert destination is not None; self.preview(destination, self.result_label, 1); self.status.set(f"完了: {destination}")


if __name__ == "__main__":
    root = TkinterDnD.Tk() if HAS_FILE_DROP else tk.Tk()
    root.title("PC-9801 4096色中16色 Converter V1"); root.minsize(900, 920); App(root); root.mainloop()
