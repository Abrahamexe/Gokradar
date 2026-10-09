/* Uçak tanıma: fotoğraf seçme/sürükleme, istemci tarafı kontrol, /api/classify-aircraft,
   top-3 (benzer aileler birleşik) sonuç çubukları. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const input = $("file"), zone = $("dropzone"), status = $("c-status"), result = $("result");
  const MAX_BYTES = Number(input.dataset.maxMb) * 1024 * 1024;
  const OK_TYPES = ["image/jpeg", "image/png"];
  const OK_EXT = /\.(jpe?g|png)$/i;
  let previewUrl = null, inflight = null;
  const pct = (v) => (v < 0.005 ? "<%1" : GR.fmtPct(v));   // çok küçük olasılıklar "%0" görünmesin

  const setAnalyzing = (on) => {
    // Tarama çizgisi fotoğraf yokken ve analiz sırasında görünür; sonuç gelince durur.
    const hasPhoto = !$("photo-img").hidden;
    $("scan").hidden = hasPhoto && !on;
    result.setAttribute("aria-busy", String(on));
    zone.classList.toggle("dropzone--busy", on);
  };

  function showPreview(file) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    const img = $("photo-img");
    img.src = previewUrl;
    img.alt = `Yüklenen fotoğraf: ${file.name}`;
    img.hidden = false;
    $("photo-empty").hidden = true;
  }

  function checkFile(file) {
    if (!OK_TYPES.includes(file.type) && !OK_EXT.test(file.name)) {
      return "Yalnızca JPG veya PNG dosyası yüklenebilir.";
    }
    if (file.size === 0) return "Dosya boş.";
    if (file.size > MAX_BYTES) {
      return `Dosya ${GR.fmtNum(file.size / 1024 / 1024, 1)} MB; en fazla ${input.dataset.maxMb} MB olabilir.`;
    }
    return null;
  }

  async function classify(file) {
    const problem = checkFile(file);
    if (problem) {
      GR.setStatus(status, "error", problem);
      return;
    }
    showPreview(file);
    $("c-chip").hidden = false;
    $("c-chip").textContent = file.name.length > 28 ? file.name.slice(0, 25) + "…" : file.name;
    $("ranks").replaceChildren();
    GR.setStatus(status, "loading", "Fotoğraf yükleniyor ve analiz ediliyor…");
    setAnalyzing(true);

    if (inflight) inflight.abort();
    inflight = new AbortController();
    const body = new FormData();
    body.append("image", file, file.name);
    try {
      const data = await GR.api("/api/classify-aircraft", { method: "POST", body, signal: inflight.signal,
                                                            timeoutMs: 60000 });
      GR.setStatus(status, null);
      render(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      GR.setStatus(status, "error", `Sınıflandırma yapılamadı: ${err.message}`);
      $("ranks").replaceChildren(Object.assign(document.createElement("p"), {
        className: "small", textContent: "Başka bir fotoğraf seçerek tekrar deneyebilirsiniz." }));
    } finally {
      setAnalyzing(false);
    }
  }

  function render(data) {
    const rows = data.top_groups && data.top_groups.length ? data.top_groups
      : data.predictions.map((p) => ({ ...p, grouped: false, breakdown: [] }));
    const frag = document.createDocumentFragment();
    rows.forEach((r, i) => {
      const row = document.createElement("div");
      row.className = "rank" + (i === 0 ? " rank--top" : "");
      row.innerHTML =
        '<div class="rank__head"><span class="rank__label"></span><span class="rank__pct mono"></span></div>' +
        '<div class="bar-track"><div class="bar"></div></div>';
      row.querySelector(".rank__label").textContent = r.label;
      row.querySelector(".rank__pct").textContent = pct(r.score);
      const bar = row.querySelector(".bar");
      bar.style.width = `${Math.max(1, r.score * 100).toFixed(1)}%`;
      bar.style.animationDelay = `${(i * 0.2).toFixed(1)}s`;
      if (i === 0) bar.classList.add("bar--accent");
      if (r.grouped) {
        const badge = document.createElement("span");
        badge.className = "rank__badge";
        badge.textContent = "benzer aileler";
        row.querySelector(".rank__label").append(" ", badge);
        const detail = document.createElement("span");
        detail.className = "rank__detail small";
        detail.textContent = r.breakdown.map((b) => `${b.label} ${pct(b.score)}`).join(" · ");
        row.append(detail);
      }
      row.setAttribute("aria-label", `${i + 1}. ${r.label}: ${pct(r.score)}`);
      frag.append(row);
    });
    if (rows[0] && rows[0].score < 0.4) {
      const warn = document.createElement("p");
      warn.className = "small rank__warn";
      warn.textContent = "Model bu fotoğraftan emin değil: uçağın tamamı görünmüyor olabilir ya da tip veri setinde bulunmuyor.";
      frag.append(warn);
    }
    $("ranks").replaceChildren(frag);
  }

  // --- seçme ve sürükle-bırak ---
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) classify(input.files[0]);
    input.value = "";                                   // aynı dosya tekrar seçilebilsin
  });
  ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, (e) => {
    e.preventDefault();
    zone.classList.add("dropzone--active");
  }));
  ["dragleave", "dragend"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("dropzone--active")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dropzone--active");
    const file = e.dataTransfer && e.dataTransfer.files[0];
    if (file) classify(file);
  });
  // Sayfanın başka yerine bırakılan dosya tarayıcıda açılmasın
  ["dragover", "drop"].forEach((type) => window.addEventListener(type, (e) => e.preventDefault()));
})();
