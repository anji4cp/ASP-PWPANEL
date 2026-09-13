@echo off
setlocal
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (echo Microsoft .NET Framework C# compiler not found.& exit /b 1)
if not exist "%~dp0bin" mkdir "%~dp0bin"
set /p "APP_VERSION="<"%~dp0VERSION"
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu /out:"%~dp0bin\ASP-PWPanel-Desktop-%APP_VERSION%.exe" /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll "%~dp0src\*.cs"
if errorlevel 1 exit /b 1
echo Built ASP-PWPanel-Desktop-%APP_VERSION%.exe
