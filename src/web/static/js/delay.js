/* Gecikme tahmini: havalimanlarını /api/airports'tan yükler, /api/predict-delay'e gönderir,
   sonucu gösterge, gün içi profil ve METAR kutucuklarıyla gösterir. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ARC_LEN = Math.PI * 140;                     // yarım çember uzunluğu (r = 140)
  const COMPARE_TIMES = ["06:00", "09:00", "12:00", "15:00", "18:00", "21:00"];
  const FIELDS = ["origin", "dest", "airline", "date", "dep_time"];
  const form = $("delay-form"), submit = $("submit"), status = $("form-status");
  const result = $("result");

  // --- varsayılanlar: bugün, bir sonraki tam saat ---
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  $("date").value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  $("dep_time").value = `${pad((now.getHours() + 1) % 24)}:00`;
  for (const id of ["dest", "airline"]) {
    $(id).addEventListener("input", (e) => { e.target.value = e.target.value.toUpperCase(); });
  }

  // --- havalimanları ---
  async function loadAirports() {
    const sel = $("origin");
    try {
      const { airports } = await GR.api("/api/airports", { timeoutMs: 10000 });
      sel.replaceChildren(...airports.map((a) => new Option(`${a.iata} — ${a.name || a.icao}`, a.iata)));
      sel.disabled = false;
      setFieldError("origin", null);
    } catch (err) {
      sel.replaceChildren(new Option("Havalimanları yüklenemedi", ""));
      setFieldError("origin", `${err.message} Sayfayı yenileyerek tekrar deneyin.`);
    }
  }
  loadAirports();

  // --- alan hataları ---
  function setFieldError(field, message) {
    const el = $(`${field}-error`), input = $(field);
    el.hidden = !message;
    el.textContent = message || "";
    if (message) input.setAttribute("aria-invalid", "true"); else input.removeAttribute("aria-invalid");
  }
  const clearErrors = () => FIELDS.forEach((f) => setFieldError(f, null));

  function validate(values) {
    const errors = {};
    if (!values.origin) errors.origin = "Kalkış havalimanı seçin.";
    if (!/^[A-Z]{3}$/.test(values.dest)) errors.dest = "3 harfli IATA kodu girin (ör. BOS).";
    if (!/^[A-Z0-9]{2,3}$/.test(values.airline)) errors.airline = "2-3 karakterli havayolu kodu girin (ör. DL).";
    if (!values.date) errors.date = "Tarih seçin.";
    if (!/^\d{2}:\d{2}$/.test(values.dep_time)) errors.dep_time = "Saat seçin (SS:DD).";
    return errors;
  }

  // Sunucu hata kodlarını ilgili alana eşle
  const ERROR_FIELD = { unsupported_origin: "origin", invalid_airline: "airline", invalid_date: "date",
                        invalid_time: "dep_time" };
  function fieldForError(err) {
    if (err.code === "invalid_airport") return err.message.startsWith("origin") ? "origin" : "dest";
    if (err.code === "missing_fields" && err.details) return err.details.missing[0];
    return ERROR_FIELD[err.code] || null;
  }

  // --- gönderim ---
  let inflight = null;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearErrors();
    const values = Object.fromEntries(FIELDS.map((f) => [f, $(f).value.trim().toUpperCase()]));
    const errors = validate(values);
    if (Object.keys(errors).length) {
      Object.entries(errors).forEach(([f, m]) => setFieldError(f, m));
      $(Object.keys(errors)[0]).focus();
      GR.setStatus(status, "error", "Lütfen işaretli alanları düzeltin.");
      return;
    }
    if (inflight) inflight.abort();
    inflight = new AbortController();
    submit.disabled = true;
    submit.textContent = "Hesaplanıyor…";
    result.setAttribute("aria-busy", "true");
    GR.setStatus(status, "loading", "Tahmin hesaplanıyor — kalkış havalimanının güncel METAR'ı alınıyor…");
    try {
      const data = await GR.api("/api/predict-delay", {
        method: "POST", signal: inflight.signal, timeoutMs: 30000,
        json: { ...values, compare_times: COMPARE_TIMES },
      });
      render(data);
      GR.setStatus(status, null);
    } catch (err) {
      if (err.name === "AbortError") return;
      const field = fieldForError(err);
      if (field) { setFieldError(field, err.message); $(field).focus(); }
      GR.setStatus(status, "error", `Tahmin yapılamadı: ${err.message}`);
    } finally {
      submit.disabled = false;
      submit.textContent = "Tahmini hesapla";
      result.setAttribute("aria-busy", "false");
    }
  });

  // --- sonuç ---
  const restart = (el) => { el.style.animation = "none"; void el.getBoundingClientRect(); el.style.animation = ""; };
  const dateTr = (iso) => iso.split("-").reverse().join(".");

  function render(d) {
    $("r-route").textContent = `${d.airline} · ${d.origin} → ${d.dest} · ${d.dep_time} · ${dateTr(d.date)}`;
    const chip = $("r-chip");
    chip.hidden = false;
    chip.textContent = d.is_delayed ? "Gecikme riski yüksek" : "Gecikme riski düşük";
    chip.dataset.level = d.is_delayed ? "high" : "low";
    chip.title = `Karar eşiği ${GR.fmtPct(d.threshold, 1)}: bu değerin üstü “yüksek risk” sayılır.`;

    // gösterge yayı
    const arc = $("r-arc");
    const len = Math.max(0.5, d.delay_probability * ARC_LEN);
    arc.style.display = "";
    arc.setAttribute("stroke-dasharray", `${len.toFixed(1)} 440`);
    arc.style.setProperty("--arc-len", len.toFixed(1));
    restart(arc);
    $("r-pct").textContent = GR.fmtPct(d.delay_probability);

    renderProfile(d.time_profile || []);
    renderWeather(d.origin, d.weather);

    const notes = $("r-notes");
    notes.replaceChildren(...(d.notes || []).map((n) => Object.assign(document.createElement("li"), { textContent: n })));
    notes.hidden = !(d.notes && d.notes.length);
  }

  function renderProfile(profile) {
    const box = $("r-profile"), note = $("r-profile-note");
    if (!profile.length) {
      box.replaceChildren(Object.assign(document.createElement("p"), {
        className: "small", textContent: "Gün içi karşılaştırma alınamadı." }));
      note.hidden = true;
      return;
    }
    box.replaceChildren(...profile.map((p, i) => {
      const row = document.createElement("div");
      row.className = "profile__row" + (p.selected ? " profile__row--sel" : "");
      row.innerHTML = '<span class="profile__time mono"></span><div class="bar-track"><div class="bar"></div></div>' +
                      '<span class="profile__pct mono"></span>';
      row.querySelector(".profile__time").textContent = p.dep_time;
      const bar = row.querySelector(".bar");
      bar.style.width = `${Math.max(1, p.probability * 100).toFixed(1)}%`;
      bar.style.animationDelay = `${(i * 0.08).toFixed(2)}s`;
      if (p.selected) bar.classList.add("bar--amber");
      row.querySelector(".profile__pct").textContent = GR.fmtPct(p.probability);
      row.setAttribute("aria-label", `${p.dep_time}: ${GR.fmtPct(p.probability)}${p.selected ? " (seçilen saat)" : ""}`);
      return row;
    }));
    const lo = profile.reduce((a, b) => (b.probability < a.probability ? b : a));
    const hi = profile.reduce((a, b) => (b.probability > a.probability ? b : a));
    note.hidden = false;
    note.textContent = hi.probability - lo.probability < 0.03
      ? "Gün içinde saatler arasında belirgin bir fark görünmüyor."
      : `En düşük risk ${lo.dep_time} (${GR.fmtPct(lo.probability)}), en yüksek ${hi.dep_time} ` +
        `(${GR.fmtPct(hi.probability)}). Rötarlar gün içinde birikme eğilimindedir.`;
  }

  function renderWeather(origin, w) {
    const set = (id, text) => { $(id).textContent = text; };
    const raw = $("r-raw");
    if (!w) {
      $("r-wx-title").textContent = `${origin} güncel hava durumu alınamadı — tahmin hava verisi olmadan yapıldı`;
      ["r-wind", "r-vis", "r-ceil", "r-precip"].forEach((id) => set(id, "—"));
      raw.hidden = true;
      return;
    }
    const time = w.valid_utc ? ` · ${w.valid_utc.slice(11, 16)} UTC` : "";
    $("r-wx-title").textContent = `${origin} güncel hava durumu (METAR${time})`;
    set("r-wind", w.wind_kt == null ? "—" :
      `${GR.fmtNum(w.wind_kt)} kt${w.gust_kt ? ` · hamle ${GR.fmtNum(w.gust_kt)}` : ""}`);
    set("r-vis", w.visibility_mi == null ? "—" : `${GR.fmtNum(w.visibility_mi, w.visibility_mi % 1 ? 1 : 0)}${w.visibility_mi >= 10 ? "+" : ""} sm`);
    set("r-ceil", w.ceiling_ft == null ? "Tavan yok" : `${GR.fmtNum(w.ceiling_ft)} ft`);
    set("r-precip", w.precip == null ? "—" : w.precip ? "Var" : "Yok");
    raw.hidden = !w.raw;
    raw.textContent = w.raw || "";
  }
})();
