@echo off
setlocal

rem Lance MARK LV depuis le dossier du projet, quel que soit le dossier courant.
cd /d "%~dp0"

rem Prefer the project's virtual environment, but only if its base Python still
rem exists. Copied or moved virtual environments can contain a stale Python path.
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        echo Lancement avec l'environnement virtuel .venv...
        "%~dp0.venv\Scripts\python.exe" main.py
        goto :report_result
    )
    echo Attention : l'environnement .venv est inutilisable, Python de base introuvable.
    echo Recherche d'une autre installation Python...
)

rem Prefer the versions the project has been tested on: 3.11, 3.12, then 3.13.
rem Python 3.14 is an untested fallback; setup.py allows newer versions to try.
where py >nul 2>&1
if not errorlevel 1 (
    for %%V in (3.13 3.12 3.11) do (
        py -%%V -c "import sys; raise SystemExit(0)" >nul 2>&1
        if not errorlevel 1 (
            echo Lancement avec Python %%V...
            py -%%V main.py
            goto :report_result
        )
    )
    py -3.14 -c "import sys; raise SystemExit(0)" >nul 2>&1
    if not errorlevel 1 (
        echo Attention : Python 3.14 n'est pas dans la plage testee.
        echo Lancement avec Python 3.14...
        py -3.14 main.py
        goto :report_result
    )
)

rem Final fallback: accept a Python in PATH at or above the project's minimum.
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        echo Lancement avec Python de PATH :
        python --version
        python main.py
        goto :report_result
    )
)

echo.
echo MARK LV a besoin de Python 3.11 a 3.13.
echo Installez une version supportee ou creez un environnement virtuel :
echo   py -3.13 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
echo Puis relancez ce script ou ouvrez une invite avec :
echo   .venv\Scripts\python.exe main.py
pause
exit /b 1

:report_result
if errorlevel 1 (
    echo.
    echo MARK LV s'est arrete avec le code %errorlevel%.
    echo Verifiez que Python et les dependances sont installes.
    echo Si besoin :
    echo   py -3.13 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
)

endlocal