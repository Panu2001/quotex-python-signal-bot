let port;
chrome.runtime.onMessage.addListener((message, sender) => {
  const origin = sender.url && new URL(sender.url).origin;
  if (!sender.tab || !["https://quotex.com", "https://www.quotex.com"].includes(origin)) return;
  if (!["quotes", "stop"].includes(message.type)) return;
  if (!port) {
    port = chrome.runtime.connectNative("com.signaldesk.market_bridge");
    port.onMessage.addListener((status) => void chrome.storage.local.set({marketStatus: status}));
    port.onDisconnect.addListener(() => {
      const error = chrome.runtime.lastError?.message || "Market bridge disconnected.";
      void chrome.storage.local.set({marketStatus: {message: error}});
      port = undefined;
    });
  }
  port.postMessage({type: message.type, captureId: message.captureId, rows: message.rows});
});
