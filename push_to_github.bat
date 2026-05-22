@echo off
REM Bu .bat dosyası PowerShell scripti execution policy engelini aşarak çalıştırır.
REM Çift tıklayarak ya da "push_to_github.bat" yazarak kullanabilirsin.

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_to_github.ps1"
pause
