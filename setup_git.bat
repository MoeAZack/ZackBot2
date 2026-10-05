@echo off
rem One-time: private Git history for this folder (tags v2.2 ... v3.2-rc2) from ..\ZackBot2_git\zackbot_v3.2-rc2.1.bundle.
rem Nothing is uploaded anywhere. Needs Git for Windows (https://git-scm.com/download/win).
rem If Windows does not let git.exe create the hidden .git folder in Documents (usually "Controlled folder access" /
rem ransomware protection), the history is kept in %LOCALAPPDATA%\ZackBot\history.git instead and ..\ZackBot2_git\zbgit.bat
rem runs git against this folder - your files stay exactly where they are.
setlocal
set SHOW=git
cd /d "%~dp0"
set BUNDLE=%~dp0..\ZackBot2_git\zackbot_v3.2-rc2.1.bundle
set GD=%LOCALAPPDATA%\ZackBot\history.git
set WRAP=%~dp0..\ZackBot2_git\zbgit.bat
where git >nul 2>nul || (echo Git is not installed. Install Git for Windows, then run this again. & pause & exit /b 1)
git --version
if not exist "%BUNDLE%" (echo Missing %BUNDLE% & pause & exit /b 1)
for /f %%c in ('powershell -NoProfile -Command "try{(Get-MpPreference).EnableControlledFolderAccess}catch{'?'}"') do set CFA=%%c
echo Windows Controlled folder access: %CFA%   (0 = off, 1 = on, 2 = audit)
if exist ".git\HEAD" (set G=git& echo This folder is already a Git repository.& goto verify)
if exist "%GD%\HEAD" goto external_ready
if exist ".git" ren ".git" ".git_failed_%RANDOM%"

echo.
echo [1] Creating the repository in this folder...
git init "%CD%"
if exist ".git\HEAD" (set G=git& goto fetch)
if exist ".git" ren ".git" ".git_failed_%RANDOM%"
echo.
echo     Windows did not let Git write into this folder (see the message above).
echo [2] Keeping the history in %GD% instead.
git --git-dir="%GD%" init || goto fail
:external_ready
set G=git --git-dir="%GD%" --work-tree="%CD%"
set SHOW=..\ZackBot2_git\zbgit.bat
> "%WRAP%" echo @git --git-dir="%GD%" --work-tree="%CD%" %%*
echo     Use ..\ZackBot2_git\zbgit.bat instead of "git" for this folder, e.g.  ..\ZackBot2_git\zbgit.bat log --oneline
:fetch
%G% symbolic-ref HEAD refs/heads/master || goto fail
%G% config core.autocrlf false
%G% config core.filemode false
%G% bundle verify "%BUNDLE%" || (echo The history bundle is damaged. & goto fail)
%G% fetch -q "%BUNDLE%" "+refs/heads/master:refs/remotes/bundle/master" "+refs/tags/*:refs/tags/*" || goto fail
%G% reset -q --mixed v3.2-rc2 || goto fail
:verify
echo.
echo Tags (versions):
%G% tag
echo.
echo Last commits:
%G% log --oneline -6
echo.
echo Files that differ from the last commit (should be none):
%G% status --short
echo.
echo To see exactly what changed between versions:  %SHOW% diff --stat v3.1 v3.2-rc2
pause
exit /b 0
:fail
echo Git setup failed - nothing in your files was changed. Copy the messages above and send them to Claude.
pause
exit /b 1
