@echo off
cd /d "%~dp0"

echo.
echo === PDF to JPG Converter - Build EXE ===
echo.

:: Step 1: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    echo Install Python from https://python.org
    echo Check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [1/5] Python OK

:: Step 2: Check source file
if not exist "pdf_to_jpg_gui.py" (
    echo ERROR: pdf_to_jpg_gui.py not found!
    echo Put build_exe.bat and pdf_to_jpg_gui.py in the same folder.
    pause
    exit /b 1
)
echo [2/5] pdf_to_jpg_gui.py found

:: Step 3: Install dependencies
echo [3/5] Installing dependencies...
pip install --upgrade pyinstaller pdf2image pillow --quiet
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies!
    pause
    exit /b 1
)
echo [3/5] Dependencies OK

:: Step 4: Download Poppler if needed
echo [4/5] Preparing Poppler...
set "POPPLER_BUNDLE=%~dp0poppler_bundle"
set "POPPLER_BIN="

if exist "%POPPLER_BUNDLE%" (
    for /r "%POPPLER_BUNDLE%" %%f in (pdftoppm.exe) do set "POPPLER_BIN=%%~dpf"
)

if defined POPPLER_BIN (
    echo [4/5] Poppler already downloaded
    goto :BUILD
)

echo Downloading Poppler...
python download_poppler.py "%POPPLER_BUNDLE%"
if %errorlevel% neq 0 (
    echo ERROR: Failed to download Poppler!
    echo Manual download: https://github.com/oschwartz10612/poppler-windows/releases
    echo Extract to: %POPPLER_BUNDLE%
    echo Then run build_exe.bat again.
    pause
    exit /b 1
)

for /r "%POPPLER_BUNDLE%" %%f in (pdftoppm.exe) do set "POPPLER_BIN=%%~dpf"

:BUILD
:: Remove trailing backslash
set "PB=%POPPLER_BIN%"
if "%PB:~-1%"=="\" set "PB=%PB:~0,-1%"
echo [4/5] Poppler OK: %PB%

:: Step 5: Build EXE
echo [5/5] Building EXE (1-3 minutes)...
echo.

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "PDF_to_JPG" ^
    --add-data "%PB%;poppler_bin" ^
    --hidden-import=PIL._tkinter_finder ^
    --hidden-import=pdf2image.pdf2image ^
    --hidden-import=pdf2image.exceptions ^
    --collect-all pdf2image ^
    --collect-all PIL ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module pandas ^
    --exclude-module scipy ^
    pdf_to_jpg_gui.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed! See output above.
    pause
    exit /b 1
)

:: Cleanup
if exist "build" rmdir /s /q "build"
if exist "PDF_to_JPG.spec" del /q "PDF_to_JPG.spec"

echo.
echo ===================================
echo  DONE! EXE: dist\PDF_to_JPG.exe
echo  Poppler is bundled inside EXE.
echo  Copy EXE to any Windows PC.
echo ===================================
echo.
explorer dist
pause
