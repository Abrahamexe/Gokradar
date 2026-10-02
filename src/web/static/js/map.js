/* Canlı harita: /api/flights/world (adsb.lol, tüm dünya) verisi tek bir canvas katmanına çizilir.
   Binlerce uçak için DOM işaretçisi yerine önceden çizilmiş, yönüne göre döndürülen simgeler
   kullanılır; iki güncelleme arasında konum hız ve yönden tahmin edilir (dead reckoning). */
(() => {
  "use strict";
  const CFG = window.MAP_CONFIG;
  const $ = (id) => document.getElementById(id);
  const reduced = GR.reducedMotion;

  const EARTH_R = 6371000;          // m
  const KT_TO_MS = 0.514444;
  const MAX_EXTRAP_S = 90;          // bundan eski sinyal ileri tahmin edilmez
  const CORRECTION_MS = 1500;       // yeni veri geldiğinde eski tahminden yumuşak geçiş
  const LIST_LIMIT = 200;
  const HISTORY_LIMIT = 240;
  const HIT_RADIUS = 14;            // px
  const LABEL_MIN_ZOOM = 8;
  const LABEL_MAX_COUNT = 500;      // ekranda bundan çok uçak varken etiket çizilmez

  const PLANE_PATH = "M12 1.5c.8 0 1.3 1 1.3 2.3V9l8.2 4.8v2.1l-8.2-2.4v4.6l2.4 1.8v1.7L12 20.7l-3.7.9v-1.7l2.4-1.8v-4.6l-8.2 2.4v-2.1L10.7 9V3.8c0-1.3.5-2.3 1.3-2.3z";
  const JET_PATH = "M12 1.2l1.1 2.6.4 5.1 7.9 7.3v1.9l-7.8-3-.4 3.2 2.6 2.2v1.5L12 21l-3.8 1v-1.5l2.6-2.2-.4-3.2-7.8 3v-1.9l7.9-7.3.4-5.1z";
  const HELI_PATH = "M12 6.2a3.2 3.2 0 0 1 3.2 3.2v3.4a3.2 3.2 0 0 1-2.4 3.1v4.3h2v1.6h-5.6v-1.6h2v-4.3a3.2 3.2 0 0 1-2.4-3.1V9.4A3.2 3.2 0 0 1 12 6.2zM3 4.4l.9-.9 16.6 16.6-.9.9zM20.1 3.5l.9.9L4.4 21l-.9-.9z";

  // Sunucudaki adsb_feed.py KIND_* sırası
  const KINDS = [
    { key: "airliner", label: "Yolcu" },
    { key: "cargo", label: "Kargo" },
    { key: "military", label: "Askeri" },
    { key: "ga", label: "Genel havacılık" },
    { key: "heli", label: "Helikopter" },
    { key: "ground", label: "Yerde" },
    { key: "fighter", label: "Savaş jeti" },
  ];
  const KIND_HELI = 4, KIND_GROUND = 5, KIND_FIGHTER = 6;
  const LEGEND_ORDER = [0, 1, 2, 6, 3, 4, 5];

  const msgEl = $("map-msg");
  const showMsg = (text) => { msgEl.textContent = text; msgEl.hidden = false; };
  const hideMsg = () => { msgEl.hidden = true; };

  if (!window.L) {
    showMsg("Harita kütüphanesi yüklenemedi. İnternet bağlantınızı kontrol edip sayfayı yenileyin.");
    return;
  }

  // ---------------------------------------------------------------- harita
  let savedView = null;
  try { savedView = JSON.parse(localStorage.getItem("gokradar.mapView")); } catch (_) { /* yok */ }
  const map = L.map("map", {
    // Yatayda sonsuz: dünya tekrar eder, sağa/sola kaydırdıkça aynı yere dönülür (merkez ±180°'de tutulur)
    zoomControl: false, minZoom: 2, maxZoom: 14, worldCopyJump: true,
    maxBounds: [[-85, -1e5], [85, 1e5]], maxBoundsViscosity: 1,
    zoomAnimation: !reduced(), fadeAnimation: !reduced(), markerZoomAnimation: false,
  }).setView(savedView && savedView.center ? savedView.center : [41.0, 22.0],
             savedView && savedView.zoom ? savedView.zoom : 5);
  map.attributionControl.setPosition("bottomleft").setPrefix(false);
  const tiles = L.tileLayer(CFG.tiles.url, {
    maxZoom: CFG.tiles.max_zoom, keepBuffer: 4,
    attribution: `${CFG.tiles.attribution} · Uçak verisi: <a href="https://adsb.lol">adsb.lol</a> (ODbL)`,
  }).addTo(map);
  let tileErrors = 0;
  tiles.on("tileerror", () => {
    if (++tileErrors === 5) showMsg("Harita karoları yüklenemiyor. İnternet bağlantınızı kontrol edin; " +
                                    "site localhost dışında yayındaysa STADIA_API_KEY gerekebilir.");
  });
  $("zoom-in").addEventListener("click", () => map.zoomIn());
  $("zoom-out").addEventListener("click", () => map.zoomOut());

  const state = {
    recs: new Map(),        // icao24 → kayıt
    selected: null,         // icao24
    selRec: null,           // seçili kayıt (sinyal kaybolsa da panelde kalır)
    hover: null,
    filter: "",
    hidden: new Set(),      // gizlenen tür indeksleri
    data: null,             // son yanıtın üst bilgisi
    etag: null,
    timer: null, inflight: null,
    liveState: "loading",
    counts: new Array(KINDS.length).fill(0),
  };

  // ---------------------------------------------------------------- biçimler
  const fmtAlt = (a) => a.kind === KIND_GROUND ? "Yerde" : a.alt == null ? "—" : `${GR.fmtNum(Math.round(a.alt / 10) * 10)} ft`;
  const fmtFL = (a) => a.kind === KIND_GROUND ? "Yerde" : a.alt == null ? "—" :
    `FL${String(Math.max(0, Math.round(a.alt / 100))).padStart(3, "0")}`;
  const fmtSpd = (a) => a.gs == null ? "—" : `${GR.fmtNum(a.gs)} kt`;
  const fmtHdg = (a) => a.trk == null ? "—" : `${String(Math.round(a.trk) % 360).padStart(3, "0")}°`;
  const fmtVs = (a) => a.vr == null ? "—" : `${a.vr > 0 ? "+" : ""}${GR.fmtNum(Math.round(a.vr / 10) * 10)} ft/dk`;
  const callsign = (a) => a.cs || a.reg || a.id.toUpperCase();
  const typeText = (a) => a.type || "Tip bilgisi yok";
  const matches = (a, q) => !q || [a.cs, a.reg, a.id, a.type].some((v) => v && v.toLowerCase().includes(q));
  const isShown = (a) => !state.hidden.has(a.kind);

  // ---------------------------------------------------------------- konum tahmini
  /** Son bilinen konumdan, hız ve yönle şimdiki konumu tahmin eder (düz yer yaklaşımı). */
  function predict(a, nowS) {
    if (reduced() || a.kind === KIND_GROUND || !a.gs || a.trk == null) return [a.lat, a.lon];
    const dt = Math.min(MAX_EXTRAP_S, Math.max(0, nowS - a.posTime));
    const dist = a.gs * KT_TO_MS * dt;
    const tr = a.trk * Math.PI / 180;
    const dLat = dist * Math.cos(tr) / EARTH_R * 180 / Math.PI;
    const dLon = dist * Math.sin(tr) / (EARTH_R * Math.cos(a.lat * Math.PI / 180)) * 180 / Math.PI;
    return [a.lat + dLat, a.lon + dLon];
  }
  function position(a, nowMs) {
    const p = predict(a, nowMs / 1000);
    if (a.corr) {
      const k = 1 - (nowMs - a.corr.t) / CORRECTION_MS;
      if (k > 0 && !reduced()) return [p[0] + a.corr.dLat * k, p[1] + a.corr.dLon * k];
      a.corr = null;
    }
    return p;
  }

  // ---------------------------------------------------------------- canvas katmanı
  const canvas = document.createElement("canvas");
  canvas.className = "ac-canvas";
  canvas.setAttribute("aria-hidden", "true");
  map.getContainer().appendChild(canvas);
  const ctx = canvas.getContext("2d");
  let dpr = 1, W = 0, H = 0;

  function resize() {
    const s = map.getSize();
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = s.x; H = s.y;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    buildSprites();
    requestDraw();
  }

  // Renkler CSS değişkenlerinden okunur (--k-airliner, --k-cargo, ...); tema değişince yeniden çizilir.
  let sprites = [];            // [kind][0 normal | 1 seçili] → canvas
  let spriteSize = 0;
  const iconSize = (z) => z < 4 ? 11 : z < 6 ? 14 : z < 8 ? 18 : 22;
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  function buildSprites() {
    spriteSize = iconSize(map.getZoom());
    const px = Math.ceil(spriteSize * dpr);
    const plane = new Path2D(PLANE_PATH), heli = new Path2D(HELI_PATH), jet = new Path2D(JET_PATH);
    const selColor = cssVar("--k-selected", "#ffffff");
    const outline = cssVar("--k-outline", "rgba(6, 8, 11, .85)");
    sprites = KINDS.map((k, i) => [false, true].map((sel) => {
      const c = document.createElement("canvas");
      c.width = c.height = px;
      const g = c.getContext("2d");
      g.scale(px / 24, px / 24);
      const path = i === KIND_HELI ? heli : i === KIND_FIGHTER ? jet : plane;
      g.lineJoin = "round";
      g.lineWidth = 2.2;
      g.strokeStyle = outline;
      g.stroke(path);
      g.fillStyle = sel ? selColor : cssVar(`--k-${k.key}`, "#e8743b");
      g.fill(path);
      return c;
    }));
  }

  // Ekrandaki konumlar (tıklama/üzerine gelme testi için son karede çizilenler)
  let hitIds = [], hitX = new Float32Array(0), hitY = new Float32Array(0), hitN = 0;
  let visibleCount = 0;

  const MAX_LAT = 85.0511287798;
  function draw() {
    const nowMs = Date.now();
    const z = map.getZoom();
    const scale = 256 * Math.pow(2, z);
    const tl = map.getPixelBounds().min;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (iconSize(z) !== spriteSize) buildSprites();

    const n = state.recs.size;
    if (hitX.length < n * 3) { hitX = new Float32Array(n * 3); hitY = new Float32Array(n * 3); }
    hitIds.length = 0; hitN = 0;
    const half = spriteSize / 2;
    const q = state.filter;
    let sel = null, selXY = null;
    const labels = [];

    for (const a of state.recs.values()) {
      if (!isShown(a)) continue;
      const p = position(a, nowMs);
      a.shown = p;
      const lat = Math.max(-MAX_LAT, Math.min(MAX_LAT, p[0]));
      const s = Math.sin(lat * Math.PI / 180);
      const y = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale - tl.y;
      if (y < -half || y > H + half) continue;
      // Dünya yatayda tekrar ettiği için ekrandaki her kopyada çizilir
      const x0 = (p[1] + 180) / 360 * scale - tl.x;
      for (let k = Math.ceil((-half - x0) / scale); x0 + k * scale <= W + half; k++) {
        const x = x0 + k * scale;
        if (a.id === state.selected) {           // en üstte çizilir; ekran ortasına en yakın kopya
          if (!selXY || Math.abs(x - W / 2) < Math.abs(selXY[0] - W / 2)) { sel = a; selXY = [x, y, k]; }
          continue;
        }
        hitIds[hitN] = a.id; hitX[hitN] = x; hitY[hitN] = y; hitN++;
        const dim = q && !a.match;
        ctx.globalAlpha = dim ? 0.18 : 1;
        drawIcon(sprites[a.kind][0], a.trk, x, y, a.kind === KIND_GROUND ? 0.75 : 1);
        if (!dim) labels.push(a, x, y);
      }
    }
    visibleCount = hitN;
    ctx.globalAlpha = 1;

    if (z >= LABEL_MIN_ZOOM && labels.length / 3 <= LABEL_MAX_COUNT) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = `600 11px ${cssVar("--font-mono", "monospace")}`;
      ctx.textBaseline = "middle";
      ctx.lineWidth = 3;
      ctx.strokeStyle = cssVar("--k-outline", "rgba(6, 8, 11, .85)");
      ctx.fillStyle = cssVar("--k-label", "#e2ded8");
      for (let i = 0; i < labels.length; i += 3) {
        const t = callsign(labels[i]);
        ctx.strokeText(t, labels[i + 1] + half + 4, labels[i + 2]);
        ctx.fillText(t, labels[i + 1] + half + 4, labels[i + 2]);
      }
    }

    if (sel) {
      const [x, y, wk] = selXY;
      hitIds[hitN] = sel.id; hitX[hitN] = x; hitY[hitN] = y; hitN++;
      drawTrail(sel, scale, { x: tl.x - wk * scale, y: tl.y }, x, y);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.beginPath();
      ctx.arc(x, y, half + 8, 0, Math.PI * 2);
      ctx.strokeStyle = cssVar("--k-selected", "#ffffff");
      ctx.lineWidth = 2;
      ctx.stroke();
      drawIcon(sprites[sel.kind][1], sel.trk, x, y, 1.25);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = `700 12px ${cssVar("--font-mono", "monospace")}`;
      ctx.textBaseline = "middle";
      const t = callsign(sel), tw = ctx.measureText(t).width;
      ctx.fillStyle = cssVar("--k-selected", "#ffffff");
      ctx.fillRect(x + half + 10, y - 9, tw + 10, 18);
      ctx.fillStyle = "#fff";
      ctx.fillText(t, x + half + 15, y);
    }
  }

  function drawIcon(sprite, trk, x, y, k) {
    const r = (trk || 0) * Math.PI / 180, c = Math.cos(r) * dpr * k, s = Math.sin(r) * dpr * k;
    ctx.setTransform(c, s, -s, c, x * dpr, y * dpr);
    ctx.drawImage(sprite, -spriteSize / 2, -spriteSize / 2, spriteSize, spriteSize);
  }

  /** Seçili uçağın bu oturumda toplanan izi (her güncellemede bir nokta). */
  function drawTrail(a, scale, tl, x, y) {
    const h = a.history;
    if (h.length < 1) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.beginPath();
    for (let i = 0; i < h.length; i++) {
      const s = Math.sin(Math.max(-MAX_LAT, Math.min(MAX_LAT, h[i].lat)) * Math.PI / 180);
      const px = (h[i].lon + 180) / 360 * scale - tl.x;
      const py = (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * scale - tl.y;
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.lineTo(x, y);
    ctx.strokeStyle = cssVar("--k-selected", "#ffffff");
    ctx.globalAlpha = 0.7;
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }

  // Çizim döngüsü: harita hareket ederken her karede, durgunken yakınlaştırmaya göre seyrek
  // (uzaktan bakınca uçaklar saniyede bir pikselden az ilerler).
  let dirty = true, zooming = false, lastDraw = 0;
  const requestDraw = () => { dirty = true; };
  const animInterval = (z) => reduced() ? Infinity : z < 4 ? 2000 : z < 6 ? 500 : z < 8 ? 120 : 50;
  function loop(ts) {
    requestAnimationFrame(loop);
    if (zooming || document.hidden) return;
    if (dirty || ts - lastDraw >= animInterval(map.getZoom())) {
      dirty = false;
      lastDraw = ts;
      draw();
    }
  }
  requestAnimationFrame(loop);

  map.on("move", () => { if (!zooming) draw(); });
  map.on("resize", resize);
  // Yakınlaştırma animasyonu: Leaflet karoları CSS ile ölçeklerken canvas da aynı dönüşümü alır.
  map.on("zoomanim", (e) => {
    zooming = true;
    const z0 = map.getZoom(), s = map.getZoomScale(e.zoom, z0), size = map.getSize();
    const oldC = map.project(map.getCenter(), z0), newC = map.project(e.center, z0);
    const tx = size.x / 2 * (1 - s) + s * (oldC.x - newC.x);
    const ty = size.y / 2 * (1 - s) + s * (oldC.y - newC.y);
    canvas.classList.add("ac-canvas--zoom");
    canvas.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
  });
  map.on("zoomend", () => {
    zooming = false;
    canvas.classList.remove("ac-canvas--zoom");
    canvas.style.transform = "";
    draw();
  });
  resize();

  // ---------------------------------------------------------------- tıklama ve üzerine gelme
  function hitTest(pt) {
    let best = null, bestD = HIT_RADIUS * HIT_RADIUS;
    for (let i = hitN - 1; i >= 0; i--) {   // sonda çizilen en üstte
      const dx = hitX[i] - pt.x, dy = hitY[i] - pt.y, d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = hitIds[i]; }
    }
    return best;
  }
  map.on("click", (e) => select(hitTest(e.containerPoint), { pan: false }));

  const tip = $("map-tip");
  let hoverPending = null;
  map.on("mousemove", (e) => {
    if (hoverPending) { hoverPending = e; return; }
    hoverPending = e;
    requestAnimationFrame(() => {
      const ev = hoverPending; hoverPending = null;
      const id = hitTest(ev.containerPoint);
      map.getContainer().classList.toggle("map--hit", !!id);
      const rec = id && state.recs.get(id);
      if (!rec) { tip.hidden = true; return; }
      tip.textContent = `${callsign(rec)} · ${typeText(rec)} · ${fmtFL(rec)}`;
      tip.style.transform = `translate(${ev.containerPoint.x + 14}px, ${ev.containerPoint.y + 14}px)`;
      tip.hidden = false;
    });
  });
  map.on("mouseout", () => { tip.hidden = true; });

  // ---------------------------------------------------------------- veri
  function apply(body) {
    const F = body.fields.reduce((o, f, i) => ((o[f] = i), o), {});
    const nowMs = Date.now();
    const seen = new Set();
    const counts = new Array(KINDS.length).fill(0);
    for (const r of body.ac) {
      const id = r[F.icao24];
      seen.add(id);
      const posTime = body.time - (r[F.seen_s] || 0);
      let a = state.recs.get(id);
      const before = a && a.shown;
      if (!a) {
        a = { id, history: [], corr: null, shown: null };
        state.recs.set(id, a);
      }
      a.cs = r[F.callsign]; a.reg = r[F.registration]; a.type = r[F.typecode];
      a.lat = r[F.lat]; a.lon = r[F.lon]; a.alt = r[F.alt_ft]; a.gs = r[F.gs_kt];
      a.trk = r[F.track]; a.vr = r[F.vrate_fpm]; a.kind = r[F.kind]; a.sq = r[F.squawk];
      a.posTime = posTime; a.lost = false;
      a.match = matches(a, state.filter);
      counts[a.kind]++;
      if (before) {
        const p = predict(a, nowMs / 1000);
        const dLat = before[0] - p[0], dLon = before[1] - p[1];
        // Büyük sıçramalarda (ör. uzun süre sinyal yok) yumuşatma yapılmaz
        a.corr = Math.abs(dLat) + Math.abs(dLon) < 0.5 ? { dLat, dLon, t: nowMs } : null;
      }
      const h = a.history, last = h[h.length - 1];
      if (!last || last.t !== posTime) {
        h.push({ t: posTime, alt: a.alt, lat: a.lat, lon: a.lon });
        if (h.length > HISTORY_LIMIT) h.shift();
      }
    }
    for (const id of state.recs.keys()) {
      if (seen.has(id)) continue;
      if (id === state.selected) state.selRec.lost = true;
      state.recs.delete(id);
    }
    if (state.selected && state.recs.has(state.selected)) state.selRec = state.recs.get(state.selected);
    state.counts = counts;
    state.data = { time: body.time, count: body.count };
    requestDraw();
    renderLegend();
    renderList();
    renderPanel();
    renderLive();
  }

  function schedule(seconds = CFG.pollSeconds) {
    clearTimeout(state.timer);
    state.timer = setTimeout(refresh, seconds * 1000);
  }

  async function refresh(fresh = false) {
    clearTimeout(state.timer);
    if (document.hidden) return;
    if (state.inflight) state.inflight.abort();
    const ctrl = new AbortController();
    state.inflight = ctrl;
    const timer = setTimeout(() => ctrl.abort(), 40000);
    try {
      const resp = await fetch(fresh === true ? "/api/flights/world?fresh=1" : "/api/flights/world", {
        signal: ctrl.signal, headers: state.etag ? { "If-None-Match": state.etag } : {},
      });
      if (resp.status === 304) {
        renderLive();
      } else {
        const body = await resp.json().catch(() => null);
        if (!resp.ok || !body) {
          const msg = (body && body.error && body.error.message) || `Sunucu hatası (HTTP ${resp.status}).`;
          throw new Error(msg);
        }
        state.etag = resp.headers.get("ETag");
        hideMsg();
        apply(body);
        openBoot();
      }
    } catch (err) {
      if (ctrl.signal.aborted && (state.inflight !== ctrl || document.hidden)) return;   // yenisi başladı / sekme gizlendi
      showMsg(`Canlı veri alınamadı: ${err.name === "AbortError" ? "sunucu zamanında yanıt vermedi" : err.message}. ` +
              `${CFG.pollSeconds} sn sonra yeniden denenecek.`);
      setLive("error", state.data ? "Güncellenemedi" : "Veri yok");
    } finally {
      clearTimeout(timer);
      if (state.inflight === ctrl) state.inflight = null;
    }
    schedule();
  }

  // ---------------------------------------------------------------- uçak fotoğrafı (Planespotters.net)
  // İstek tarayıcıdan atılır (API Origin/Referer bekler). Fotoğraf indirilmez/saklanmaz; yalnızca
  // API'nin döndürdüğü küçük resim adresi gösterilir. Sonuçlar sayfa açık kaldıkça bellekte tutulur.
  const PHOTO_API = "https://api.planespotters.net/pub/photos/hex/";
  const PHOTO_RETRY_MS = 60000;   // başarısız istek en erken bu süre sonra, uçak yeniden seçilince tekrarlanır
  const photoCache = new Map();   // icao24 → { promise: Promise<{src, link, photographer} | null>, failedAt }

  function httpsUrl(u) {
    try { return new URL(u).protocol === "https:"; } catch { return false; }
  }

  async function fetchPhoto(icao) {
    const resp = await fetch(PHOTO_API + encodeURIComponent(icao), { signal: AbortSignal.timeout(10000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    const p = body.photos && body.photos[0];
    const thumb = p && (p.thumbnail_large || p.thumbnail);
    if (!thumb || !httpsUrl(thumb.src) || !httpsUrl(p.link)) return null;   // üçüncü taraf verisi: yalnızca https
    return { src: thumb.src, link: p.link, photographer: p.photographer || "bilinmeyen fotoğrafçı" };
  }

  /** Kesin sonuç (fotoğraf ya da "fotoğraf yok") oturum boyunca bellekte kalır; aynı uçak tekrar
      seçilince istek atılmaz. Ağ/HTTP hatasında yer tutucu gösterilir, PHOTO_RETRY_MS dolmadan tekrar denenmez. */
  function loadPhoto(icao) {
    const hit = photoCache.get(icao);
    if (hit && !(hit.failedAt && Date.now() - hit.failedAt > PHOTO_RETRY_MS)) return hit.promise;
    const entry = { failedAt: 0 };
    entry.promise = fetchPhoto(icao).catch(() => { entry.failedAt = Date.now(); return null; });
    photoCache.set(icao, entry);
    return entry.promise;
  }

  const photoEls = {
    fig: $("photo"), skel: $("photo-skel"), link: $("photo-link"), img: $("photo-img"),
    empty: $("photo-empty"), credit: $("photo-credit"), creditLink: $("photo-credit-link"),
  };
  function photoState(kind) {        // "loading" | "photo" | "empty"
    photoEls.skel.hidden = kind !== "loading";
    photoEls.link.hidden = kind !== "photo";
    photoEls.credit.hidden = kind !== "photo";
    photoEls.empty.hidden = kind !== "empty";
    photoEls.fig.setAttribute("aria-busy", String(kind === "loading"));
  }

  async function showPhoto(icao) {
    photoState("loading");
    const photo = await loadPhoto(icao);
    if (state.selected !== icao) return;           // bu arada başka uçak seçildi
    if (!photo) return photoState("empty");
    const { img, link, creditLink } = photoEls;
    img.onload = () => { if (state.selected === icao) photoState("photo"); };
    img.onerror = () => { if (state.selected === icao) photoState("empty"); };
    img.alt = `${state.selRec ? callsign(state.selRec) : icao.toUpperCase()} uçağının fotoğrafı`;
    link.href = photo.link;
    creditLink.href = photo.link;
    creditLink.textContent = photo.photographer;
    img.src = photo.src;
    if (img.complete && img.naturalWidth) photoState("photo");   // tarayıcı önbelleğinden anında
  }

  // ---------------------------------------------------------------- ayrıntı (operatör, model)
  const detailCache = new Map();    // icao24 → Promise<detail | null>
  function loadDetail(icao) {
    if (!detailCache.has(icao)) {
      detailCache.set(icao, GR.api(`/api/flights/${encodeURIComponent(icao)}/detail`, { timeoutMs: 15000 })
        .catch(() => { detailCache.delete(icao); return null; }));
    }
    return detailCache.get(icao);
  }
  async function showDetail(icao) {
    const d = await loadDetail(icao);
    if (state.selected !== icao || !state.selRec) return;
    state.selRec.detail = d;
    renderPanel();
  }

  // ---------------------------------------------------------------- seçim ve panel
  function select(id, { pan = true } = {}) {
    const prev = state.selected;
    state.selected = id;
    state.selRec = id ? state.recs.get(id) || null : null;
    if (id !== prev) {
      if (id && state.selRec) loadRoute(state.selRec, true); else clearRoute();
    }
    if (id && id !== prev) {
      photoEls.img.removeAttribute("src");           // önceki uçağın fotoğrafı görünmesin
      showPhoto(id);
      showDetail(id);
      // Panel listeden aşağı kaydırılmışsa seçilen uçağın bilgileri görünsün
      document.querySelector(".map-panel").scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
    }
    if (id && pan && state.selRec && state.selRec.shown) {
      const target = state.selRec.shown;
      if (map.getZoom() < 6) map.flyTo(target, 7, { animate: !reduced(), duration: 1.2 });
      else map.panTo(target, { animate: !reduced() });
    }
    requestDraw();
    renderPanel();
    renderList();
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selected && document.activeElement !== $("search")) select(null);
  });
  $("sel-close").addEventListener("click", () => select(null));

  // ---------------------------------------------------------------- rota (kalkış → varış)
  // Rota adsbdb.com tarife verisinden gelir. Uçulan kısım (kalkış → uçak) düz, kalan kısım
  // (uçak → varış) kesikli büyük daire yolu olarak çizilir. Uçağın konumu yolun üzerinde değilse
  // tarife bu uçuşa ait olmayabilir: rota soluk çizilir ve panelde not düşülür.
  const routeLayer = L.layerGroup().addTo(map);
  const route = { id: null, data: null, req: 0 };
  const RAD = Math.PI / 180;
  const kmBetween = (a, b) => {
    const s = Math.sin((b[0] - a[0]) * RAD / 2) ** 2 +
      Math.cos(a[0] * RAD) * Math.cos(b[0] * RAD) * Math.sin((b[1] - a[1]) * RAD / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(Math.min(1, s)));
  };
  /** İki nokta arasındaki büyük daire yolu; 180. boylamı geçtiği yerde parçalara bölünür. */
  function greatCircle(a, b, n = 96) {
    const la1 = a[0] * RAD, lo1 = a[1] * RAD, la2 = b[0] * RAD, lo2 = b[1] * RAD;
    const d = kmBetween(a, b) / 6371;
    if (d < 1e-6) return [[a, b]];
    const segs = [[]];
    let prev = null;
    for (let i = 0; i <= n; i++) {
      const f = i / n, A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(la1) * Math.cos(lo1) + B * Math.cos(la2) * Math.cos(lo2);
      const y = A * Math.cos(la1) * Math.sin(lo1) + B * Math.cos(la2) * Math.sin(lo2);
      const z = A * Math.sin(la1) + B * Math.sin(la2);
      const pt = [Math.atan2(z, Math.hypot(x, y)) / RAD, Math.atan2(y, x) / RAD];
      if (prev && Math.abs(pt[1] - prev[1]) > 180) segs.push([]);
      segs[segs.length - 1].push(pt);
      prev = pt;
    }
    return segs;
  }
  const apCode = (ap) => ap.iata || ap.icao || "—";
  const apCity = (ap) => (ap.city || ap.name || "").split(",").pop().trim();

  function clearRoute() {
    route.req++;
    route.id = null;
    route.data = null;
    routeLayer.clearLayers();
    $("route").hidden = true;
    $("route-msg").hidden = true;
  }
  function routeMessage(text) {
    $("route").hidden = true;
    $("route-msg").textContent = text;
    $("route-msg").hidden = false;
  }
  async function loadRoute(rec, fit) {
    clearRoute();
    route.id = rec.id;
    const token = route.req;
    const cs = (rec.cs || "").toUpperCase();
    if (!/^[A-Z0-9]{2,8}$/.test(cs)) return routeMessage("Çağrı kodu yayınlamadığı için rotası bilinmiyor.");
    routeMessage("Rota yükleniyor…");
    const pos = rec.shown || [rec.lat, rec.lon];
    try {
      const data = await GR.api(`/api/flights/route/${encodeURIComponent(cs)}?lat=${pos[0].toFixed(3)}&lon=${pos[1].toFixed(3)}`,
                                { timeoutMs: 15000 });
      if (token !== route.req) return;
      route.data = data;
      renderRoute(fit);
    } catch (err) {
      if (token !== route.req) return;
      routeMessage(err.status === 404
        ? "Bu uçuşun rotası bilinmiyor (askeri, özel ya da tarifesiz bir uçuş olabilir)."
        : "Rota bilgisi şu an alınamadı.");
    }
  }
  const fmtDuration = (min) => {
    const h = Math.floor(min / 60), m = Math.round(min % 60);
    return h ? `${h} sa ${m} dk` : `${m} dk`;
  };
  function renderRoute(fit = false) {
    const d = route.data, rec = state.selRec;
    if (!d || !rec || rec.id !== route.id || d.origin.lat == null || d.destination.lat == null) return;
    const o = [d.origin.lat, d.origin.lon], t = [d.destination.lat, d.destination.lon];
    const pos = rec.shown || [rec.lat, rec.lon];
    const total = kmBetween(o, t), flown = kmBetween(o, pos), left = kmBetween(pos, t);
    const plausible = !rec.lost && flown + left <= total * 1.25 + 250;
    const frac = flown + left > 0 ? flown / (flown + left) : 0;

    $("route").hidden = false;
    $("route-msg").hidden = true;
    $("route-o-code").textContent = apCode(d.origin);
    $("route-o-city").textContent = apCity(d.origin);
    $("route-o-city").title = d.origin.name || "";
    $("route-d-code").textContent = apCode(d.destination);
    $("route-d-city").textContent = apCity(d.destination);
    $("route-d-city").title = d.destination.name || "";
    $("route").style.setProperty("--f", `${(plausible ? frac * 100 : 0).toFixed(1)}%`);
    $("route-plane").hidden = !plausible;
    const parts = [];
    if (plausible) {
      parts.push(`${GR.fmtNum(Math.round(left))} km kaldı`);
      if (rec.gs > 80 && rec.kind !== KIND_GROUND) parts.push(`~${fmtDuration(left / (rec.gs * 1.852) * 60)}`);
    } else {
      parts.push(`${GR.fmtNum(Math.round(total))} km`);
    }
    if (d.airline && d.airline.name) parts.push(d.airline.name);
    $("route-meta").textContent = parts.join(" · ");
    $("route-note").hidden = plausible;
    $("route-note").textContent = "Tarifedeki rota uçağın şu anki konumuyla uyuşmuyor; bu sefer farklı bir uçuş olabilir.";

    routeLayer.clearLayers();
    const line = (a, b, style) => greatCircle(a, b).forEach((seg) => L.polyline(seg, { interactive: false, ...style }).addTo(routeLayer));
    if (plausible) {
      line(o, pos, { color: cssVar("--route-flown", "#E8743B"), weight: 3, opacity: 0.95 });
      // Kalan kısım: kesikler varışa doğru akar (yön)
      line(pos, t, { color: cssVar("--route-left", "#FFFFFF"), weight: 2, opacity: 0.85, dashArray: "6 8", className: "route-left" });
    } else {
      line(o, t, { color: "#A7A39D", weight: 2, opacity: 0.5, dashArray: "4 8" });
    }
    for (const [ap, at] of [[d.origin, o], [d.destination, t]]) {
      L.circleMarker(at, { radius: 5, color: "#E8743B", weight: 2, fillColor: "#0B0D10", fillOpacity: 1, interactive: false })
        .bindTooltip(apCode(ap), { permanent: true, direction: "top", offset: [0, -6], className: "ap-label" })
        .addTo(routeLayer);
    }
    if (fit) {
      const wide = innerWidth > 1100;
      map.fitBounds(L.latLngBounds([o, t, pos]), {
        paddingTopLeft: [60, 150], paddingBottomRight: [wide ? 460 : 60, 70], maxZoom: 8, animate: !reduced(),
      });
    }
  }

  function renderPanel() {
    const rec = state.selRec;
    $("sel-empty").hidden = !!rec;
    $("sel-body").hidden = !rec;
    $("sel-age").hidden = !rec;
    if (!rec) return;
    const d = rec.detail || {};
    $("sel-cs").textContent = callsign(rec);
    const kind = KINDS[rec.kind] ? KINDS[rec.kind].label : "";
    $("sel-kind").textContent = kind;
    $("sel-kind").dataset.kind = KINDS[rec.kind] ? KINDS[rec.kind].key : "";
    $("sel-sub").textContent = [d.operator, d.description || rec.type].filter(Boolean).join(" · ") || typeText(rec);
    $("sel-alt").textContent = fmtAlt(rec);
    $("sel-spd").textContent = fmtSpd(rec);
    $("sel-hdg").textContent = fmtHdg(rec);
    $("sel-vs").textContent = fmtVs(rec);
    $("sel-reg").textContent = rec.reg || d.registration || "—";
    $("sel-icao").textContent = rec.id;
    $("sel-type").textContent = rec.type || d.typecode || "—";
    $("sel-sq").textContent = rec.sq || "—";
    $("sel-lost").hidden = !rec.lost;
    renderSignalAge();
    renderSpark(rec);
    renderRoute();
  }

  function renderSignalAge() {
    const rec = state.selRec;
    if (!rec) return;
    const sec = Math.max(0, Math.round(Date.now() / 1000 - rec.posTime));
    $("sel-age").textContent = rec.lost ? "sinyal yok" : `sinyal ${GR.fmtNum(sec)} sn önce`;
  }

  function renderSpark(rec) {
    const h = rec.history.filter((p) => p.alt != null);
    const line = $("spark-line"), area = $("spark-area");
    if (h.length < 2) {
      line.setAttribute("points", "");
      area.setAttribute("points", "");
      $("spark-label").textContent = "İrtifa geçmişi — veri toplanıyor (her güncellemede bir ölçüm eklenir)";
      return;
    }
    const t0 = h[0].t, t1 = h[h.length - 1].t;
    const maxAlt = Math.max(10000, ...h.map((p) => p.alt));
    const pts = h.map((p) => {
      const x = t1 === t0 ? 0 : (p.t - t0) / (t1 - t0) * 324;
      const y = 60 - Math.max(0, p.alt) / maxAlt * 52;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    line.setAttribute("points", pts.join(" "));
    area.setAttribute("points", `0,64 ${pts.join(" ")} ${pts[pts.length - 1].split(",")[0]},64`);
    const minutes = Math.max(1, Math.round((t1 - t0) / 60));
    $("spark-label").textContent = `İrtifa geçmişi · son ${GR.fmtNum(minutes)} dk (bu oturumda ${h.length} ölçüm)`;
  }

  // ---------------------------------------------------------------- tür filtresi (lejant)
  const legendEl = $("legend");
  function buildLegend() {
    try { (JSON.parse(localStorage.getItem("gokradar.hiddenKinds")) || []).forEach((k) => state.hidden.add(k)); }
    catch (_) { /* yok */ }
    legendEl.replaceChildren(...LEGEND_ORDER.map((i) => {
      const k = KINDS[i];
      const b = document.createElement("button");
      b.type = "button";
      b.className = "legend__item";
      b.dataset.kind = k.key;
      b.dataset.index = i;
      b.innerHTML = '<span class="legend__dot" aria-hidden="true"></span><span class="legend__label"></span><span class="legend__count"></span>';
      b.children[1].textContent = k.label;
      return b;
    }));
    renderLegend();
  }
  function renderLegend() {
    for (const b of legendEl.children) {
      const i = Number(b.dataset.index);
      b.setAttribute("aria-pressed", String(!state.hidden.has(i)));
      b.children[2].textContent = state.data ? GR.fmtNum(state.counts[i]) : "";
    }
  }
  legendEl.addEventListener("click", (e) => {
    const b = e.target.closest(".legend__item");
    if (!b) return;
    const i = Number(b.dataset.index);
    if (state.hidden.has(i)) state.hidden.delete(i); else state.hidden.add(i);
    try { localStorage.setItem("gokradar.hiddenKinds", JSON.stringify([...state.hidden])); } catch (_) { /* yok */ }
    renderLegend();
    renderList();
    requestDraw();
  });
  buildLegend();

  // ---------------------------------------------------------------- liste ve arama
  // Arama boşken ekranda görünen uçaklar, arama varken dünya genelindeki eşleşmeler listelenir.
  const listEl = $("list");
  function renderList() {
    const q = state.filter;
    let items;
    if (q) {
      items = [...state.recs.values()].filter((a) => a.match && isShown(a));
    } else {
      const b = map.getBounds();
      const inView = ([lat, lon]) => b.contains([lat, lon]) || b.contains([lat, lon + 360]) || b.contains([lat, lon - 360]);
      items = [...state.recs.values()].filter((a) => isShown(a) && inView(a.shown || [a.lat, a.lon]));
    }
    items.sort((x, y) => (y.id === state.selected) - (x.id === state.selected)
                         || (x.kind === KIND_GROUND) - (y.kind === KIND_GROUND)
                         || callsign(x).localeCompare(callsign(y), "tr"));
    $("list-title").textContent = q ? "ARAMA SONUÇLARI" : "GÖRÜNEN UÇAKLAR";
    $("list-count").textContent = state.recs.size ? `· ${GR.fmtNum(items.length)}` : "";
    const frag = document.createDocumentFragment();
    if (!items.length) {
      const p = document.createElement("p");
      p.className = "small ac-list__empty";
      p.textContent = q ? "Aramaya uyan uçak yok." :
        state.data ? "Bu bölgede şu an uçak görünmüyor." : "Uçak verisi bekleniyor…";
      frag.append(p);
    }
    for (const a of items.slice(0, LIST_LIMIT)) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ac-row";
      b.dataset.id = a.id;
      b.dataset.kind = KINDS[a.kind].key;
      b.setAttribute("aria-pressed", String(a.id === state.selected));
      b.innerHTML = '<span class="ac-row__cs"></span><span class="ac-row__type"></span><span class="ac-row__fl"></span>';
      b.children[0].textContent = callsign(a);
      b.children[1].textContent = typeText(a);
      b.children[2].textContent = fmtFL(a);
      frag.append(b);
    }
    if (items.length > LIST_LIMIT) {
      const p = document.createElement("p");
      p.className = "small ac-list__empty";
      p.textContent = `+${GR.fmtNum(items.length - LIST_LIMIT)} uçak daha — ${q ? "aramayı daraltın" : "yakınlaştırın ya da arayın"}.`;
      frag.append(p);
    }
    listEl.replaceChildren(frag);
  }
  listEl.addEventListener("click", (e) => {
    const row = e.target.closest(".ac-row");
    if (row) select(row.dataset.id);
  });

  const search = $("search");
  let searchTimer = null;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.filter = search.value.trim().toLowerCase();
      for (const a of state.recs.values()) a.match = matches(a, state.filter);
      requestDraw();
      renderList();
    }, 120);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      clearTimeout(searchTimer);
      state.filter = search.value.trim().toLowerCase();
      for (const a of state.recs.values()) a.match = matches(a, state.filter);
      renderList();
      const first = listEl.querySelector(".ac-row");
      if (first) select(first.dataset.id);
    } else if (e.key === "Escape") {
      search.value = "";
      search.dispatchEvent(new Event("input"));
    }
  });

  // ---------------------------------------------------------------- durum çipleri
  function setLive(kind, text) {
    state.liveState = kind;
    $("live").dataset.state = kind;
    $("live-text").textContent = text;
    $("nav-live").dataset.state = kind;
  }
  function renderLive() {
    const d = state.data;
    if (!d) return;
    $("nav-live-count").textContent = GR.fmtNum(d.count);
    const age = Math.max(0, Math.round(Date.now() / 1000 - d.time));
    const stale = age > 60;
    setLive(stale ? "stale" : "ok",
            `${stale ? "GECİKMELİ" : "CANLI"} · ${GR.fmtNum(d.count)} uçak · ${GR.fmtNum(age)} sn önce`);
  }
  function renderInfo() {
    $("map-info").textContent = `Ekranda ${GR.fmtNum(visibleCount)} uçak · adsb.lol`;
  }

  // Açılış kâğıdı: ilk veri gelince silme çizgisiyle açılır, sonra DOM'dan kalkar
  const boot = $("map-boot");
  function openBoot() {
    if (!boot || boot.classList.contains("is-open")) return;
    boot.classList.add("is-open");
    boot.querySelector(".map-boot__paper").addEventListener("transitionend", () => boot.remove(), { once: true });
    setTimeout(() => boot.isConnected && boot.remove(), 1600);
  }
  setInterval(() => {
    if (state.liveState === "ok" || state.liveState === "stale") renderLive();
    renderSignalAge();
    renderInfo();
  }, 1000);

  // ---------------------------------------------------------------- tetikleyiciler
  let moveTimer = null;
  map.on("moveend", () => {
    try {
      localStorage.setItem("gokradar.mapView",
                           JSON.stringify({ center: map.getCenter(), zoom: map.getZoom() }));
    } catch (_) { /* depolama yok */ }
    clearTimeout(moveTimer);
    moveTimer = setTimeout(renderList, 150);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(state.timer);
      if (state.inflight) state.inflight.abort();
    } else {
      refresh(true);
    }
  });
  GR.motionQuery.addEventListener("change", requestDraw);

  const refreshBtn = $("refresh");
  refreshBtn.addEventListener("click", async () => {
    if (refreshBtn.classList.contains("is-spinning")) return;
    refreshBtn.classList.add("is-spinning");
    state.etag = null;                         // sunucudaki son görüntüyü koşulsuz al
    try { await refresh(true); } finally {
      setTimeout(() => refreshBtn.classList.remove("is-spinning"), 400);
    }
  });

  setLive("loading", "Uçaklar yükleniyor…");
  renderList();
  refresh(true);
})();
