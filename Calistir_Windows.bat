@echo off
chcp 65001 >nul
title Ada-Soft DDM — Petlebi Kolay IK - DIA ERP Entegrasyon
cd /d "%~dp0"
if exist "dist\Petlebi-KolayIK-DIA\Petlebi-KolayIK-DIA.exe" (
  start "" "dist\Petlebi-KolayIK-DIA\Petlebi-KolayIK-DIA.exe"
  exit /b 0
)
python desktop_launcher.py
pause
