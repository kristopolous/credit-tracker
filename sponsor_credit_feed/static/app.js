const feedEl = document.getElementById("feed");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("item-count");
const dotEl = document.getElementById("conn-dot");
const labelEl = document.getElementById("conn-label");
const filtersEl = document.getElementById("filters");
const crawlStatusEl = document.getElementById("crawl-status");
const crawlSourcesEl = document.getElementById("crawl-sources");

// "credit" kind is unreachable - code_detector.py's find_candidates()
// always requires a literal code or QR to surface a candidate at all.
const KIND_LABELS = { code: "Code", qr: "QR" };

let items = [];
let activeFilter = "all";
const SOURCES = ["all", "luma", "cerebral_valley", "lablab"];

function renderFilters() {
  filtersEl.innerHTML = "";
  SOURCES.forEach((s) => {
    const btn = document.createElement("button");
    btn.textContent = s === "all" ? "All sources" : s.replace("_", " ");
    if (s === activeFilter) btn.classList.add("active");
    btn.onclick = () => {
      activeFilter = s;
      renderFilters();
      renderFeed();
    };
    filtersEl.appendChild(btn);
  });
}

function timeAgo(ts) {
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function factRow(item) {
  const facts = [];
  const service = item.service;
  if (service) facts.push(`<div class="fact"><span class="fact-label">service</span><span class="fact-value">${escapeHtml(service)}</span></div>`);
  item.codes.forEach((c) =>
    facts.push(`<div class="fact fact-code"><span class="fact-label">code</span><span class="fact-value mono">${escapeHtml(c)}</span></div>`)
  );
  item.amounts.forEach((a) =>
    facts.push(`<div class="fact fact-amount"><span class="fact-label">amount</span><span class="fact-value">${escapeHtml(a)}</span></div>`)
  );
  return facts.length ? `<div class="facts">${facts.join("")}</div>` : "";
}

const URL_RE = /https?:\/\/[^\s)]+/g;

function linkify(text) {
  return escapeHtml(text).replace(URL_RE, (url) => {
    const safe = escapeHtml(url);
    return `<a href="${safe}" target="_blank" rel="noopener">${safe}</a>`;
  });
}

