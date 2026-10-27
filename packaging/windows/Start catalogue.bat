@echo off
rem Starts the data catalogue in your browser. Your catalogue and settings are kept
rem in %USERPROFILE%\.pdcat, so replacing this folder with a newer version keeps them.
setlocal
set "HERE=%~dp0"
if not exist "%HERE%env\.unpacked" (
    echo Getting ready for first use. This takes a minute and only happens once...
    "%HERE%env\Scripts\conda-unpack.exe"
    if errorlevel 1 (
        echo Setting up failed. Try unzipping the folder somewhere else, such as your Documents folder.
        pause
        exit /b 1
    )
    echo done> "%HERE%env\.unpacked"
)
echo Starting the catalogue. Leave this window open while you use it; close it to stop.
"%HERE%env\python.exe" -m pdcat.cli ui
if errorlevel 1 pause
