@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m PyInstaller --clean --noconfirm scal_pdf.spec
echo.
echo Gotowe: dist\scal_pdf.exe
pause
