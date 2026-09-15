#!/usr/bin/env python3
"""GUI entry point for the image-balanced PC-8801 V2 converter."""

import tkinter as tk
from pathlib import Path

import pc8801_gui_v1 as legacy_gui
from pc8801_digital8_v2 import convert


legacy_gui.convert = convert


class App(legacy_gui.App):
    def set_input(self, path: Path) -> None:
        super().set_input(path)
        if path.is_file():
            self.output_path.set(str(path.with_name(f"{path.stem}_pc8801_v2.png")))


def main() -> None:
    root = legacy_gui.TkinterDnD.Tk() if legacy_gui.HAS_FILE_DROP else tk.Tk()
    root.title("PC-8801 Digital 8 Converter V2 (Balanced Reference)")
    root.minsize(780, 880)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