function sourceLinkLabel(sourceUrl) {
  // A .txt source is a bulk inventory file (e.g. Cerebral Valley's
  // llms-full.txt), not a page about this specific event - label it
  // honestly instead of implying "view source" leads to a normal page.
  return sourceUrl.endsWith(".txt") ? "source: full event list →" : "view source →";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Every stored item already has a literal code/QR and a resolved company
// (see code_detector.py's find_candidates) - nothing vaguer than that ever
// makes it into the feed, so there's no separate "actionable" tier to sort
// by. Just recency.
function compareItems(a, b) {
  return b.discovered_at - a.discovered_at;
}

// Slug-guess fallback ("sep-21-cognee-aws" -> "Sep 21 Cognee Aws") for when
// we truly have no real title - only used if nothing in the group carried
// one back from the source (see groupByEvent below).
function guessTitleFromUrl(eventUrl) {
  try {
    const u = new URL(eventUrl);
    const slug = u.pathname.replace(/\/+$/, "").split("/").filter(Boolean).pop() || u.hostname;
    const words = slug.replace(/[-_]+/g, " ").trim();
    return words.replace(/\b\w/g, (c) => c.toUpperCase()) || u.hostname;
  } catch {
    return eventUrl;
  }
}

// One event can surface several deals (a code, a QR, a plain credit
// mention, ...) discovered from different pages (the event page itself,
// its linked resources doc, a hub crawl of another platform) - group them
// under a single card instead of one card per mention, so "7 links to 1
// event" becomes "1 event, here are its deals."
function groupByEvent(list) {
  const groups = new Map();
  for (const item of list) {
    const key = item.event_url;
    if (!groups.has(key)) {
      groups.set(key, { eventUrl: key, source: item.source, deals: [] });
    }
    groups.get(key).deals.push(item);
  }
  for (const g of groups.values()) {
    g.deals.sort(compareItems);
    g.latestAt = Math.max(...g.deals.map((d) => d.discovered_at));
    const realTitle = g.deals.map((d) => d.event_title).find(Boolean);
    g.title = realTitle || guessTitleFromUrl(g.eventUrl);
  }
  return [...groups.values()];
}

function compareGroups(a, b) {
  return b.latestAt - a.latestAt;
}

function dealHTML(item) {
  const description = item.text && item.text.trim() ? `<p class="text">${linkify(item.text)}</p>` : "";
  return `
    <div class="deal" data-id="${item.id}" data-discovered-at="${item.discovered_at}">
      <div class="deal-top">
        <span class="badge badge-${item.kind}">${KIND_LABELS[item.kind] || item.kind}</span>
        <span class="time-tag">${timeAgo(item.discovered_at)}</span>
      </div>
      ${factRow(item)}
      ${description}
      <div class="card-links">
        <a class="src-link" href="${item.source_url}" target="_blank" rel="noopener">${sourceLinkLabel(item.source_url)}</a>
        ${item.redeem_url ? `<a class="redeem-link" href="${item.redeem_url}" target="_blank" rel="noopener">redeem →</a>` : ""}
      </div>
    </div>`;
}

function groupCardHTML(group) {
  const sources = [...new Set(group.deals.map((d) => d.source))];
  return `
    <div class="card event-card" data-event-url="${escapeHtml(group.eventUrl)}">
      <div class="event-top">
        <h3 class="event-title">${escapeHtml(group.title)}</h3>
        <span class="deal-count">${group.deals.length} deal${group.deals.length === 1 ? "" : "s"}</span>
      </div>
      <div class="event-meta">
        ${sources.map((s) => `<span class="source-tag">${escapeHtml(s.replace("_", " "))}</span>`).join("")}
        <a class="src-link" href="${group.eventUrl}" target="_blank" rel="noopener">view event →</a>
      </div>
      <div class="deals">${group.deals.map(dealHTML).join("")}</div>
    </div>`;
}

// Full rebuild - only for filter switches and first load, since it wipes
// and repaints every card (visible as a "flash" if called often).
function renderFeed() {
  const visible = items.filter((i) => activeFilter === "all" || i.source === activeFilter);
  const groups = groupByEvent(visible).sort(compareGroups);
  countEl.textContent = `${visible.length} deal${visible.length === 1 ? "" : "s"} · ${groups.length} event${groups.length === 1 ? "" : "s"}`;
  emptyEl.classList.toggle("hidden", visible.length > 0);
  feedEl.innerHTML = groups.map(groupCardHTML).join("");
}

// Cheap per-tick refresh of just the "Xm ago" labels - touches only text
// nodes, no rebuild, so it can run every 30s without any visible flash.
function refreshTimeTags() {
  feedEl.querySelectorAll(".deal").forEach((deal) => {
    const ts = Number(deal.dataset.discoveredAt);
    const tag = deal.querySelector(".time-tag");
    if (tag && !Number.isNaN(ts)) tag.textContent = timeAgo(ts);
  });
}

function upsert(item) {
  const idx = items.findIndex((i) => i.id === item.id);
  if (idx < 0) items.unshift(item);
  else items[idx] = item;

  const visible = items.filter((i) => activeFilter === "all" || i.source === activeFilter);
  const groups = groupByEvent(visible).sort(compareGroups);
  countEl.textContent = `${visible.length} deal${visible.length === 1 ? "" : "s"} · ${groups.length} event${groups.length === 1 ? "" : "s"}`;
  emptyEl.classList.add("hidden");

  const matchesFilter = activeFilter === "all" || item.source === activeFilter;
  const existingGroupEl = feedEl.querySelector(`.event-card[data-event-url="${cssEscape(item.event_url)}"]`);

  if (!matchesFilter) {
    if (existingGroupEl) existingGroupEl.remove();
    return;
  }

  const group = groups.find((g) => g.eventUrl === item.event_url);
  if (!group) return; // shouldn't happen - item was just added to `items`

  if (existingGroupEl) {
    // Re-render just this one event's card in place - not the whole feed -
    // so adding a second deal to an existing event doesn't flash the list.
    existingGroupEl.outerHTML = groupCardHTML(group);
    return;
  }

  // Brand new event - insert its card at the correct sorted position.
  const nextGroupEl = [...feedEl.querySelectorAll(".event-card")].find((el) => {
    const other = groups.find((g) => g.eventUrl === el.dataset.eventUrl);
    return other && compareGroups(group, other) < 0;
  });
  if (nextGroupEl) nextGroupEl.insertAdjacentHTML("beforebegin", groupCardHTML(group));
  else feedEl.insertAdjacentHTML("beforeend", groupCardHTML(group));
}

function cssEscape(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/["\\]/g, "\\$&");
}

function connect() {
  const es = new EventSource("/api/stream");
  es.onopen = () => {
    dotEl.classList.remove("dot-off");
    dotEl.classList.add("dot-on");
    labelEl.textContent = "live";
  };
  es.onerror = () => {
    dotEl.classList.remove("dot-on");
    dotEl.classList.add("dot-off");
    labelEl.textContent = "reconnecting…";
  };
  es.onmessage = (e) => {
    try {
      upsert(JSON.parse(e.data));
    } catch (err) {
      console.error(err);
    }
  };
}

function fmtAgo(ts) {
  if (!ts) return null;
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function fmtETA(secondsFromNow) {
  if (secondsFromNow == null) return null;
  const m = Math.max(0, Math.round(secondsFromNow / 60));
  if (m < 1) return "any moment";
  if (m < 60) return `~${m}m`;
  return `~${(m / 60).toFixed(1)}h`;
}

// Renders exactly what the crawler actually did, with real numbers - "this
// really did check N pages Xm ago" is the whole point, not a decorative
// "live" dot. Also renders a per-source breakdown (toggle-able) so the
// claim is checkable, not just asserted.
let lastStatus = null;

function renderCrawlStatus(s) {
  lastStatus = s;
  const parts = [];
  if (s.in_progress) {
    const doneCount = s.sources_done || 0;
    parts.push(`checking now — ${doneCount}/${s.sources_total} sources, ${s.total_checked} pages so far`);
  } else if (s.last_completed_at) {
    const ago = fmtAgo(s.last_completed_at);
    parts.push(`checked ${s.total_checked} pages across ${s.sources.length} sources ${ago}`);
    parts.push(
      `${s.total_candidates} new code${s.total_candidates === 1 ? "" : "s"} found this pass, ` +
        `${items.length} total in feed`,
    );
    if (s.poll_interval_seconds) {
      const nextIn = s.last_completed_at + s.poll_interval_seconds - Date.now() / 1000;
      const eta = fmtETA(nextIn);
      if (eta) parts.push(`next check ${eta}`);
    }
  } else {
    parts.push("first crawl hasn't finished yet…");
  }
  crawlStatusEl.textContent = parts.join(" · ");
  crawlStatusEl.onclick = () => {
    crawlSourcesEl.classList.toggle("hidden");
    renderCrawlSources(s);
  };
  if (!crawlSourcesEl.classList.contains("hidden")) renderCrawlSources(s);
}

function renderCrawlSources(s) {
  if (!s.sources || !s.sources.length) {
    crawlSourcesEl.innerHTML = `<div class="crawl-source-row">no sources have reported yet</div>`;
    return;
  }
  crawlSourcesEl.innerHTML = s.sources
    .map((src) => {
      const errTag = src.error ? `<span class="crawl-source-err" title="${escapeHtml(src.error)}">failed</span>` : "";
      return `<div class="crawl-source-row">
        <span class="crawl-source-label">${escapeHtml(src.label)}</span>
        <span class="crawl-source-nums">${src.blobs_checked} page${src.blobs_checked === 1 ? "" : "s"} checked, ${src.candidates_found} new code${src.candidates_found === 1 ? "" : "s"}</span>
        ${errTag}
      </div>`;
    })
    .join("");
}

async function pollCrawlStatus() {
  try {
    const resp = await fetch("/api/status");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    renderCrawlStatus(await resp.json());
  } catch (err) {
    crawlStatusEl.textContent = "couldn't reach crawl status";
    console.error(err);
  }
}

// Re-render the relative "Xm ago" / countdown text every tick even without
// a fresh fetch, then actually re-fetch on a slower cadence.
setInterval(() => { if (lastStatus) renderCrawlStatus(lastStatus); }, 15000);
setInterval(pollCrawlStatus, 20000);

setInterval(refreshTimeTags, 30000); // keep "time ago" labels fresh, no flash
renderFilters();
connect();
pollCrawlStatus();
