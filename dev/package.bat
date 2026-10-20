@echo off
setlocal DisableDelayedExpansion
rem Preserve literal exclamation marks in the directory even under cmd /v:on.
rem Run from any working directory; all project paths come from this batch file.
rem Try the Windows Python launcher first, then the python command on PATH.
rem The version probe only inspects the interpreter and does not install anything.
set "PACKAGE_PYTHON="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
    if not errorlevel 1 set "PACKAGE_PYTHON=py -3"
)
if not defined PACKAGE_PYTHON (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
        if not errorlevel 1 set "PACKAGE_PYTHON=python"
    )
)
if not defined PACKAGE_PYTHON (
    echo ERROR: Python 3.9 or newer is required. Install Python and try again.
    exit /b 1
)

rem Ship the self-contained skill first, then the separate developer source ZIP.
rem Both helpers validate contents before replacing their distribution files.
%PACKAGE_PYTHON% "%~dp0tools\package_skill.py"
if errorlevel 1 (
    echo ERROR: Skill packaging failed. See the error above.
    exit /b 1
)

rem Quoting the helper path supports spaces and Japanese directory names.
%PACKAGE_PYTHON% "%~dp0tools\package_release.py"
set "PACKAGE_EXIT_CODE=%ERRORLEVEL%"
if not "%PACKAGE_EXIT_CODE%"=="0" echo ERROR: Packaging failed. See the error above.
exit /b %PACKAGE_EXIT_CODE%
