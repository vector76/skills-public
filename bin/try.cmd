@echo off
rem Start Claude Code in the CURRENT directory with this repo's plugin loaded
rem from the working tree. Run it from the project to try an edit in, by its
rem full path; extra arguments go to claude. See bin/try.sh for details.
claude --plugin-dir "%~dp0..\plugins\jamie" %*
