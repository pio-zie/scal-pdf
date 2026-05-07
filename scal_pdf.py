from pathlib import Path
from PyPDF2 import PdfMerger
import sys
import re

base_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

pdf_dir = base_dir / "pdf"

if not pdf_dir.exists():
    print("Brak folderu 'pdf'")
    input("\nENTER aby zamknąć...")
    sys.exit()

if not pdf_dir.is_dir():
    print("'pdf' istnieje, ale nie jest folderem")
    input("\nENTER aby zamknąć...")
    sys.exit()

def _natural_key(p: Path):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", p.name)]

pdf_files = sorted(pdf_dir.glob("*.pdf"), key=_natural_key)

if not pdf_files:
    print("Brak plików PDF w folderze 'pdf'")
    input("\nENTER aby zamknąć...")
    sys.exit()

output_dir = base_dir / "pdf-calosc"
output_dir.mkdir(parents=True, exist_ok=True)
output_file = output_dir / "pdf-scalony.pdf"

merger = PdfMerger()

for pdf in pdf_files:
    print(f"Dodawanie: {pdf.name}")
    try:
        merger.append(str(pdf))
    except Exception as e:
        print(f"\nBłąd podczas dodawania: {pdf.name}")
        print(f"Szczegóły: {e}")
        merger.close()
        input("\nENTER aby zamknąć...")
        sys.exit(1)

merger.write(str(output_file))
merger.close()

print("\nGotowe!")
print(f"Utworzono:\n{output_file}")

input("\nENTER aby zamknąć...")