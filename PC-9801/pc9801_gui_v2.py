#!/usr/bin/env python3
"""GUI entry point for the image-balanced PC-9801 V2 converter."""

import tkinter as tk
from pathlib import Path

import pc9801_gui_v1 as legacy_gui
from pc9801_16_v2 import convert, load_reference


legacy_gui.convert = convert
legacy_gui.load_reference = load_reference


class App(legacy_gui.App):
    def set_input(self, path: Path) -> None:
        super().set_input(path)
        if path.is_file() and path.suffix.lower() in legacy_gui.IMAGE_SUFFIXES:
            self.output_path.set(str(path.with_name(f"{path.stem}_pc9801_v2.png")))


def main() -> None:
    root = legacy_gui.TkinterDnD.Tk() if legacy_gui.HAS_FILE_DROP else tk.Tk()
    root.title("PC-9801 16-colour Converter V2 (Balanced Reference)")
    root.minsize(900, 850)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
