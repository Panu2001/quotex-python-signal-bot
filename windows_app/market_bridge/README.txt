SignalDesk Market Bridge

1. Extract this ZIP. In Chrome open chrome://extensions, enable Developer mode,
   and choose Load unpacked. Select the extracted market_bridge folder.
2. Copy the extension ID shown by Chrome. Open PowerShell in market_bridge and
   run .\Install-Bridge.ps1 -ExtensionId YOUR_ID (Python 3 must be on PATH).
3. Sign into Quotex normally in Chrome. Open the asset list on quotex.com,
   click Market Bridge and then Start sharing. Keep that tab visible.
4. Open SignalDesk Markets. The source appears as Browser bridge. Use Stop to
   clear the bridge snapshot. Browser data expires automatically after 15 seconds.

The collector reads visible table rows and elements matching the row selector.
A supported row has a symbol in data-symbol or data-asset, or an FX pair label
such as AUD/JPY (OTC). It reads visible named data-field elements for price,
change_24h, payout_1m and payout_5m. It can also read the three percent columns
from a four-column table. Site layouts with other markup may require a selector
adjustment. Hidden rows, passwords, cookies and WebSocket messages are excluded.

This bridge supplies Markets fields only. It does not supply closed candles or
settlement prices to Stubay. Quotex and SignalDesk must permit your use of the data.
