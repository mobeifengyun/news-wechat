@echo off
REM Auto catchup on logon: wait for network, then scan & self-heal.
REM Registered as Windows Scheduled Task "BaoJianShuo-Catchup".
set PY=C:\Users\jhon\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set DIR=C:\Users\jhon\WorkBuddy\2026-07-29-10-24-16\news_wechat
cd /d "%DIR%"
"%PY%" "%DIR%\catchup.py" --days 7 --wait-net 180 >> "%DIR%\output\_catchup.log" 2>&1
exit /b 0
