/* Ana sayfa: kaydırmaya bağlı render dizisi (Apple ürün sayfalarındaki yöntem).
   Kaydırma oranı → kare numarası; kare canvas'a "cover" olarak çizilir. Kareler önce duraklar
   (açılış, kokpit, pencere, tepe), sonra seyrek, en son tümü olacak şekilde yüklenir; henüz
   yüklenmemiş bir kare istenirse en yakın yüklü kare çizilir. Kartlar data-from/to aralığında. */
(() => {
  "use strict";
  const canvas = document.getElementById("scene");
  const ctx = canvas.getContext("2d", { alpha: false });
  const N = Number(canvas.dataset.frames);
  const set = window.matchMedia("(max-width: 900px)").matches ? "m" : "d";
  const url = (i) => `${canvas.dataset.src}${set}/${String(i + 1).padStart(3, "0")}.webp`;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const frames = new Array(N);
  const beats = [...document.querySelectorAll(".beat")];
  const nav = document.getElementById("nav");
  const shade = document.getElementById("shade");
  let current = 0, target = 0, drawn = -1, loaded = 0;

  canvas.parentElement.style.backgroundImage = `url("${url(0)}")`;

  // --- yükleme sırası: duraklar → her 6. kare → kalanlar --------------------------------------
  const order = [];
  const push = (i) => { if (i >= 0 && i < N && !order.includes(i)) order.push(i); };
  [0, Math.round(N * 0.33), Math.round(N * 0.64), N - 1].forEach(push);
  for (let i = 0; i < N; i += 6) push(i);
  for (let i = 0; i < N; i++) push(i);

  const fill = document.getElementById("loadbar-fill");
  function load(i) {
    return new Promise((resolve) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => { frames[i] = img; loaded++; fill.style.width = `${(loaded / N) * 100}%`; resolve(); };
      img.onerror = resolve;
      img.src = url(i);
    });
  }
  async function loadAll() {
    const queue = reduced ? [0] : order;
    const workers = 6;
    let k = 0;
    await Promise.all(Array.from({ length: workers }, async () => {
      while (k < queue.length) {
        const i = queue[k++];
        await load(i);
        if (i === Math.round(current)) drawn = -1;   // beklenen kare geldi: yeniden çiz
      }
    }));
    document.getElementById("loadbar").classList.add("is-done");
  }

  // --- çizim ---------------------------------------------------------------------------
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(innerWidth * dpr);
    canvas.height = Math.round(innerHeight * dpr);
    drawn = -1;
  }
  function nearest(i) {
    for (let d = 0; d < N; d++) {
      if (frames[i - d]) return frames[i - d];
      if (frames[i + d]) return frames[i + d];
    }
    return null;
  }
  function draw(i) {
    const img = frames[i] || nearest(i);
    if (!img) return false;
    const cw = canvas.width, ch = canvas.height;
    const s = Math.max(cw / img.width, ch / img.height);
    const w = img.width * s, h = img.height * s;
    ctx.drawImage(img, (cw - w) / 2, (ch - h) / 2, w, h);
    return Boolean(frames[i]);
  }

  // --- kaydırma --------------------------------------------------------------------------
  const progress = () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    return max > 0 ? Math.min(1, Math.max(0, scrollY / max)) : 0;
  };
  let lastY = scrollY;
  function onScroll() {
    const p = progress();
    target = p * (N - 1);
    for (const b of beats) {
      b.classList.toggle("is-on", p >= Number(b.dataset.from) && p < Number(b.dataset.to));
    }
    shade.classList.toggle("is-on", p < 0.07);
    const dy = scrollY - lastY;
    if (Math.abs(dy) > 6) nav.classList.toggle("is-hidden", dy > 0 && scrollY > 120);
    lastY = scrollY;
    if (p > 0.12) loadLiveCount();
  }

  function tick() {
    // Yumuşak takip: kare, kaydırmanın gösterdiği kareye her karede biraz yaklaşır (atalet hissi)
    current += (target - current) * 0.16;
    if (Math.abs(target - current) < 0.01) current = target;
    const i = Math.round(current);
    if (i !== drawn && draw(i)) drawn = i;
    else if (drawn === -1) draw(i);
    requestAnimationFrame(tick);
  }

  // --- canlı uçak sayısı (yalnızca kokpit durağına yaklaşınca, bir kez) ---------------------
  let liveAsked = false;
  async function loadLiveCount() {
    if (liveAsked || !window.GR) return;
    liveAsked = true;
    try {
      const body = await GR.api("/api/flights/world", { timeoutMs: 40000 });
      document.getElementById("live-count").textContent = GR.fmtNum(body.count);
    } catch (_) { /* sayı gösterilmez, kart yine çalışır */ }
  }

  resize();
  addEventListener("resize", resize);
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  loadAll();
  if (reduced) {
    beats.forEach((b) => b.classList.add("is-on"));
    load(0).then(() => draw(0));
    loadLiveCount();
  } else {
    requestAnimationFrame(tick);
  }
})();
