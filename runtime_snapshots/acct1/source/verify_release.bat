@echo off
rem Double-click: release verification (full checks + rollback drill + read-only check of the running bot).
call "%~dp0verify.bat" release
