@echo off
chcp 65001 >nul
title Ada-Soft DDM — Petlebi Kolay IK - DIA ERP (.exe ve Setup Derleyici)
echo ============================================================================
echo   Ada-Soft Dijital Donusum Merkezi (www.adasoftddm.com)
echo   Petlebi Kolay IK ^<--^> DIA Cloud ERP Windows .exe ve Kurulum Sihirbazi Derleme
echo ============================================================================
echo.

cd /d "%~dp0"

echo [1/3] Python bagimliliklari ve PyInstaller yukleniyor...
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller pillow

echo.
echo [2/3] Bagimsiz Windows .exe uygulamasi (Petlebi-KolayIK-DIA.exe) derleniyor...
pyinstaller --noconfirm --onedir --windowed ^
  --name "Petlebi-KolayIK-DIA" ^
  --icon "src/static/adasoftlogo.ico" ^
  --add-data "src;src" ^
  --add-data "version.json;." ^
  --hidden-import "uvicorn.logging" ^
  --hidden-import "uvicorn.loops" ^
  --hidden-import "uvicorn.loops.auto" ^
  --hidden-import "uvicorn.protocols" ^
  --hidden-import "uvicorn.protocols.http" ^
  --hidden-import "uvicorn.protocols.http.auto" ^
  --hidden-import "uvicorn.lifespan" ^
  --hidden-import "uvicorn.lifespan.on" ^
  desktop_launcher.py

if exist ".env" (
  copy /Y ".env" "dist\Petlebi-KolayIK-DIA\.env" >nul
)
copy /Y "version.json" "dist\Petlebi-KolayIK-DIA\version.json" >nul
xcopy /E /I /Y "src" "dist\Petlebi-KolayIK-DIA\src" >nul

echo.
echo [3/3] Inno Setup Kurulum Sihirbazi (Petlebi-KolayIK-DIA-Setup.exe) kontrol ediliyor...
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" (
  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
  echo.
  echo [BASARILI] Kurulum Sihirbazi hazir: dist_installer\Petlebi-KolayIK-DIA-Setup.exe
) else (
  echo [BILGI] Inno Setup 6 bulunamadi. Taşınabilir .exe klasoru hazir: dist\Petlebi-KolayIK-DIA\Petlebi-KolayIK-DIA.exe
)

echo.
pause
