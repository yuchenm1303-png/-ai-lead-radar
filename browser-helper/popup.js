const IMPORT_URL = "https://smirel.com/radar/import.html";
const statusEl = document.getElementById("status");
const noteButton = document.getElementById("captureNote");
const listButton = document.getElementById("captureList");

function setStatus(message) { statusEl.textContent = message; }
function setBusy(busy) { noteButton.disabled = busy; listButton.disabled = busy; }

function encodePayload(payload) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/g, "");
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("没有可读取的当前页面");
  return tab;
}

function extractor(mode) {
  function cleanText(value, limit = 1600) {
    return String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  }
  function meta(name) {
    return document.querySelector(`meta[property="${name}"]`)?.content || document.querySelector(`meta[name="${name}"]`)?.content || "";
  }
  function cleanUrl(value) {
    try {
      const url = new URL(value, location.href);
      if (!['http:', 'https:'].includes(url.protocol)) return "";
      if (url.hostname.includes('xiaohongshu.com')) return `${url.origin}${url.pathname}`;
      ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'].forEach((key) => url.searchParams.delete(key));
      url.hash = "";
      return url.href;
    } catch { return ""; }
  }
  function sourceId(value) {
    try {
      const path = new URL(value, location.href).pathname;
      const match = path.match(/\/(?:explore|discovery\/item)\/([a-zA-Z0-9_-]+)/);
      return match?.[1] || null;
    } catch { return null; }
  }
  function publishedFromText(text) {
    const now = new Date();
    let match = text.match(/(\d+)\s*分钟(?:前|ago)/i);
    if (match) return new Date(now.getTime() - Number(match[1]) * 60000).toISOString();
    match = text.match(/(\d+)\s*小时(?:前|ago)/i);
    if (match) return new Date(now.getTime() - Number(match[1]) * 3600000).toISOString();
    if (/刚刚|just now/i.test(text)) return now.toISOString();
    if (/昨天/.test(text)) return new Date(now.getTime() - 86400000).toISOString();
    match = text.match(/(?:^|\s)(\d{1,2})[-/.](\d{1,2})(?:\s|$)/);
    if (match) {
      const date = new Date(now.getFullYear(), Number(match[1]) - 1, Number(match[2]), 12, 0, 0);
      if (date.getTime() > now.getTime() + 86400000) date.setFullYear(date.getFullYear() - 1);
      return date.toISOString();
    }
    return null;
  }
  function pageSource() {
    return location.hostname.includes('xiaohongshu.com') ? '小红书' : location.hostname.replace(/^www\./, '');
  }
  function titleFromPage() {
    const raw = cleanText(meta('og:title') || document.querySelector('h1')?.innerText || document.title, 240);
    return raw.replace(/\s*[-|_]\s*小红书.*$/i, '').replace(/\s*-\s*Xiaohongshu.*$/i, '').trim();
  }
  function currentItem() {
    const selected = cleanText(window.getSelection?.()?.toString(), 1600);
    const candidates = [
      selected,
      meta('og:description'),
      meta('description'),
      document.querySelector('article')?.innerText,
      document.querySelector('main')?.innerText,
      document.querySelector('[class*="note-content"]')?.innerText,
      document.querySelector('[class*="desc"]')?.innerText
    ].map((value) => cleanText(value, 2400)).filter(Boolean);
    const title = titleFromPage();
    let excerpt = candidates.sort((a, b) => b.length - a.length)[0] || '';
    if (excerpt.startsWith(title)) excerpt = excerpt.slice(title.length).trim();
    const context = cleanText(`${title} ${excerpt}`, 3000);
    return {
      source: pageSource(),
      external_id: sourceId(location.href),
      title: title || cleanText(excerpt, 120) || '未命名公开需求',
      excerpt: cleanText(excerpt, 1600),
      url: cleanUrl(location.href),
      published_at: publishedFromText(context)
    };
  }
  function visibleItems() {
    const selectors = ['a[href*="/explore/"]', 'a[href*="/discovery/item/"]'];
    const anchors = [...document.querySelectorAll(selectors.join(','))];
    const seen = new Set();
    const items = [];
    for (const anchor of anchors) {
      const url = cleanUrl(anchor.href);
      if (!url || seen.has(url)) continue;
      seen.add(url);
      let node = anchor;
      let best = '';
      for (let depth = 0; depth < 5 && node; depth += 1, node = node.parentElement) {
        const text = cleanText(node.innerText, 1200);
        if (text.length >= 8 && text.length <= 1200) best = text;
        if (text.length >= 40) break;
      }
      const lines = String(best || anchor.innerText || '').split(/\n+/).map((x) => cleanText(x, 240)).filter(Boolean);
      const title = lines[0] || cleanText(anchor.getAttribute('aria-label') || anchor.title, 240);
      if (!title || title.length < 2) continue;
      items.push({
        source: pageSource(),
        external_id: sourceId(url),
        title,
        excerpt: cleanText(lines.slice(1).join(' · ') || best, 1000),
        url,
        published_at: publishedFromText(best)
      });
      if (items.length >= 30) break;
    }
    return items;
  }
  return mode === 'list' ? visibleItems() : [currentItem()];
}

async function capture(mode) {
  setBusy(true);
  setStatus(mode === 'list' ? '正在整理当前可见候选…' : '正在读取当前帖子…');
  try {
    const tab = await activeTab();
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractor, args: [mode] });
    const items = results?.[0]?.result || [];
    if (!items.length) throw new Error(mode === 'list' ? '当前页没有识别到可见帖子卡片' : '没有识别到当前帖子内容');
    const payload = { version: 1, mode, captured_at: new Date().toISOString(), items };
    const encoded = encodePayload(payload);
    await chrome.tabs.create({ url: `${IMPORT_URL}#${encoded}` });
    setStatus(`已整理 ${items.length} 条，正在 Radar 中复核`);
    window.close();
  } catch (error) {
    setStatus(error?.message || '读取失败，请确认当前页面可正常浏览');
  } finally {
    setBusy(false);
  }
}

noteButton.addEventListener('click', () => capture('note'));
listButton.addEventListener('click', () => capture('list'));
