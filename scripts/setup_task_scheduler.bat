@echo off
REM ============================================================================
REM Setup Windows Task Scheduler for FactorLab India orchestrators
REM Run this once as Administrator.
REM
REM Creates two scheduled tasks:
REM   1. factlab_india_premarket — daily at 06:00 IST
REM   2. factlab_india_hourly    — hourly 10:15-15:15 + 15:28 IST
REM
REM The 5-min poller (factlab_india_5min) is a long-running process,
REM not suitable for Task Scheduler. Run it manually or as a service.
REM ============================================================================

SET CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat
SET PROJECT_DIR=%~dp0..

echo ============================================================
echo FactorLab India - Task Scheduler Setup
echo ============================================================
echo.
echo IMPORTANT: Adjust /st times based on your system clock timezone.
echo   If Windows clock is IST: use times as-is (06:00, 10:15, etc.)
echo   If Windows clock is UTC: subtract 5:30 (00:30, 04:45, etc.)
echo.

REM ── Pre-market task ─────────────────────────────────────────────────────────
SET TASK1=FactorLab-India-PreMarket
schtasks /delete /tn "%TASK1%" /f >nul 2>&1

schtasks /create ^
  /tn "%TASK1%" ^
  /sc DAILY ^
  /st 06:00 ^
  /tr "\"%CONDA_BAT%\" run -n factorlab python \"%PROJECT_DIR%\scripts\factlab_india_premarket.py\"" ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo [OK] %TASK1% — daily at 06:00
) ELSE (
    echo [FAIL] %TASK1% — run as Administrator
)

REM ── Hourly tasks ────────────────────────────────────────────────────────────
REM Task Scheduler doesn't support "hourly between X and Y" natively.
REM We create one task that repeats every hour with a duration of 6 hours,
REM starting at 10:15. Plus a separate one-off at 15:28 for the close sweep.

SET TASK2=FactorLab-India-Hourly
schtasks /delete /tn "%TASK2%" /f >nul 2>&1

schtasks /create ^
  /tn "%TASK2%" ^
  /sc DAILY ^
  /st 10:15 ^
  /ri 60 ^
  /du 05:15 ^
  /tr "\"%CONDA_BAT%\" run -n factorlab python \"%PROJECT_DIR%\scripts\factlab_india_hourly.py\" --universe demo" ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo [OK] %TASK2% — hourly 10:15-15:15
) ELSE (
    echo [FAIL] %TASK2% — run as Administrator
)

SET TASK3=FactorLab-India-CloseSwoop
schtasks /delete /tn "%TASK3%" /f >nul 2>&1

schtasks /create ^
  /tn "%TASK3%" ^
  /sc DAILY ^
  /st 15:28 ^
  /tr "\"%CONDA_BAT%\" run -n factorlab python \"%PROJECT_DIR%\scripts\factlab_india_hourly.py\" --universe demo" ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo [OK] %TASK3% — daily at 15:28 (close sweep)
) ELSE (
    echo [FAIL] %TASK3% — run as Administrator
)

echo.
echo ============================================================
echo Setup complete. Commands to manage tasks:
echo.
echo   Test pre-market:  schtasks /run /tn "%TASK1%"
echo   Test hourly:      schtasks /run /tn "%TASK2%"
echo   View all:         schtasks /query /tn "FactorLab*" /v
echo   Delete all:       schtasks /delete /tn "%TASK1%" /f
echo                     schtasks /delete /tn "%TASK2%" /f
echo                     schtasks /delete /tn "%TASK3%" /f
echo ============================================================
