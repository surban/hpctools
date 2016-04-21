@echo off
powershell -NonInteractive -NoLogo -Command "& { Import-Module Cluster; Start-OnBestDevice '%1' '%2' '%3' '%4' '%5' '%6' '%7' '%8'; exit $LASTEXITCODE }"

