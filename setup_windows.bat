@echo off
py --version
py -m pip install -r requirements.txt
echo.
echo Setup finished.
echo Copy .env.example to .env if you have not already done so.
echo Then edit .env and add your Discord bot token and server ID.
echo.
pause
