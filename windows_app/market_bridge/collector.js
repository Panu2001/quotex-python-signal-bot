(() => {
  if (globalThis.signalDeskMarketCollector) return;
  globalThis.signalDeskMarketCollector = true;
  let timer;
  let captureId;
  const number = (value) => {
    const text = String(value ?? "").trim().replace("−", "-").replace(",", ".");
    return /^[+-]?\d+(?:\.\d+)?%?$/.test(text) ? Number(text.replace("%", "")) : null;
  };
  function quote(row) {
    if (!row.checkVisibility()) return null;
    const cells = Array.from(row.querySelectorAll(':scope > td, :scope > [role="cell"]'));
    const text = (cells[0]?.innerText || row.innerText || "").slice(0, 320);
    const pair = text.match(/\b([A-Z]{3})\s*\/\s*([A-Z]{3})\b(?:\s*\(OTC\))?/i);
    const raw = row.dataset.symbol || row.dataset.asset;
    const symbol = raw ? raw.toUpperCase().replace(/[_-]OTC$/, "-OTC")
      : pair ? (pair[1] + pair[2] + (/OTC/i.test(pair[0]) ? "-OTC" : "")).toUpperCase() : "";
    if (!/^[A-Z0-9][A-Z0-9._-]{1,31}$/.test(symbol)) return null;
    const field = (name) => row.querySelector(`[data-field="${name}"]`)?.innerText;
    const fourColumns = cells.length === 4 && cells.slice(1).every((cell) => cell.innerText.includes("%"));
    return {
      symbol, price: number(field("price")),
      change_24h: number(field("change_24h") ?? (fourColumns ? cells[1].innerText : null)),
      payout_1m: number(field("payout_1m") ?? (fourColumns ? cells[2].innerText : null)),
      payout_5m: number(field("payout_5m") ?? (fourColumns ? cells[3].innerText : null)),
    };
  }
  function visibleAssetRows() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const rows = [];
    const pairLabel = /^\s*[A-Z]{3}\s*\/\s*[A-Z]{3}(?:\s*\(OTC\))?\s*$/i;
    const pairPattern = /\b([A-Z]{3})\s*\/\s*([A-Z]{3})\b(?:\s*\(OTC\))?/gi;
    for (let node; (node = walker.nextNode()) && rows.length < 500;) {
      if (!pairLabel.test(node.nodeValue)) continue;
      let row = node.parentElement;
      for (let depth = 0; row && row !== document.body && depth < 7; depth++, row = row.parentElement) {
        if (!row.checkVisibility()) break;
        const text = row.innerText || "";
        if (text.length > 220) break;
        const pairs = [...text.matchAll(pairPattern)];
        if (pairs.length !== 1) break;
        const values = [...text.matchAll(/[+−-]?\s*\d+(?:[.,]\d+)?\s*%/g)];
        if (values.length !== 3) continue;
        if (values[0].index < pairs[0].index) continue;
        const [label, left, right] = pairs[0];
        rows.push({
          symbol: (left + right + (/OTC/i.test(label) ? "-OTC" : "")).toUpperCase(),
          change_24h: number(values[0][0].replace(/\s/g, "")),
          payout_1m: number(values[1][0].replace(/\s/g, "")),
          payout_5m: number(values[2][0].replace(/\s/g, "")),
          price: null,
        });
        break;
      }
    }
    return rows;
  }
  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (message.type !== "capture" || sender.id !== chrome.runtime.id) return;
    clearInterval(timer);
    if (!message.enabled) {
      if (captureId) void chrome.runtime.sendMessage({type: "stop", captureId}).catch(() => {});
      reply({ok: true});
      return;
    }
    try {
      document.querySelector(message.selector);
      captureId = crypto.randomUUID();
      const tick = () => {
        if (document.visibilityState !== "visible") return;
        const rows = Array.from(document.querySelectorAll(message.selector)).slice(0, 500)
          .map(quote).filter((row) => row && Object.keys(row).some((key) => key !== "symbol" && row[key] !== null));
        const matched = new Map(rows.map((row) => [row.symbol, row]));
        for (const row of visibleAssetRows()) {
          if (!matched.has(row.symbol)) matched.set(row.symbol, row);
        }
        void chrome.runtime.sendMessage({type: "quotes", rows: [...matched.values()], captureId}).catch(() => {});
      };
      tick();
      timer = setInterval(tick, 2500);
      reply({ok: true});
    } catch {
      reply({ok: false, error: "Invalid market row selector."});
    }
  });
})();
