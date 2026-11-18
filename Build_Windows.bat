@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo       Javad PC Doctor - Build EXE
echo ========================================
where py >nul 2>nul
if errorlevel 1 (echo Python 3.10+ is required. Install Python from python.org and enable Add to PATH.& pause& exit /b 1)
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --onefile --name JavadPCDoctor --icon assets\JavadPCDoctor.ico --add-data "assets;assets" javad_pc_doctor.py
if errorlevel 1 (echo Build failed.& pause& exit /b 1)
if not exist USB_Ready mkdir USB_Ready
copy /Y dist\JavadPCDoctor.exe USB_Ready\
if not exist USB_Ready\Reports mkdir USB_Ready\Reports
echo Build complete. Copy the USB_Ready folder to your flash drive.
pause
