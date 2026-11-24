====================================================================
  SignalDesk - Windows Desktop Application & Installer Package
====================================================================

OPTION 1 - QUICK WINDOWS INSTALL (Recommended, 1-click)
--------------------------------------------------------------------
1. Extract this ZIP archive to any folder on your Windows 10/11 PC.
2. Double-click:  Install_SignalDesk.bat
3. The installer will:
   - Copy SignalDesk into %LOCALAPPDATA%\SignalDesk
   - Set up a local Python virtual environment automatically
   - Create a Desktop shortcut & Start Menu entry with the SignalDesk icon
   - Launch SignalDesk in a standalone desktop window (no Node.js needed!)

To uninstall later:
   Open Start Menu -> SignalDesk -> Uninstall SignalDesk.


OPTION 2 - BUILD A SINGLE-FILE SignalDesk.exe & SignalDesk_Setup.exe
--------------------------------------------------------------------
If you want to distribute a standalone .exe that runs on PCs without
Python installed:
1. Extract this ZIP on Windows.
2. Double-click:  Build_Windows_EXE.bat
   -> Produces dist\SignalDesk.exe (standalone Windows desktop app)
3. (Optional) If Inno Setup is installed, it also compiles:
   -> Output\SignalDesk_Setup.exe (standard Windows Setup Wizard)


OPTIONAL: FREE AI CHART ANALYSIS LOCALLY
--------------------------------------------------------------------
To enable the AI Analysis module in the local Windows desktop app,
set your free Gemini API key as a Windows User Environment Variable:
   setx GEMINI_API_KEY "YOUR_KEY_HERE"
and restart SignalDesk.


QUANTITATIVE STRATEGY ENGINE & CONFLUENCE
--------------------------------------------------------------------
The application includes the Pandas & Pandas-TA Quantitative Engine:
- Mean Reversion (Bollinger Bands 20,2 + RSI 14 + Pinbar rejection)
- Trend Pullback (EMA 50/200 + Stochastic 14,3,3 crossover)
- Momentum Breakout (MACD 12,26,9 + ADX 14 > 25 trend strength)
- Price Action S/R (Williams Fractals + Bullish/Bearish Engulfing)
- ATR Volatility Gating (filters flat markets < 0.55x and spikes > 2.6x)
- Strict 0-5 second candle open execution rule

Stubay learning and settings are stored under %LOCALAPPDATA%\SignalDesk Data.
Reinstall or download again using the same Windows account to reuse that data.
The ZIP contains application files only.
