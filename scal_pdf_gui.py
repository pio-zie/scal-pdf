from datetime import date
from pathlib import Path
import os
import sys
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox

import pikepdf
from tkinterdnd2 import DND_FILES, TkinterDnD

base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def _enable_windows_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _norm(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except OSError:
        return os.path.normcase(str(path))


class App:
    def __init__(self, root: TkinterDnD.Tk) -> None:
        self.root = root
        self.files: list[Path] = []
        # Folder pierwszego wczytanego pliku. Nie zmienia się przy zmianie kolejności.
        self.default_dir: Path | None = None

        root.title("Scal PDF")
        root.minsize(520, 360)
        root.geometry("640x420")

        outer = tk.Frame(root, padx=12, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        hint = tk.Label(outer, text="Przeciągnij pliki PDF tutaj", anchor="w")
        hint.pack(fill=tk.X, pady=(0, 8))

        mid = tk.Frame(outer)
        mid.pack(fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(mid)
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            activestyle="dotbox",
            exportselection=False,
        )
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)

        side = tk.Frame(mid)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        tk.Button(side, text="Góra", width=10, command=lambda: self._move(-1)).pack(fill=tk.X, pady=(0, 4))
        tk.Button(side, text="Dół", width=10, command=lambda: self._move(1)).pack(fill=tk.X, pady=(0, 4))
        tk.Button(side, text="Usuń", width=10, command=self._remove).pack(fill=tk.X, pady=(0, 12))
        tk.Button(side, text="Wyczyść", width=10, command=self._clear).pack(fill=tk.X)

        bottom = tk.Frame(outer)
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.path_var = tk.StringVar()
        self.path_label = tk.Label(bottom, textvariable=self.path_var, anchor="w", justify=tk.LEFT)
        self.path_label.pack(fill=tk.X, pady=(0, 8))

        buttons = tk.Frame(bottom)
        buttons.pack(side=tk.RIGHT)
        tk.Button(buttons, text="Zapisz", width=10, command=self._save).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(buttons, text="Zamknij", width=10, command=root.destroy).pack(side=tk.LEFT)

        self._refresh_path()
        self._register_drop_tree(root)
        outer.bind("<Configure>", self._on_resize)
        self.listbox.bind("<Delete>", lambda _event: self._remove())

    def _on_resize(self, event: tk.Event) -> None:
        self.path_label.configure(wraplength=max(event.width - 8, 200))

    def _register_drop_tree(self, widget: tk.Misc) -> None:
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._register_drop_tree(child)

    def _refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for path in self.files:
            self.listbox.insert(tk.END, path.name)

    def _refresh_path(self) -> None:
        if self.default_dir is None:
            self.path_var.set("Domyślna ścieżka: —")
        else:
            self.path_var.set(f"Domyślna ścieżka: {self.default_dir}")

    def _select(self, index: int) -> None:
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        self.listbox.see(index)

    def _on_drop(self, event: tk.Event) -> None:
        try:
            raw_paths = self.root.tk.splitlist(event.data)
        except tk.TclError:
            return

        added = 0
        non_pdf = 0
        for raw in raw_paths:
            path = Path(raw)
            if not path.is_file() or path.suffix.lower() != ".pdf":
                non_pdf += 1
                continue
            resolved = path.resolve()
            if any(_norm(existing) == _norm(resolved) for existing in self.files):
                continue
            if self.default_dir is None:
                self.default_dir = resolved.parent
            self.files.append(resolved)
            added += 1

        if added:
            self._refresh_list()
            self._refresh_path()
        elif raw_paths and non_pdf == len(raw_paths):
            messagebox.showinfo(
                "Scal PDF",
                "Żaden z upuszczonych plików nie jest plikiem PDF.",
                parent=self.root,
            )

    def _selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        return int(selection[0])

    def _move(self, delta: int) -> None:
        index = self._selected_index()
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self.files):
            return
        self.files[index], self.files[target] = self.files[target], self.files[index]
        self._refresh_list()
        self._select(target)

    def _remove(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self.files[index]
        if not self.files:
            self.default_dir = None
        self._refresh_list()
        self._refresh_path()
        if self.files:
            self._select(min(index, len(self.files) - 1))

    def _clear(self) -> None:
        self.files.clear()
        self.default_dir = None
        self._refresh_list()
        self._refresh_path()

    def _report(self, message: str, exc: BaseException | None = None) -> None:
        log_note = ""
        if exc is not None:
            try:
                log_path = base_dir / "scal_pdf_gui-error.log"
                log_path.write_text(traceback.format_exc(), encoding="utf-8")
                log_note = f"\n\nZapisano log:\n{log_path}"
            except Exception:
                log_note = ""
        messagebox.showerror("Scal PDF", message + log_note, parent=self.root)

    def _is_input_file(self, output: Path) -> bool:
        output_key = _norm(output)
        for src in self.files:
            if _norm(src) == output_key:
                return True
            try:
                if output.exists() and src.exists() and output.samefile(src):
                    return True
            except OSError:
                pass
        return False

    def _save(self) -> None:
        if not self.files:
            messagebox.showwarning("Scal PDF", "Najpierw dodaj pliki PDF.", parent=self.root)
            return

        first = self.files[0]
        date_stamp = date.today().strftime("%y%m%d")
        suggested = f"{first.stem}-0000_cale opracowanie_{date_stamp}-v01.pdf"
        initial_dir = self.default_dir if self.default_dir is not None else first.parent

        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Zapisz scalony PDF",
            initialdir=str(initial_dir),
            initialfile=suggested,
            defaultextension=".pdf",
            filetypes=[("Pliki PDF", "*.pdf")],
        )
        if not chosen:
            return

        output = Path(chosen)
        if output.suffix.lower() != ".pdf":
            output = output.with_name(output.name + ".pdf")

        if self._is_input_file(output):
            messagebox.showerror(
                "Scal PDF",
                "Nie można zapisać wyniku do pliku, który jest jednym z plików wejściowych.",
                parent=self.root,
            )
            return

        try:
            self._merge_to(output)
        except Exception as exc:
            self._report("Nieoczekiwany błąd podczas zapisu.", exc)

    def _merge_to(self, output: Path) -> None:
        merged = pikepdf.Pdf.new()
        try:
            for pdf in self.files:
                try:
                    with pikepdf.open(pdf) as src:
                        merged.pages.extend(src.pages)
                except Exception as exc:
                    self._report(f"Błąd podczas dodawania:\n{pdf.name}", exc)
                    return
            try:
                merged.save(
                    output,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                )
            except Exception as exc:
                self._report("Błąd podczas zapisu wynikowego PDF.", exc)
                return
        finally:
            merged.close()

        messagebox.showinfo("Scal PDF", f"Gotowe!\n\nUtworzono:\n{output}", parent=self.root)


def main() -> None:
    _enable_windows_dpi()
    root = TkinterDnD.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        log_path: Path | None
        try:
            log_path = base_dir / "scal_pdf_gui-error.log"
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            log_path = None
        try:
            fallback = tk.Tk()
            fallback.withdraw()
            extra = f"\n\nZapisano log:\n{log_path}" if log_path is not None else ""
            messagebox.showerror("Scal PDF", f"Nieoczekiwany błąd aplikacji.\n{exc}{extra}")
            fallback.destroy()
        except Exception:
            pass
        raise SystemExit(1)
