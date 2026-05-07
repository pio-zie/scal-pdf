from pathlib import Path
import sys
import re
import traceback
import shutil

import pikepdf

base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

def _pause_exit(code: int = 0):
    input("\nENTER aby zamknąć...")
    raise SystemExit(code)

def _fatal(message: str, exc: BaseException | None = None, code: int = 1):
    print(message)
    if exc is not None:
        print("\nSzczegóły błędu:")
        print(str(exc))
        try:
            log_path = base_dir / "scal_pdf-error.log"
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            print(f"\nZapisano log:\n{log_path}")
        except Exception:
            # jeśli zapis loga się nie uda, i tak pokażemy błąd w konsoli
            pass
    _pause_exit(code)

def main():
    pdf_dir = base_dir / "pdf"

    if not pdf_dir.exists():
        _fatal("Brak folderu 'pdf'", code=1)

    if not pdf_dir.is_dir():
        _fatal("'pdf' istnieje, ale nie jest folderem", code=1)

    def _natural_key(p: Path):
        return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", p.name)]

    pdf_files = sorted(pdf_dir.glob("*.pdf"), key=_natural_key)

    if not pdf_files:
        _fatal("Brak plików PDF w folderze 'pdf'", code=1)

    output_dir = base_dir / "pdf-calosc"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / pdf_files[0].name

    merged = pikepdf.Pdf.new()

    for pdf in pdf_files:
        print(f"Dodawanie: {pdf.name}")
        try:
            with pikepdf.open(pdf) as src:
                merged.pages.extend(src.pages)
        except Exception as e:
            _fatal(f"\nBłąd podczas dodawania: {pdf.name}", e, code=1)

    try:
        merged.save(
            output_file,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
        )
    except Exception as e:
        _fatal("\nBłąd podczas zapisu wynikowego PDF", e, code=1)

    try:
        podp_file = output_dir / f"{output_file.stem}-podp{output_file.suffix}"
        shutil.copy2(output_file, podp_file)
    except Exception as e:
        _fatal("\nBłąd podczas kopiowania pliku '-podp' do 'pdf-calosc'", e, code=1)

    print("\nGotowe!")
    print(f"Utworzono:\n{output_file}")
    print(f"Utworzono kopię '-podp' w 'pdf-calosc':\n{podp_file}")

    _pause_exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fatal("Nieoczekiwany błąd aplikacji.", e, code=1)