"""An editable RGB palette; changes are committed only with Apply."""

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Callable

import anime_paint


class PaletteEditor(tk.Toplevel):
    def __init__(self, master: tk.Misc, colours: tuple,
                 commit: Callable[[tuple], None], *, fixed_count: int | None = None,
                 palette_name: str = "アニメ塗り") -> None:
        colours = anime_paint.validate_palette(colours)
        if fixed_count is not None and len(colours) != fixed_count:
            raise ValueError(f"パレットは{fixed_count}色で指定して。")
        super().__init__(master)
        self.fixed_count = fixed_count
        self.columns = 8 if fixed_count is not None else 16
        self.title(f"{palette_name}のパレット編集（{fixed_count}色固定）" if fixed_count is not None
                   else f"{palette_name}のパレット編集（1〜4096色）")
        self.colours = list(colours)
        self.commit = commit
        self.selected = 0
        self.hex_value = tk.StringVar()
        self.caption = tk.StringVar()
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, width=560 if fixed_count is not None else 760,
                           height=(50 * ((fixed_count + 7) // 8) + 20) if fixed_count else 350,
                           highlightthickness=0)
        self.canvas = canvas
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.swatches = ttk.Frame(canvas, padding=10)
        canvas.create_window(0, 0, window=self.swatches, anchor="nw")
        self.swatches.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        controls = ttk.Frame(self, padding=10)
        controls.pack(fill="x")
        ttk.Label(controls, textvariable=self.caption).grid(row=0, column=0, columnspan=4, sticky="w")
        entry = ttk.Entry(controls, textvariable=self.hex_value, width=12)
        entry.grid(row=1, column=0, sticky="w")
        entry.bind("<Return>", lambda _event: self.apply_hex())
        ttk.Button(controls, text="RGBを反映", command=self.apply_hex).grid(row=1, column=1)
        ttk.Button(controls, text="色を選ぶ…", command=self.choose).grid(row=1, column=2)
        if fixed_count is None:
            ttk.Button(controls, text="色を追加", command=self.add).grid(row=2, column=0, pady=5)
            ttk.Button(controls, text="選択色を削除", command=self.remove).grid(row=2, column=1)
        ttk.Button(controls, text="JSON読込…", command=self.load).grid(row=3, column=0)
        ttk.Button(controls, text="JSON保存…", command=self.save).grid(row=3, column=1)
        ttk.Button(controls, text="適用して閉じる", command=self.apply).grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(controls, text="キャンセル", command=self.destroy).grid(row=4, column=2, pady=(10, 0))
        self.redraw()
        self.transient(master.winfo_toplevel())

    def redraw(self) -> None:
        for widget in self.swatches.winfo_children():
            widget.destroy()
        for index, rgb in enumerate(self.colours):
            colour = "#%02x%02x%02x" % rgb
            foreground = "white" if sum(rgb) < 360 else "black"
            tk.Button(self.swatches, text=str(index + 1), background=colour, foreground=foreground,
                      width=4, relief="sunken" if index == self.selected else "raised",
                      command=lambda index=index: self.select(index)).grid(row=index // self.columns,
                                                                          column=index % self.columns)
        self.caption.set(f"{len(self.colours)}色 / 選択: {self.selected + 1}番  （クリックで選択）")
        self.hex_value.set("#%02x%02x%02x" % self.colours[self.selected])

    def select(self, index: int) -> None:
        self.selected = index
        self.redraw()

    def apply_hex(self) -> bool:
        text = self.hex_value.get().strip().lstrip("#")
        try:
            if len(text) != 6:
                raise ValueError
            colour = tuple(int(text[start:start + 2], 16) for start in (0, 2, 4))
        except ValueError:
            messagebox.showerror("色の入力エラー", "#RRGGBB の6桁で指定して。", parent=self)
            return False
        self.colours[self.selected] = colour
        self.redraw()
        return True

    def choose(self) -> None:
        _rgb, hex_colour = colorchooser.askcolor(self.hex_value.get(), parent=self)
        if hex_colour:
            self.hex_value.set(hex_colour)
            self.apply_hex()

    def add(self) -> None:
        if self.fixed_count is not None:
            return
        if len(self.colours) >= 4096:
            messagebox.showinfo("色数の上限", "最大4096色だよ。", parent=self)
            return
        self.colours.append(self.colours[self.selected])
        self.selected = len(self.colours) - 1
        self.redraw()

    def remove(self) -> None:
        if self.fixed_count is not None:
            return
        if len(self.colours) <= 1:
            return
        self.colours.pop(self.selected)
        self.selected = min(self.selected, len(self.colours) - 1)
        self.redraw()

    def load(self) -> None:
        filename = filedialog.askopenfilename(filetypes=(("RGB palette JSON", "*.json"),), parent=self)
        if not filename:
            return
        try:
            colours = list(anime_paint.load_palette(filename))
            if self.fixed_count is not None and len(colours) != self.fixed_count:
                raise ValueError(f"パレットは{self.fixed_count}色で指定して。")
            self.colours = colours
        except (ValueError, OSError) as error:
            messagebox.showerror("パレット読込エラー", str(error), parent=self)
            return
        self.selected = 0
        self.redraw()

    def save(self) -> None:
        if not self.apply_hex():
            return
        filename = filedialog.asksaveasfilename(defaultextension=".json",
                                               filetypes=(("RGB palette JSON", "*.json"),), parent=self)
        if filename:
            try:
                anime_paint.save_palette(filename, self.colours)
            except OSError as error:
                messagebox.showerror("パレット保存エラー", str(error), parent=self)

    def apply(self) -> None:
        if self.apply_hex():
            self.commit(tuple(self.colours))
            self.destroy()
