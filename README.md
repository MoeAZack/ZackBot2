# ZackBot 2 — desktop app

Install / update: double-click **build_app.bat**. It stops the old bot, builds ZackBot.exe into
%LOCALAPPDATA%\ZackBot\app, and puts a ZackBot shortcut on your Desktop and Start menu.

Everything (keys, settings, logs, trades, backtests, candle cache) lives in %LOCALAPPDATA%\ZackBot.
Your existing keys are imported automatically from Documents\ZackBot\config.env.

Tabs: Dashboard · Strategies (profiles + slots) · Risk · Coins · Signals · Backtest Lab · Research · Logs · Settings.
Closing the window keeps the bot trading in the background (switch in Risk tab); reopen with the shortcut.
"Quit ZackBot" in Settings stops it. Exchange stops stay on Binance either way.
