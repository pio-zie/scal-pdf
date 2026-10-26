from datetime import date
from pathlib import Path
import io
import os
import queue
import sys
import threading
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


def _degrade(png_bytes: bytes, rotation: float, blur: float, noise: float) -> bytes:
    import numpy as np
    from PIL import Image, ImageFilter

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")

    if rotation:
        img = img.rotate(
            rotation,
            resample=Image.BICUBIC,
            expand=False,
            fillcolor=(255, 255, 255),
        )

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    if noise:
        arr = np.asarray(img).astype(np.int16)
        noise_arr = np.random.normal(0, noise, arr.shape).astype(np.int16)
        arr = np.clip(arr + noise_arr, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _rasterize(
    src_path: Path,
    out_path: Path,
    dpi: int,
    rotation: float,
    blur: float,
    noise: float,
    progress=None,
) -> None:
    import pymupdf

    src = pymupdf.open(src_path)
    try:
        out = pymupdf.open()
        try:
            total = src.page_count
            for index, page in enumerate(src, start=1):
                if progress is not None:
                    progress(index, total)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                degraded_png = _degrade(pix.tobytes("png"), rotation, blur, noise)
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(page.rect, stream=degraded_png)

            # Pusty słownik nic nie czyści; nowy dokument i tak dostaje producer PyMuPDF.
            out.set_metadata(
                {
                    "format": "",
                    "title": "",
                    "author": "",
                    "subject": "",
                    "keywords": "",
                    "creator": "",
                    "producer": "",
                    "creationDate": "",
                    "modDate": "",
                    "trapped": "",
                }
            )
            out.del_xml_metadata()
            out.save(out_path, garbage=4, deflate=True)
        finally:
            out.close()
    finally:
        src.close()


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
        self._busy = False

        root.title("Scal PDF")
        root.minsize(560, 460)
        root.geometry("680x520")
        root.protocol("WM_DELETE_WINDOW", self._on_close)

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

        raster = tk.Frame(bottom)
        raster.pack(fill=tk.X, pady=(0, 8))

        self.raster_var = tk.BooleanVar(value=False)
        self.raster_check = tk.Checkbutton(
            raster,
            text="Rasteryzuj (utrudnij odczyt)",
            variable=self.raster_var,
            command=self._toggle_raster,
            anchor="w",
        )
        self.raster_check.pack(fill=tk.X)

        params = tk.Frame(raster)
        params.pack(fill=tk.X, pady=(4, 0))

        self.dpi_var = tk.StringVar(value="250")
        self.angle_var = tk.StringVar(value="0.5")
        self.blur_var = tk.StringVar(value="0.6")
        self.noise_var = tk.StringVar(value="4")
        self._raster_spins = [
            self._add_spin(params, "DPI", self.dpi_var, 72, 600, 1),
            self._add_spin(params, "Kąt °", self.angle_var, -5, 5, 0.1, "%0.1f"),
            self._add_spin(params, "Rozmycie", self.blur_var, 0, 3, 0.1, "%0.1f"),
            self._add_spin(params, "Szum", self.noise_var, 0, 30, 1),
        ]
        self._toggle_raster()

        self.status_var = tk.StringVar()
        self.status_label = tk.Label(bottom, textvariable=self.status_var, anchor="w", justify=tk.LEFT)

        self.path_var = tk.StringVar()
        self.path_label = tk.Label(bottom, textvariable=self.path_var, anchor="w", justify=tk.LEFT)
        self.path_label.pack(fill=tk.X, pady=(0, 8))

        buttons = tk.Frame(bottom)
        buttons.pack(side=tk.RIGHT)
        self.save_button = tk.Button(buttons, text="Zapisz", width=10, command=self._save)
        self.save_button.pack(side=tk.LEFT, padx=(0, 6))
        self.close_button = tk.Button(buttons, text="Zamknij", width=10, command=self._on_close)
        self.close_button.pack(side=tk.LEFT)

        self._refresh_path()
        self._register_drop_tree(root)
        outer.bind("<Configure>", self._on_resize)
        self.listbox.bind("<Delete>", lambda _event: self._remove())

    def _on_resize(self, event: tk.Event) -> None:
        wrap = max(event.width - 8, 200)
        self.path_label.configure(wraplength=wrap)
        self.status_label.configure(wraplength=wrap)

    def _add_spin(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        from_: float,
        to: float,
        increment: float,
        fmt: str | None = None,
    ) -> tk.Spinbox:
        box = tk.Frame(parent)
        box.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(box, text=label).pack(side=tk.LEFT)
        options: dict = {
            "textvariable": variable,
            "from_": from_,
            "to": to,
            "increment": increment,
            "width": 6,
        }
        if fmt is not None:
            options["format"] = fmt
        spin = tk.Spinbox(box, **options)
        spin.pack(side=tk.LEFT, padx=(4, 0))
        return spin

    def _toggle_raster(self) -> None:
        if self._busy:
            return
        state = tk.NORMAL if self.raster_var.get() else tk.DISABLED
        for spin in self._raster_spins:
            spin.configure(state=state)

    def _on_close(self) -> None:
        if self._busy:
            return
        self.root.destroy()

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.save_button.configure(state=state)
        self.close_button.configure(state=state)
        self.raster_check.configure(state=state)
        spin_state = tk.DISABLED if busy or not self.raster_var.get() else tk.NORMAL
        for spin in self._raster_spins:
            spin.configure(state=spin_state)
        self.status_var.set(status)
        if status:
            if not self.status_label.winfo_ismapped():
                self.status_label.pack(fill=tk.X, before=self.path_label)
        elif self.status_label.winfo_ismapped():
            self.status_label.pack_forget()

    def _read_raster_params(self) -> tuple[int, float, float, float]:
        try:
            dpi_raw = float(self.dpi_var.get().strip().replace(",", "."))
            angle = float(self.angle_var.get().strip().replace(",", "."))
            blur = float(self.blur_var.get().strip().replace(",", "."))
            noise = float(self.noise_var.get().strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Podaj liczby w polach DPI, kąta, rozmycia i szumu.") from exc
        if not dpi_raw.is_integer():
            raise ValueError("DPI musi być liczbą całkowitą.")
        dpi = int(dpi_raw)

        if not 72 <= dpi <= 600:
            raise ValueError("DPI musi być w zakresie 72–600.")
        if not -5 <= angle <= 5:
            raise ValueError("Kąt musi być w zakresie od -5 do 5 stopni.")
        if not 0 <= blur <= 3:
            raise ValueError("Rozmycie musi być w zakresie 0–3.")
        if not 0 <= noise <= 30:
            raise ValueError("Szum musi być w zakresie 0–30.")
        return dpi, angle, blur, noise

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

    def _report(self, message: str, exc: BaseException | None = None, tb: str | None = None) -> None:
        log_note = ""
        if exc is not None:
            try:
                log_path = base_dir / "scal_pdf_gui-error.log"
                log_path.write_text(tb if tb is not None else traceback.format_exc(), encoding="utf-8")
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

        raster = bool(self.raster_var.get())
        raster_params: tuple[int, float, float, float] | None = None
        if raster:
            try:
                raster_params = self._read_raster_params()
            except ValueError as exc:
                messagebox.showerror("Scal PDF", str(exc), parent=self.root)
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

        raster_output: Path | None = None
        if raster and raster_params is not None:
            raster_output = output.with_name(f"{output.stem}-raster{output.suffix}")
            if self._is_input_file(raster_output):
                messagebox.showerror(
                    "Scal PDF",
                    "Nie można zapisać pliku rastrowego do pliku, który jest jednym z plików wejściowych.",
                    parent=self.root,
                )
                return
            if raster_output.exists():
                overwrite = messagebox.askyesno(
                    "Scal PDF",
                    f"Plik już istnieje:\n{raster_output}\n\nNadpisać?",
                    parent=self.root,
                )
                if not overwrite:
                    raster_output = None
                    raster_params = None

        try:
            ok = self._merge_to(output)
        except Exception as exc:
            self._report("Nieoczekiwany błąd podczas zapisu.", exc)
            return
        if not ok:
            return

        if raster_output is None or raster_params is None:
            messagebox.showinfo("Scal PDF", f"Gotowe!\n\nUtworzono:\n{output}", parent=self.root)
            return

        self._start_raster(output, raster_output, raster_params)

    def _start_raster(
        self,
        output: Path,
        raster_output: Path,
        params: tuple[int, float, float, float],
    ) -> None:
        dpi, angle, blur, noise = params
        self._set_busy(True, "Rasteryzacja: przygotowanie…")
        events: queue.Queue = queue.Queue()

        def progress(current: int, total: int) -> None:
            events.put(("progress", current, total))

        def worker() -> None:
            try:
                _rasterize(output, raster_output, dpi, angle, blur, noise, progress=progress)
            except Exception as exc:
                events.put(("error", exc, traceback.format_exc()))
                return
            events.put(("ok",))

        threading.Thread(target=worker, daemon=True).start()
        self._poll_raster(events, output, raster_output)

    def _poll_raster(self, events: queue.Queue, output: Path, raster_output: Path) -> None:
        try:
            while True:
                msg = events.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.status_var.set(f"Rasteryzacja: strona {msg[1]}/{msg[2]}")
                elif kind == "error":
                    self._on_raster_fail(msg[1], msg[2], output)
                    return
                elif kind == "ok":
                    self._on_raster_ok(output, raster_output)
                    return
        except queue.Empty:
            pass
        self.root.after(100, lambda: self._poll_raster(events, output, raster_output))

    def _on_raster_fail(self, exc: BaseException, tb: str, output: Path) -> None:
        self._set_busy(False)
        self._report(
            f"Błąd podczas rasteryzacji.\n\nScalony plik został zapisany:\n{output}",
            exc,
            tb=tb,
        )

    def _on_raster_ok(self, output: Path, raster_output: Path) -> None:
        self._set_busy(False)
        messagebox.showinfo(
            "Scal PDF",
            f"Gotowe!\n\nUtworzono:\n{output}\n{raster_output}",
            parent=self.root,
        )

    def _merge_to(self, output: Path) -> bool:
        merged = pikepdf.Pdf.new()
        try:
            for pdf in self.files:
                try:
                    with pikepdf.open(pdf) as src:
                        merged.pages.extend(src.pages)
                except Exception as exc:
                    self._report(f"Błąd podczas dodawania:\n{pdf.name}", exc)
                    return False
            try:
                merged.save(
                    output,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                )
            except Exception as exc:
                self._report("Błąd podczas zapisu wynikowego PDF.", exc)
                return False
        finally:
            merged.close()

        return True


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
