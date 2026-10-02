/* Ortak yardımcılar: API çağrısı, hata mesajları, sayı biçimleri, hareket tercihi. */
window.GR = (() => {
  "use strict";

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const reducedMotion = () => motionQuery.matches;

  class ApiError extends Error {
    constructor(message, status, code, details) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  /** /api/... çağrısı. Hata durumunda kullanıcıya gösterilebilir mesajlı ApiError fırlatır. */
  async function api(path, { method = "GET", json, body, timeoutMs = 20000, signal } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
    if (signal) signal.addEventListener("abort", () => ctrl.abort(signal.reason), { once: true });
    let resp;
    try {
      resp = await fetch(path, {
        method,
        body: json !== undefined ? JSON.stringify(json) : body,
        headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
        signal: ctrl.signal,
      });
    } catch (err) {
      if (signal && signal.aborted) throw err;
      const timedOut = ctrl.signal.reason && ctrl.signal.reason.name === "TimeoutError";
      throw new ApiError(
        timedOut ? "Sunucu zamanında yanıt vermedi. Lütfen tekrar deneyin."
                 : "Sunucuya ulaşılamadı. İnternet bağlantınızı ve sunucunun çalıştığını kontrol edin.",
        0, timedOut ? "timeout" : "network");
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    try { data = await resp.json(); } catch (_) { /* JSON olmayan yanıt */ }
    if (!resp.ok) {
      const e = (data && data.error) || {};
      throw new ApiError(e.message || `Sunucu hatası (HTTP ${resp.status}).`, resp.status,
                         e.code || "http_error", e.details);
    }
    return data;
  }

  const formatters = {};
  const nf = (digits) => (formatters[digits] ||= new Intl.NumberFormat("tr-TR", {
    minimumFractionDigits: digits, maximumFractionDigits: digits }));
  /** 12345.6 → "12.345,6" ; boş değer → "—" */
  const fmtNum = (v, digits = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : nf(digits).format(v);
  /** 0.472 → "%47" (Türkçe yüzde gösterimi) */
  const fmtPct = (v, digits = 0) => (v === null || v === undefined) ? "—" : "%" + nf(digits).format(v * 100);

  /** Durum kutusu: state = "loading" | "error" | "ok" | null (gizle) */
  function setStatus(el, state, message) {
    if (!el) return;
    el.hidden = !state;
    el.className = "status" + (state ? ` status--${state}` : "");
    el.textContent = message || "";
  }

  return { api, ApiError, reducedMotion, motionQuery, fmtNum, fmtPct, setStatus };
})();
