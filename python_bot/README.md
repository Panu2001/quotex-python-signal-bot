# Quotex Multi-Strategy Python Signal Bot

A complete, modular Python 3.10+ trading signal engine for Quotex binary options implementing **11 technical & price-action strategies** with weighted confluence scoring, Martingale/compounding risk management, historical backtesting, Telegram alert broadcasting, and Quotex WebSocket SSID execution.

## How to Connect to Quotex

Quotex does not provide official public API keys. You can connect this bot in two ways:

### Option 1 — Signal-Only + Telegram Alerts (Recommended / Zero Ban Risk)
1. Create a Telegram bot via `@BotFather` and get your **Bot Token** and **Chat ID** (via `@userinfobot`).
2. Put them in `config.json` (`"telegram_bot_token"` and `"telegram_chat_id"`) and keep `"auto_trade": false`.
3. Run `python3 bot.py`. Every high-confidence CALL/PUT signal will be delivered to your Telegram with exact entry price, expiry, and stake, and you place the trade manually on Quotex.

### Option 2 — Direct Quotex WebSocket Auto-Trading via SSID Token
1. Install WebSocket support:
   ```bash
   pip install -r requirements.txt
   ```
2. Log in to Quotex (`qxbroker.com`) in Chrome/Edge and switch to **Demo** first.
3. Open **DevTools (`F12`) → Network → WS**, refresh the page, click the `socket.io` connection, and open the **Messages** tab.
4. Locate the outgoing authorization packet:
   `42["authorization",{"session":"YOUR_SESSION_TOKEN","isDemo":1,"tournamentId":0}]`
5. Copy the `"session"` string (or the full `42["authorization",...]` frame) into `config.json` -> `"quotex_ssid"`.
6. Set `"account_type": "DEMO"` (or `"REAL"`) and `"auto_trade": true`, then start:
   ```bash
   python3 bot.py
   ```

> **Note:** Quotex SSID sessions expire periodically when you log out or clear cookies. If `quotex_client.py` reports `not_authorized`, grab a fresh SSID from DevTools.

## Included Strategies (11)

1. **RSI + Bollinger Bands Reversal** — Mean reversion at upper/lower BB(20,2) with RSI(14) overbought/oversold confirmation.
2. **EMA 9/21 Cross + EMA 200 Filter** — Golden/Death cross filtered by macro 200 EMA trend direction.
3. **MACD (12,26,9) Momentum Cross** — Signal line crossover and expanding histogram momentum.
4. **Stochastic (14,3,3) Extreme Cross** — %K / %D crossover inside oversold (<22) and overbought (>78) zones.
5. **Parabolic SAR + ADX(14)** — Trend reversals validated by ADX > 20 and +DI/-DI directional separation.
6. **SuperTrend ATR(10, 3.0)** — Volatility-adjusted trailing trend flip and pullback continuation.
7. **Candlestick Price Action** — Bullish/Bearish Engulfing, Pin Bars (Hammer/Shooting Star), Morning/Evening Star, Three Soldiers/Crows.
8. **Support/Resistance & Floor Pivots** — Swing liquidity zone rejections + classical Pivot Points (S1/S2/R1/R2).
9. **CCI(20) + Williams %R(14) Dual** — Dual-oscillator extreme exhaustion reversal.
10. **Ichimoku Kinko Hyo Cloud** — Tenkan/Kijun crossover aligned with Kumo Cloud (Senkou Span A/B).
11. **Donchian(20) Volatility Breakout** — 20-bar channel breakout with candle confirmation.
