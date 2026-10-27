@echo off
rem Refresh the catalogue: scan everything in the config, then measure the tables
rem approved on the app's SQL scanning page. Output is added to scan.log.
rem
rem Change the two lines below if your conda or config is somewhere else.
rem   CONDA_BASE: run "conda info --base" in Anaconda Prompt to find it.
rem   PDCAT_CONFIG: the config.toml your catalogue uses.
set "CONDA_BASE=%USERPROFILE%\miniconda3"
set "PDCAT_CONFIG=%USERPROFILE%\.pdcat\config.toml"

set "LOG=%USERPROFILE%\.pdcat\scan.log"
call "%CONDA_BASE%\Scripts\activate.bat" pdcat
if errorlevel 1 (
    echo %date% %time% could not activate the pdcat environment from %CONDA_BASE% >> "%LOG%"
    exit /b 2
)

echo. >> "%LOG%"
echo ===== %date% %time% scan ===== >> "%LOG%"
pdcat scan >> "%LOG%" 2>&1
set "SCAN_RESULT=%errorlevel%"

echo ===== %date% %time% profile --queue ===== >> "%LOG%"
pdcat profile --queue >> "%LOG%" 2>&1

exit /b %SCAN_RESULT%
