(function () {
  "use strict";

  const bootLog = document.getElementById("boot-log");
  const bootScreen = document.getElementById("boot");
  const vault = document.getElementById("vault");
  const listingBody = document.getElementById("listing-body");
  const listView = document.getElementById("list-view");
  const gridView = document.getElementById("grid-view");
  const emptyState = document.getElementById("empty-state");
  const searchInput = document.getElementById("search-input");
  const systemTabs = document.getElementById("system-tabs");
  const romCount = document.getElementById("rom-count");
  const themeToggle = document.getElementById("theme-toggle");
  const viewToggle = document.getElementById("view-toggle");
  const rescanBtn = document.getElementById("rescan-btn");
  const mountStatus = document.getElementById("mount-status");

  const VIEW_KEY = "romvault:view";
  const THEME_KEY = "romvault:theme";

  let allRoms = [];          // full library, fetched once
  let currentTab = "all";    // "all" | "favorites" | "recent" | system key
  let currentQuery = "";
  let viewMode = localStorage.getItem(VIEW_KEY) || "list";

  // Favorites/recent now live server-side, tied to the logged-in account,
  // so they follow you across devices. Fetched once and kept in memory;
  // refreshed after any toggle.
  let favoriteKeys = new Set();  // "system:filename"
  let recentOrder = new Map();   // "system:filename" -> most-recent-first index

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // -------------------------------------------------------------------
  // Icon sprite helper
  // -------------------------------------------------------------------
  function iconSvg(name) {
    return `<svg viewBox="0 0 24 24"><use href="#icon-${name}"></use></svg>`;
  }

  // -------------------------------------------------------------------
  // Favorites / recent (server-side, tied to your account)
  // -------------------------------------------------------------------
  function romKey(rom) { return `${rom.system}:${rom.filename}`; }

  function isFavorite(rom) {
    return favoriteKeys.has(romKey(rom));
  }

  async function loadFavorites() {
    try {
      const res = await fetch("/api/favorites");
      const favs = await res.json();
      favoriteKeys = new Set(favs.map((f) => `${f.system}:${f.filename}`));
    } catch (e) { /* non-fatal */ }
  }

  async function loadRecent() {
    try {
      const res = await fetch("/api/recent");
      const recent = await res.json();
      recentOrder = new Map(recent.map((e, i) => [`${e.system}:${e.filename}`, i]));
    } catch (e) { /* non-fatal */ }
  }

  async function toggleFavorite(rom) {
    try {
      const res = await fetch("/api/favorites/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system: rom.system, filename: rom.filename }),
      });
      const result = await res.json();
      const key = romKey(rom);
      if (result.favorite) favoriteKeys.add(key);
      else favoriteKeys.delete(key);
    } catch (e) { /* non-fatal -- UI just won't update this time */ }
  }

  // -------------------------------------------------------------------
  // Boot sequence
  // -------------------------------------------------------------------
  const BOOT_LINES = [
    "ROM VAULT OS v2.0 -- INITIALIZING...",
    "CHECKING MEMORY......... OK",
    "MOUNTING NAS ARRAY....... OK",
    "INDEXING LIBRARY........ PLEASE WAIT",
  ];

  function typeBootSequence(onDone) {
    if (prefersReducedMotion) { onDone(); return; }
    let lineIndex = 0, charIndex = 0, text = "";
    function tick() {
      if (lineIndex >= BOOT_LINES.length) { setTimeout(onDone, 300); return; }
      const line = BOOT_LINES[lineIndex];
      if (charIndex < line.length) {
        text += line[charIndex];
        charIndex++;
        bootLog.textContent = text + "_";
        setTimeout(tick, 12);
      } else {
        text += "\n";
        lineIndex++;
        charIndex = 0;
        setTimeout(tick, 120);
      }
    }
    tick();
  }

  function revealVault() {
    bootScreen.classList.add("hidden");
    vault.classList.remove("hidden");
    searchInput.focus();
  }

  // -------------------------------------------------------------------
  // Data loading
  // -------------------------------------------------------------------
  let scanPollTimer = null;

  async function loadSystems() {
    try {
      const res = await fetch("/api/systems");
      const systems = await res.json();
      renderSystemTabs(systems);
      mountStatus.textContent = "CONNECTED";
    } catch (e) {
      mountStatus.textContent = "ERROR";
    }
  }

  async function loadLibrary() {
    try {
      const res = await fetch("/api/roms");
      allRoms = await res.json();
      applyFiltersAndRender();
    } catch (e) {
      mountStatus.textContent = "ERROR";
    }
    pollScanStatusIfNeeded();
  }

  async function pollScanStatusIfNeeded() {
    clearTimeout(scanPollTimer);
    try {
      const res = await fetch("/api/scan-status");
      const status = await res.json();

      if (status.error) {
        romCount.textContent = "SCAN ERROR -- CHECK SERVER LOGS";
        return;
      }

      if (status.scanning) {
        // First scan (or a big library on a slow NAS) can take a while --
        // show that it's actively working instead of a misleading "0
        // titles" state, and keep checking back.
        romCount.textContent = allRoms.length > 0
          ? `${allRoms.length} TITLES INDEXED (STILL SCANNING...)`
          : "INDEXING LIBRARY... (large NAS libraries can take a while)";
        scanPollTimer = setTimeout(async () => {
          await loadSystems();
          await loadLibrary();
        }, 4000);
        return;
      }

      // Not scanning the filesystem anymore -- check whether a box art
      // scrape is running (auto-triggered after rescan when Skyscraper's
      // installed) and reflect that instead.
      const scrapeRes = await fetch("/api/scrape-status");
      const scrape = await scrapeRes.json();
      if (scrape.running) {
        const sys = scrape.system ? ` (${scrape.system.toUpperCase()})` : "";
        romCount.textContent = `FETCHING BOX ART${sys}: ${scrape.done}/${scrape.total}`;
        scanPollTimer = setTimeout(async () => {
          await loadLibrary();
        }, 4000);
      }
    } catch (e) {
      // Non-fatal -- just stop polling silently.
    }
  }

  function renderSystemTabs(systems) {
    systemTabs.querySelectorAll(".tab[data-dynamic]").forEach((el) => el.remove());
    systems.forEach((sys) => {
      const btn = document.createElement("button");
      btn.className = "tab";
      btn.setAttribute("role", "tab");
      btn.setAttribute("data-system", sys.key);
      btn.setAttribute("data-dynamic", "1");
      btn.innerHTML = `<span class="tab-icon">${iconSvg("cart")}</span>${sys.key.toUpperCase()} (${sys.count})`;
      btn.title = sys.label + (sys.playable ? "" : " -- download only, no browser emulator core");
      systemTabs.appendChild(btn);
    });
  }

  function setActiveTab(tabKey) {
    currentTab = tabKey;
    systemTabs.querySelectorAll(".tab").forEach((t) => {
      const isActive = t.getAttribute("data-system") === tabKey;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
  }

  // -------------------------------------------------------------------
  // Filtering
  // -------------------------------------------------------------------
  function applyFiltersAndRender() {
    let roms;

    if (currentTab === "favorites") {
      roms = allRoms.filter((r) => favoriteKeys.has(romKey(r)));
    } else if (currentTab === "recent") {
      roms = allRoms.filter((r) => recentOrder.has(romKey(r)));
      roms.sort((a, b) => recentOrder.get(romKey(a)) - recentOrder.get(romKey(b)));
    } else if (currentTab === "all") {
      roms = allRoms.slice();
    } else {
      roms = allRoms.filter((r) => r.system === currentTab);
    }

    if (currentQuery) {
      const q = currentQuery.toLowerCase();
      roms = roms.filter((r) => r.title.toLowerCase().includes(q) || (r.category || "").toLowerCase().includes(q));
    }

    render(roms);
  }

  // -------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------
  function humanCount(n) { return `${n} TITLE${n === 1 ? "" : "S"} INDEXED`; }

  function artThumbHtml(rom, cls, fallbackCls) {
    if (rom.art_url) {
      return `<img class="${cls}" src="${rom.art_url}" alt="" loading="lazy"
                onerror="this.outerHTML=window.__rv_fallback('${fallbackCls}')">`;
    }
    return window.__rv_fallback(fallbackCls);
  }

  window.__rv_fallback = function (fallbackCls) {
    return `<div class="${fallbackCls}">${iconSvg("cart")}</div>`;
  };

  function render(roms) {
    romCount.textContent = humanCount(roms.length);

    if (roms.length === 0) {
      emptyState.classList.remove("hidden");
      listingBody.innerHTML = "";
      gridView.innerHTML = "";
      return;
    }
    emptyState.classList.add("hidden");

    if (viewMode === "grid") {
      listView.classList.add("hidden");
      gridView.classList.remove("hidden");
      renderGrid(roms);
    } else {
      gridView.classList.add("hidden");
      listView.classList.remove("hidden");
      renderList(roms);
    }
  }

  function renderList(roms) {
    const frag = document.createDocumentFragment();
    roms.forEach((rom) => {
      const tr = document.createElement("tr");

      const tdArt = document.createElement("td");
      tdArt.className = "col-art";
      tdArt.innerHTML = artThumbHtml(rom, "thumb", "thumb-fallback");
      tr.appendChild(tdArt);

      const tdTitle = document.createElement("td");
      tdTitle.className = "rom-title";
      const fav = isFavorite(rom);
      tdTitle.innerHTML = `
        <button class="star-btn${fav ? " active" : ""}" data-action="fav" title="Toggle favorite">
          ${iconSvg(fav ? "star" : "star-outline")}
        </button>
        <span>${escapeHtml(rom.title)}</span>
        ${rom.category ? `<span class="rom-category">${escapeHtml(rom.category)}</span>` : ""}`;
      tdTitle.querySelector('[data-action="fav"]').addEventListener("click", async () => {
        await toggleFavorite(rom);
        applyFiltersAndRender();
      });
      tr.appendChild(tdTitle);

      const tdSystem = document.createElement("td");
      tdSystem.className = "col-system";
      tdSystem.textContent = rom.system.toUpperCase();
      tr.appendChild(tdSystem);

      const tdSize = document.createElement("td");
      tdSize.className = "col-size";
      tdSize.textContent = rom.size_human;
      tr.appendChild(tdSize);

      const tdActions = document.createElement("td");
      tdActions.className = "col-actions";
      tdActions.appendChild(actionLinks(rom));
      tr.appendChild(tdActions);

      frag.appendChild(tr);
    });
    listingBody.innerHTML = "";
    listingBody.appendChild(frag);
  }

  function renderGrid(roms) {
    const frag = document.createDocumentFragment();
    roms.forEach((rom) => {
      const card = document.createElement("div");
      card.className = "grid-card";

      const artWrap = document.createElement("div");
      artWrap.innerHTML = artThumbHtml(rom, "grid-card-art", "grid-card-art-fallback");
      card.appendChild(artWrap.firstElementChild);

      const body = document.createElement("div");
      body.className = "grid-card-body";

      const title = document.createElement("div");
      title.className = "grid-card-title";
      title.textContent = rom.title;
      body.appendChild(title);

      if (rom.category) {
        const cat = document.createElement("div");
        cat.className = "grid-card-category";
        cat.textContent = rom.category;
        body.appendChild(cat);
      }

      const meta = document.createElement("div");
      meta.className = "grid-card-meta";
      const fav = isFavorite(rom);
      meta.innerHTML = `
        <span>${rom.system.toUpperCase()}</span>
        <button class="star-btn${fav ? " active" : ""}" data-action="fav" title="Toggle favorite">
          ${iconSvg(fav ? "star" : "star-outline")}
        </button>`;
      meta.querySelector('[data-action="fav"]').addEventListener("click", async () => {
        await toggleFavorite(rom);
        applyFiltersAndRender();
      });
      body.appendChild(meta);

      const actions = document.createElement("div");
      actions.className = "grid-card-actions";
      actions.appendChild(actionLinks(rom));
      body.appendChild(actions);

      card.appendChild(body);
      frag.appendChild(card);
    });
    gridView.innerHTML = "";
    gridView.appendChild(frag);
  }

  function actionLinks(rom) {
    const wrap = document.createElement("span");

    const playLink = document.createElement("a");
    playLink.className = "action-link" + (rom.playable ? "" : " disabled");
    playLink.textContent = "PLAY";
    if (rom.playable) {
      playLink.href = `/play/${rom.system}/${encodeURIComponent(rom.filename)}`;
    } else {
      playLink.href = "#";
      playLink.setAttribute("aria-disabled", "true");
      playLink.title = "No browser emulator core for this system yet";
    }
    wrap.appendChild(playLink);

    const dlLink = document.createElement("a");
    dlLink.className = "action-link";
    dlLink.textContent = "GET";
    dlLink.href = `/download/${rom.system}/${encodeURIComponent(rom.filename)}`;
    wrap.appendChild(dlLink);

    return wrap;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // -------------------------------------------------------------------
  // Event wiring
  // -------------------------------------------------------------------
  systemTabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn) return;
    setActiveTab(btn.getAttribute("data-system"));
    applyFiltersAndRender();
  });

  let debounceTimer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      currentQuery = searchInput.value.trim();
      applyFiltersAndRender();
    }, 120);
  });

  themeToggle.addEventListener("click", () => {
    const body = document.body;
    const isGreen = body.getAttribute("data-theme") === "green";
    const next = isGreen ? "amber" : "green";
    body.setAttribute("data-theme", next);
    themeToggle.textContent = isGreen ? "[ GREEN ]" : "[ AMBER ]";
    localStorage.setItem(THEME_KEY, next);
  });

  viewToggle.addEventListener("click", () => {
    viewMode = viewMode === "grid" ? "list" : "grid";
    localStorage.setItem(VIEW_KEY, viewMode);
    viewToggle.textContent = viewMode === "grid" ? "[ LIST ]" : "[ GRID ]";
    applyFiltersAndRender();
  });

  rescanBtn.addEventListener("click", async () => {
    rescanBtn.textContent = "[ SCANNING... ]";
    try {
      await fetch("/api/rescan", { method: "POST" });
      await loadSystems();
      await loadLibrary();
    } finally {
      rescanBtn.textContent = "[ RESCAN ]";
    }
  });

  // Restore saved theme / view preference
  (function initPrefs() {
    const savedTheme = localStorage.getItem(THEME_KEY);
    if (savedTheme) {
      document.body.setAttribute("data-theme", savedTheme);
      themeToggle.textContent = savedTheme === "green" ? "[ AMBER ]" : "[ GREEN ]";
    }
    viewToggle.textContent = viewMode === "grid" ? "[ LIST ]" : "[ GRID ]";
  })();

  // Boot
  typeBootSequence(async () => {
    revealVault();
    await Promise.all([loadFavorites(), loadRecent()]);
    await loadSystems();
    await loadLibrary();
  });
})();
