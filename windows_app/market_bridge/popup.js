const status = document.getElementById("status");
const selector = document.getElementById("rows");
const paint = ({ message } = {}) => { status.textContent = message || "Open the market list in your Quotex tab."; };
chrome.storage.local.get(["selector", "marketStatus"]).then((saved) => {
  if (saved.selector) selector.value = saved.selector;
  paint(saved.marketStatus);
});
chrome.storage.onChanged.addListener((changes) => {
  if (changes.marketStatus) paint(changes.marketStatus.newValue);
});

async function control(enabled) {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab || !tab.id) throw new Error("No active tab.");
    if (enabled) {
      const url = new URL(tab.url);
      if (url.protocol !== "https:" || !["quotex.com", "www.quotex.com"].includes(url.hostname)) {
        throw new Error("Open your signed-in quotex.com tab first.");
      }
      if (!selector.value.trim()) throw new Error("Enter a market row selector.");
      await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ["collector.js"]});
      await chrome.storage.local.set({selector: selector.value.trim()});
    }
    const result = await chrome.tabs.sendMessage(tab.id, {type: "capture", enabled, selector: selector.value.trim()});
    if (!result?.ok) throw new Error(result?.error || "Unable to change capture state.");
    if (!enabled) paint({message: "Sharing stopped."});
  } catch (error) {
    paint({message: error.message});
  }
}
document.getElementById("start").onclick = () => void control(true);
document.getElementById("stop").onclick = () => void control(false);
