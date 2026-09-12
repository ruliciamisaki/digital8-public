#!/usr/bin/env python3
"""Small local GUI for pc8801_digital8_v1.py.  No network or cloud services."""

from __future__ import annotations

import threading
import tkinter as tk
import os
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

from pc8801_digital8_v1 import adjust_colours, convert, resize_input

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_FILE_DROP = True
except ImportError:  # The normal file-picker UI remains usable without it.
    DND_FILES = None
    TkinterDnD = None
    HAS_FILE_DROP = False


PREVIEW_SIZE = (500, 500)
IMAGE_TYPES = (("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*"))
PATTERN_MODE_LABELS = {
    "通常4×2": "normal-42",
    "通常4×4": "normal-44",
    "通常Mix（4×2＋4×4）": "normal-mix",
    "グラデーション": "gradient",
}


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.pixel_size = tk.IntVar(value=2)
        self.line_threshold = tk.IntVar(value=58)
        self.red_gain = tk.DoubleVar(value=1.00)
        self.green_gain = tk.DoubleVar(value=1.00)
        self.blue_gain = tk.DoubleVar(value=1.00)
        self.brightness = tk.DoubleVar(value=1.00)
        self.saturation = tk.DoubleVar(value=1.00)
        self.contrast = tk.DoubleVar(value=1.00)
        self.edge_ink = tk.DoubleVar(value=0.00)
        self.resize_640 = tk.BooleanVar(value=False)
        self.pc8801_200 = tk.BooleanVar(value=False)
        self.pattern_mode = tk.StringVar(value="通常Mix（4×2＋4×4）")
        self.status = tk.StringVar(value="画像を選んでね。")
        self.source_size = tk.StringVar(value="変換対象: 未選択")
        self.source_preview: ImageTk.PhotoImage | None = None
        self.result_preview: ImageTk.PhotoImage | None = None
        self.source_image: Image.Image | None = None
        self._preview_after: str | None = None
        self._scale_entry_values: list[tk.StringVar] = []
        self._build()

    def _build(self) -> None:
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="入力画像").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.input_path, width=66).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="選択…", command=self.choose_input).grid(row=0, column=2)

        ttk.Label(self, text="出力PNG").grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Entry(self, textvariable=self.output_path, width=66).grid(row=1, column=1, sticky="ew", padx=6, pady=(7, 0))
        output_buttons = ttk.Frame(self)
        output_buttons.grid(row=1, column=2, pady=(7, 0))
        ttk.Button(output_buttons, text="保存先…", command=self.choose_output).pack(fill="x")
        ttk.Button(output_buttons, text="出力フォルダを開く", command=self.open_output_folder).pack(fill="x", pady=(3, 0))

        controls = ttk.LabelFrame(self, text="変換設定（RGB・彩度・明度・コントラストは1.00＝無補正）", padding=8)
        controls.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        ttk.Label(controls, text="仮想ピクセル").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(controls, from_=1, to=8, textvariable=self.pixel_size, width=5).grid(row=0, column=1, padx=(5, 18))
        ttk.Label(controls, text="1=原寸、2=2×2のドットとして描画").grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(
            controls,
            text="入力を横幅640pxへ縮小（縦は比率維持・幅640以下は原寸）",
            variable=self.resize_640,
            command=self.schedule_source_preview,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.add_scale(controls, 2, "主線しきい値", self.line_threshold, 0, 120)
        self.add_scale(controls, 3, "赤 (R)", self.red_gain, 0.50, 1.50, preview=True)
        self.add_scale(controls, 4, "緑 (G)", self.green_gain, 0.50, 1.50, preview=True)
        self.add_scale(controls, 5, "青 (B)", self.blue_gain, 0.50, 1.50, preview=True)
        self.add_scale(controls, 6, "彩度", self.saturation, 0.00, 2.00, preview=True)
        self.add_scale(controls, 7, "明度", self.brightness, 0.50, 1.50, preview=True)
        self.add_scale(controls, 8, "コントラスト", self.contrast, 0.50, 1.50, preview=True)
        self.add_scale(controls, 9, "輪郭抽出", self.edge_ink, 0.00, 1.00)
        ttk.Checkbutton(controls, text="PC-8801 200ライン出力（入力高を1/2で変換し、各ラインを縦2倍）", variable=self.pc8801_200).grid(row=10, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(controls, text="パターン方式").grid(row=11, column=0, sticky="w", pady=(6, 0))
        mode_picker = ttk.Combobox(controls, textvariable=self.pattern_mode, values=tuple(PATTERN_MODE_LABELS), state="readonly", width=24)
        mode_picker.grid(row=11, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(6, 0))
        ttk.Label(controls, text="4×4は全色を16段階で混色。Mixは4×2を優先。").grid(row=12, column=1, columnspan=3, sticky="w", padx=(5, 0), pady=(2, 0))

        self.convert_button = ttk.Button(self, text="PC-8801 デジタル8色へ変換", command=self.start_convert)
        self.convert_button.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        previews = ttk.Frame(self)
        previews.grid(row=4, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(4, weight=1)
        previews.columnconfigure((0, 1), weight=1)
        source = ttk.LabelFrame(previews, text="入力", padding=6)
        result = ttk.LabelFrame(previews, text="8色出力", padding=6)
        source.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        result.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.source_label = ttk.Label(source, text="未選択", anchor="center")
        self.result_label = ttk.Label(result, text="未変換", anchor="center")
        ttk.Label(source, textvariable=self.source_size, anchor="center").pack(side="bottom", fill="x")
        self.source_label.pack(fill="both", expand=True)
        self.result_label.pack(fill="both", expand=True)
        ttk.Label(self, textvariable=self.status).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        if HAS_FILE_DROP:
            for target in (self, self.source_label):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self.drop_input)
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
        scale = ttk.Scale(parent, from_=start, to=end, variable=variable, orient="horizontal", length=220)
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
        scale.grid(row=row, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=(6, 0))
        entry = ttk.Entry(parent, textvariable=entry_value, width=7, justify="right")
        entry.grid(row=row, column=3, padx=6, pady=(6, 0))
        entry.bind("<Return>", normalise_entry)
        entry.bind("<FocusOut>", normalise_entry)
        entry_value.trace_add("write", entry_changed)

    def choose_input(self) -> None:
        filename = filedialog.askopenfilename(title="入力画像を選択", filetypes=IMAGE_TYPES)
        if not filename:
            return
        self.set_input(Path(filename))

    def drop_input(self, event: tk.Event) -> None:
        filenames = self.master.tk.splitlist(event.data)
        if not filenames:
            return
        path = Path(filenames[0])
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            messagebox.showerror("非対応ファイル", "PNG、JPEG、WebP、BMP、GIFの画像を落として。")
            return
        self.set_input(path)

    def set_input(self, path: Path) -> None:
        if not path.is_file():
            messagebox.showerror("入力エラー", f"画像が見つからない。\n{path}")
            return
        self.input_path.set(str(path))
        self.output_path.set(str(path.with_name(f"{path.stem}_pc8801_v1.png")))
        self.source_image = Image.open(path).convert("RGB")
        self.refresh_source_preview()
        self.status.set("設定を確認して「変換」を押して。")

    def choose_output(self) -> None:
        filename = filedialog.asksaveasfilename(title="出力PNGの保存先", defaultextension=".png", filetypes=(("PNG", "*.png"),))
        if filename:
            self.output_path.set(filename)

    def open_output_folder(self) -> None:
        filename = self.output_path.get().strip()
        folder = Path(filename).parent if filename else Path.cwd()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def show_preview(self, path: Path, label: ttk.Label, side: str) -> None:
        image = Image.open(path).convert("RGB")
        image.thumbnail(PREVIEW_SIZE, Image.Resampling.NEAREST)
        photo = ImageTk.PhotoImage(image)
        label.configure(image=photo, text="")
        if side == "source":
            self.source_preview = photo
        else:
            self.result_preview = photo

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
        image.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
        adjusted = adjust_colours(
            np.asarray(image, dtype=np.uint8),
            self.brightness.get(), self.saturation.get(),
            self.red_gain.get(), self.green_gain.get(), self.blue_gain.get(),
            self.contrast.get(),
        )
        image = Image.fromarray(adjusted, "RGB")
        self.source_preview = ImageTk.PhotoImage(image)
        self.source_label.configure(image=self.source_preview, text="")

    def start_convert(self) -> None:
        source = Path(self.input_path.get())
        destination = Path(self.output_path.get())
        if not source.is_file() or not destination.name:
            messagebox.showerror("入力不足", "入力画像と出力先を指定して。")
            return
        self.convert_button.configure(state="disabled")
        self.status.set("変換中… 大きな画像は少し待って。")
        settings = {
            "pixel_size": self.pixel_size.get(),
            "line_threshold": self.line_threshold.get(),
            "brightness": self.brightness.get(),
            "saturation": self.saturation.get(),
            "yellow_bias": 0.0,
            "edge_strength": self.edge_ink.get(),
            "pc8801_200": self.pc8801_200.get(),
            "pattern_mode": PATTERN_MODE_LABELS[self.pattern_mode.get()],
            "red_gain": self.red_gain.get(),
            "green_gain": self.green_gain.get(),
            "blue_gain": self.blue_gain.get(),
            "contrast": self.contrast.get(),
            "resize_640": self.resize_640.get(),
        }
        threading.Thread(target=self._convert_worker, args=(source, destination, settings), daemon=True).start()

    def _convert_worker(self, source: Path, destination: Path, settings: dict[str, object]) -> None:
        try:
            convert(source, destination, **settings)
        except Exception as error:  # Shown in the UI instead of disappearing in a worker thread.
            self.master.after(0, lambda: self.finished(error, None))
        else:
            self.master.after(0, lambda: self.finished(None, destination))

    def finished(self, error: Exception | None, destination: Path | None) -> None:
        self.convert_button.configure(state="normal")
        if error:
            self.status.set("変換に失敗した。")
            messagebox.showerror("変換エラー", str(error))
            return
        assert destination is not None
        self.show_preview(destination, self.result_label, "result")
        self.status.set(f"完了: {destination}")


def main() -> None:
    root = TkinterDnD.Tk() if HAS_FILE_DROP else tk.Tk()
    root.title("PC-8801 Digital 8 Converter V1")
    root.minsize(780, 810)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
