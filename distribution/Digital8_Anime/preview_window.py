"""Explicit zoom, with 100% mapping one image pixel to one canvas pixel."""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class PreviewWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, image: Image.Image) -> None:
        super().__init__(master)
        self.original = image.convert("RGBA")
        self.zoom = tk.StringVar(value="100%")
        self.dimensions = tk.StringVar()
        self.geometry(f"{min(image.width + 50, int(self.winfo_screenwidth() * .85))}x"
                      f"{min(image.height + 100, int(self.winfo_screenheight() * .8))}")
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill="x")
        picker = ttk.Combobox(toolbar, textvariable=self.zoom, width=12, state="readonly",
                              values=("25%", "50%", "75%", "100%", "150%", "200%", "画面に収める"))
        picker.pack(side="left")
        picker.bind("<<ComboboxSelected>>", lambda _event: self.redraw())
        ttk.Label(toolbar, textvariable=self.dimensions).pack(side="left", padx=10)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, background="#777777", highlightthickness=0)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.update_idletasks()
        self.redraw()

    def set_image(self, image: Image.Image) -> None:
        self.original = image.convert("RGBA")
        self.redraw()

    def redraw(self) -> None:
        if self.zoom.get() == "画面に収める":
            self.update_idletasks()
            scale = min(self.canvas.winfo_width() / self.original.width,
                        self.canvas.winfo_height() / self.original.height, 1.0)
        else:
            scale = float(self.zoom.get().rstrip("%")) / 100
        width, height = max(1, round(self.original.width * scale)), max(1, round(self.original.height * scale))
        image = self.original if (width, height) == self.original.size else self.original.resize(
            (width, height), Image.Resampling.NEAREST,
        )
        self.preview_photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.itemconfigure(self.image_item, image=self.preview_photo)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.dimensions.set(f"画像 {self.original.width}×{self.original.height}px / 表示 {width}×{height}px")
        self.title(f"プレビュー {scale * 100:g}% | {self.original.width}×{self.original.height}px | 未保存")
