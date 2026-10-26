@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm --onefile --windowed --collect-all tkinterdnd2 --name scal_pdf_gui scal_pdf_gui.py
echo.
echo Gotowe: dist\scal_pdf_gui.exe
pause
