@echo off
rem One-time: turn this folder into a private Git repository with the full ZackBot history (v2.2 ... v3.2-rc2),
rem from ..\ZackBot2_git\zackbot_v3.2-rc2.bundle. Nothing is uploaded anywhere. Needs Git for Windows (https://git-scm.com/download/win).
setlocal
cd /d "%~dp0"
where git >nul 2>nul || (echo Git is not installed. Install Git for Windows, then run this again. & pause & exit /b 1)
if exist ".git" (echo This folder is already a Git repository. & goto verify)
set BUNDLE=..\ZackBot2_git\zackbot_v3.2-rc2.bundle
if not exist "%BUNDLE%" (echo Missing %BUNDLE% & pause & exit /b 1)
git init -q -b master || goto fail
git bundle verify "%BUNDLE%" || (echo The history bundle is damaged. & goto fail)
git config core.autocrlf false
git config core.filemode false
git fetch -q "%BUNDLE%" "+refs/heads/master:refs/remotes/bundle/master" "+refs/tags/*:refs/tags/*" || goto fail
git reset -q --mixed v3.2-rc2 || goto fail
:verify
echo.
echo Tags (versions):
git tag
echo.
echo Last commits:
git log --oneline -6
echo.
echo Files that differ from the last commit (should be none):
git status --short
echo.
echo To see exactly what changed between versions:  git diff --stat v3.1 v3.2-rc2
pause
exit /b 0
:fail
echo Git setup failed - nothing in your files was changed.
pause
exit /b 1
