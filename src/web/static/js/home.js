/* Ana sayfa: teknik çizimden gerçek sahneye geçen, kaydırmayla ilerleyen 3B Boeing 787 hikâyesi.

   Açılış (zamana bağlı, kaydırınca hızlanır):
     1. Kâğıt üzerinde uçak burundan kuyruğa kendini çizer (kalem ucu parlar).
     2. Parlak bir silme çizgisi sağdan sola geçer: çizginin sağında çizgiler ve düz dolgu yerini
        ışıklandırılmış gerçek gövdeye, kâğıt da gerçek gökyüzüne bırakır (ikisi aynı çizgiyle).
   Ardından kaydırma: dönerken ölçü çizgileri → kokpit camından "Canlı harita" → yatışta yolcu
   penceresinden "Gecikme tahmini" → tepeden bakışta tanıma çerçevesinden "Uçak tanıma".

   Çizgiler önceden hesaplanır (tools/build_edges.mjs). Gerçek gövde, dolgu ve çizgiler aynı
   geometriyi ve aynı ekran uzayı silme çizgisini (uWipe) paylaşır. */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { createSky, SUN_BASE } from "./sky.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";

const STATIC = document.querySelector('link[href*="/static/css/home.css"]').href.replace(/css\/home\.css.*$/, "");
const $ = (id) => document.getElementById(id);
const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const reduced = () => motionQuery.matches;
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const smooth = (t) => t * t * (3 - 2 * t);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);   // silme hareketi
const range = (p, a, b) => clamp01((p - a) / (b - a));
const lerp = (a, b, t) => a + (b - a) * t;
const bump = (p, a, b, c, d) => range(p, a, b) * (1 - range(p, c, d));   // a→b yüksel, c→d in

const INK = new THREE.Color("#ECE9E4"), ACCENT = new THREE.Color("#E8743B"), FILL = new THREE.Color("#1E252E");

// Açılış zamanlaması (sn)
const T_DRAW = 2.4, T_HOLD = 0.45, T_DEVELOP = 2.3;

// ------------------------------------------------------------------ silme çizgisi (ekran uzayı)
// uWipe: çizim tamponu pikselinde dikey çizgi. Sağı gerçek gövde, solu teknik çizim; arka plandaki
// kâğıt da aynı çizgiyle silindiği için uçak ve arka plan piksel piksel eşzamanlı dönüşür.
const SWEEP = { uWipe: { value: 1e6 }, uGlowW: { value: 3 } };
const SWEEP_DECL = "uniform float uWipe; uniform float uGlowW;";
const SWEEP_POS = "float sweepD = gl_FragCoord.x - uWipe;";

/** Standart malzemeye silme ekler: keep="real" çizginin sağını, keep="art" solunu çizer. */
function patchSweep(material, keep) {
  material.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, SWEEP);
    const test = keep === "real" ? "if (sweepD < 0.0) discard;" : "if (sweepD >= 0.0) discard;";
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `#include <common>\n${SWEEP_DECL}`)
      .replace("void main() {", `void main() {\n${SWEEP_POS}\n${test}`);
    if (keep === "real") {
      // Silme çizgisinin hemen arkasında kısa, sıcak bir parıltı
      sh.fragmentShader = sh.fragmentShader.replace("#include <opaque_fragment>",
        "#include <opaque_fragment>\ngl_FragColor.rgb += vec3(1.0, .74, .38) * (1.0 - smoothstep(0.0, uGlowW * 8.0, sweepD)) * 1.3;");
    }
  };
  material.customProgramCacheKey = () => `sweep-${keep}`;
}

/** Çizgi gölgelendiricisi: kendini çizme (uReveal), tarama ile kaybolma, odak bölgesini vurgulama. */
function lineMaterial({ hidden }) {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: !hidden,
    uniforms: {
      ...SWEEP,
      uReveal: { value: 0 }, uInk: { value: INK }, uAccent: { value: ACCENT },
      uFocus: { value: new THREE.Vector3() }, uFocusR: { value: 1 }, uFocusAmt: { value: 0 },
      uAlpha: { value: hidden ? 0.08 : 0.85 },
    },
    vertexShader: `attribute float aProg; varying float vProg; varying vec3 vPos;
      void main(){ vProg = aProg; vPos = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform float uReveal, uFocusR, uFocusAmt, uAlpha; uniform vec3 uInk, uAccent, uFocus;
      varying float vProg; varying vec3 vPos; ${SWEEP_DECL}
      void main(){
        if (vProg > uReveal) discard;
        ${SWEEP_POS}
        if (sweepD >= 0.0) discard;                                                // gerçek gövdeye dönen kısım
        float head = 1.0 - smoothstep(0.0, 0.05, uReveal - vProg);                 // kalem ucu
        float front = 1.0 - smoothstep(0.0, uGlowW * 10.0, -sweepD);               // silme çizgisinin önü
        float f = (1.0 - smoothstep(uFocusR * 0.55, uFocusR, distance(vPos, uFocus))) * uFocusAmt;
        vec3 c = mix(mix(uInk, uAccent, f), vec3(1.0), head);
        c = mix(c, vec3(1.0, .86, .6), front);
        float a = clamp(uAlpha + head + front + f * 0.25, 0.0, 1.0);
        gl_FragColor = vec4(c, a);
      }`,
  });
}

// ------------------------------------------------------------------ model
async function loadEdges(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Kenar dosyası: HTTP ${resp.status}`);
  let buf = await resp.arrayBuffer();
  const head = new Uint8Array(buf, 0, 2);
  if (head[0] === 0x1f && head[1] === 0x8b) {
    buf = await new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
  }
  const dv = new DataView(buf);
  if (String.fromCharCode(...new Uint8Array(buf, 0, 4)) !== "EDG1") throw new Error("Kenar dosyası biçimi tanınmadı.");
  const segs = dv.getUint32(4, true);
  const min = [0, 1, 2].map((i) => dv.getFloat32(8 + i * 4, true));
  const sc = [0, 1, 2].map((i) => dv.getFloat32(20 + i * 4, true));
  const q = new Uint16Array(buf, 32, segs * 6);
  const pos = new Float32Array(segs * 6);
  for (let i = 0; i < pos.length; i++) { const a = i % 3; pos[i] = min[a] + q[i] * sc[a]; }
  return pos;
}

async function loadPlane() {
  const meta = await (await fetch(`${STATIC}models/b787.json`, { cache: "no-cache" })).json();
  const loader = new GLTFLoader();
  const draco = new DRACOLoader();
  draco.setDecoderPath("https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/libs/draco/gltf/");
  loader.setDRACOLoader(draco);
  const [gltf, edgePos] = await Promise.all([
    loader.loadAsync(`${STATIC}models/${meta.file}`), loadEdges(`${STATIC}models/${meta.edges}`)]);
  const real = gltf.scene;

  // Gerçek gövde: ada göre malzeme ayarı, kuyruk boyası, tarama
  const tuned = new Set();
  const paint = new THREE.MeshStandardMaterial({ color: meta.paint.color, roughness: meta.paint.roughness,
                                                 metalness: meta.paint.metalness, name: "Paint" });
  const meshes = [];
  real.traverse((o) => {
    if (!o.isMesh) return;
    meshes.push(o);
    o.frustumCulled = false;
    o.receiveShadow = true;
    const owner = o.parent && /^Object_/.test(o.name) ? o.parent.name : o.name;
    if (meta.paint.nodes.includes(owner) && o.material.name === "Body") o.material = paint;
    const m = o.material;
    if (tuned.has(m)) return;
    tuned.add(m);
    const t = (meta.materials || {})[m.name];
    if (t) {
      if (t.color) m.color.set(t.color);
      if (t.roughness != null) m.roughness = t.roughness;
      if (t.metalness != null) m.metalness = t.metalness;
      if (t.opaque) { m.transparent = false; m.opacity = 1; m.depthWrite = true; }
    }
    m.envMapIntensity = meta.envIntensity ?? 1;
    patchSweep(m, "real");
  });

  // Teknik çizim dolgusu: aynı geometri, düz renk; arkadaki çizgileri örter
  const fillMat = new THREE.MeshBasicMaterial({
    color: FILL, transparent: true, opacity: 0, depthWrite: true,
    polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2 });
  patchSweep(fillMat, "art");
  const fill = real.clone(true);
  fill.traverse((o) => { if (o.isMesh) { o.material = fillMat; o.castShadow = o.receiveShadow = false; } });

  // Çizgiler: çizim sırası burundan (+z, glTF uzayı) kuyruğa, biraz rastgelelikle
  let zmin = Infinity, zmax = -Infinity;
  for (let i = 2; i < edgePos.length; i += 3) { zmin = Math.min(zmin, edgePos[i]); zmax = Math.max(zmax, edgePos[i]); }
  const prog = new Float32Array(edgePos.length / 3);
  for (let v = 0; v < prog.length; v += 2) {
    const z = (edgePos[v * 3 + 2] + edgePos[v * 3 + 5]) / 2;
    const r = Math.abs(Math.sin(v * 12.9898) * 43758.5453) % 1;
    prog[v] = prog[v + 1] = ((zmax - z) / (zmax - zmin)) * 0.88 + r * 0.1;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(edgePos, 3));
  geo.setAttribute("aProg", new THREE.BufferAttribute(prog, 1));
  const lines = new THREE.LineSegments(geo, lineMaterial({ hidden: false }));
  const ghost = new THREE.LineSegments(geo, lineMaterial({ hidden: true }));
  lines.frustumCulled = ghost.frustumCulled = false;
  lines.renderOrder = 2; ghost.renderOrder = 3;

  // Hepsi glTF sahne uzayında; birlikte normalleştirilir (burun +x, merkez orijinde, metre)
  const art = new THREE.Group();
  art.add(real, fill, lines, ghost);
  art.rotation.set(...meta.rotation.map((d) => (d * Math.PI) / 180));
  const object = new THREE.Group();
  object.add(art);
  object.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(real);
  const s = (meta.length || 62.8) / box.getSize(new THREE.Vector3()).x;
  art.scale.multiplyScalar(s);
  object.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(real);
  art.position.sub(box.getCenter(new THREE.Vector3()));
  art.updateMatrix();
  object.updateMatrixWorld(true);

  // Ölçü noktaları: burun, kuyruk, kanat uçları (nesne uzayında)
  const p = new THREE.Vector3();
  const ext = { nose: null, tail: null, wl: null, wr: null };
  for (let i = 0; i < edgePos.length; i += 3) {
    p.set(edgePos[i], edgePos[i + 1], edgePos[i + 2]).applyMatrix4(art.matrix);
    if (!ext.nose || p.x > ext.nose.x) ext.nose = p.clone();
    if (!ext.tail || p.x < ext.tail.x) ext.tail = p.clone();
    if (!ext.wl || p.z < ext.wl.z) ext.wl = p.clone();
    if (!ext.wr || p.z > ext.wr.z) ext.wr = p.clone();
  }
  const v = (a) => new THREE.Vector3(...a);
  const inv = art.matrix.clone().invert();
  return {
    object, ext, meshes, fill, fillMat, lineMats: [lines.material, ghost.material], artParts: [fill, lines, ghost],
    anchors: { cockpit: v(meta.anchors.cockpit), window: v(meta.anchors.window), windowNormal: v(meta.anchors.windowNormal).normalize() },
    toArt: (vec) => vec.clone().applyMatrix4(inv), artScale: s, credit: meta.credit || "",
  };
}

// ------------------------------------------------------------------ kamera anahtar kareleri
/* cam/tgt: attach=0 iken dünya, 1 iken uçağa bağlı (yerel) koordinat. shift: uçağı ekranda yatay
   kaydırır (genişlik oranı, + sağa). yaw/pitch/roll: uçağın duruşu (rad). */
function makeKeys(a) {
  const C = a.cockpit, Wn = a.windowNormal, W = a.window;
  const arr = (vec) => vec.toArray();
  const winCam = W.clone().addScaledVector(Wn, 58).add(new THREE.Vector3(24, 12, 0));
  const keys = [
    { p: 0.00, cam: [70, 13, 92], tgt: [0, 1, 0], attach: 0, fov: 30, shift: 0.2, yaw: -0.42, pitch: 0.03, roll: -0.05 },
    { p: 0.09, cam: [68, 12, 90], tgt: [0, 1, 0], attach: 0, fov: 30, shift: 0.2, yaw: -0.32, pitch: 0.03, roll: -0.03 },
    { p: 0.25, cam: [66, 14, 92], tgt: [0, 1, 0], attach: 0, fov: 30, shift: 0.12, yaw: -0.32 + Math.PI * 2, pitch: 0.02, roll: 0 },
    { p: 0.33, cam: arr(C.clone().add(new THREE.Vector3(27, 5, -31))), tgt: arr(C.clone().add(new THREE.Vector3(-15, -1.5, 0))),
      attach: 1, fov: 32, shift: -0.22, yaw: -0.32, pitch: 0.02, roll: 0 },
    { p: 0.46, cam: arr(C.clone().add(new THREE.Vector3(24, 4.5, -28))), tgt: arr(C.clone().add(new THREE.Vector3(-15, -1.5, 0))),
      attach: 1, fov: 32, shift: -0.22, yaw: -0.32, pitch: 0.02, roll: 0 },
    { p: 0.57, cam: arr(winCam), tgt: arr(W.clone().add(new THREE.Vector3(-7, -3, 0))),
      attach: 1, fov: 34, shift: -0.2, yaw: -0.32, pitch: 0.02, roll: 0.3 },
    { p: 0.70, cam: arr(winCam.clone().add(new THREE.Vector3(-3, 1, 2))), tgt: arr(W.clone().add(new THREE.Vector3(-7, -3, 0))),
      attach: 1, fov: 34, shift: -0.2, yaw: -0.32, pitch: 0.02, roll: 0.34 },
    { p: 0.81, cam: [-4, 165, 3], tgt: [0, 0, 0], attach: 0, fov: 32, shift: -0.15, up: [1, 0, 0.1], yaw: 0.06, pitch: 0, roll: 0 },
    { p: 0.96, cam: [-4, 170, 3], tgt: [0, 0, 0], attach: 0, fov: 32, shift: -0.15, up: [1, 0, 0.1], yaw: 0.06, pitch: 0, roll: 0 },
    { p: 1.00, cam: [-4, 230, 3], tgt: [0, 0, 0], attach: 0, fov: 32, shift: 0, up: [1, 0, 0.1], yaw: 0.06, pitch: 0, roll: 0 },
  ];
  for (const k of keys) if (k.p > 0.25) k.yaw += Math.PI * 2;   // tam turdan sonra geri sarma yok
  return keys;
}

/* Anahtar kareler arasında monoton kübik (Fritsch–Carlson) eğri: hız her anahtar karede sıfıra
   inmez, bekleme kareleri ise aşım yapmadan yumuşakça durur. */
const CHANNELS = [["cam", 3], ["tgt", 3], ["up", 3], ["attach", 1], ["fov", 1], ["shift", 1],
                  ["yaw", 1], ["pitch", 1], ["roll", 1]];
function monotoneTangents(x, y) {
  const n = x.length, d = [], m = new Array(n);
  for (let i = 0; i < n - 1; i++) d.push((y[i + 1] - y[i]) / (x[i + 1] - x[i]));
  m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const al = m[i] / d[i], be = m[i + 1] / d[i], ss = al * al + be * be;
    if (ss > 9) { const t = 3 / Math.sqrt(ss); m[i] = t * al * d[i]; m[i + 1] = t * be * d[i]; }
  }
  return m;
}
function buildCurves(keys) {
  const P = keys.map((k) => k.p);
  const curves = [];
  for (const [name, n] of CHANNELS) {
    for (let c = 0; c < n; c++) {
      const Y = keys.map((k) => {
        const val = k[name] ?? (name === "up" ? [0, 1, 0] : 0);
        return n === 1 ? val : val[c];
      });
      curves.push({ name, c, n, Y, M: monotoneTangents(P, Y) });
    }
  }
  return { P, curves };
}
function frameAt({ P, curves }, p) {
  let i = 0;
  while (i < P.length - 2 && p > P[i + 1]) i++;
  const h = P[i + 1] - P[i], t = clamp01((p - P[i]) / h), t2 = t * t, t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
  const out = { cam: [0, 0, 0], tgt: [0, 0, 0], up: [0, 0, 0] };
  for (const cv of curves) {
    const val = h00 * cv.Y[i] + h10 * h * cv.M[i] + h01 * cv.Y[i + 1] + h11 * h * cv.M[i + 1];
    if (cv.n === 1) out[cv.name] = val; else out[cv.name][cv.c] = val;
  }
  out.attach = clamp01(out.attach);
  return out;
}

// Ölçü etiketleri: modelin ölçülen boyu değil, metindeki resmi değerler (787-8)
const DIM_LABELS = [0, 1].map((i) => {
  const dd = document.querySelector(`[data-dim="${i}"]`);
  if (!dd) return "";
  const dec = +dd.dataset.dec || 0;
  return new Intl.NumberFormat("tr-TR", { minimumFractionDigits: dec, maximumFractionDigits: dec })
    .format(+dd.dataset.count) + (dd.dataset.suffix || "");
});
const STOPS = { kalkis: 0.0, kokpit: 0.4, kabin: 0.645, irtifa: 0.89 };
// Kılavuz çizgi zamanlaması: [çizgi başlar, kart görünür, kart kaybolur]
const CALLOUTS = [[0.325, 0.345, 0.455], [0.575, 0.595, 0.70], [0.815, 0.835, 0.955]];

// ------------------------------------------------------------------ DOM katmanları
const els = {
  bp: $("bp"), wipe: $("wipe"), tb: document.querySelector(".titleblock"), tbState: $("tb-state"),
  sunset: $("sky-sunset"), above: $("sky-above"), shade: $("sky-shade"),
  frame: $("idframe"), guess: $("guess-bar"),
  leaders: [0, 1, 2].map((i) => ({ path: $(`leader-${i}`), dot: $(`dot-${i}`), card: $(`callout-${i}`), len: 0 })),
  dims: [0, 1].map((i) => ({ g: $(`dim-${i}`), line: $(`dim-${i}-line`), ext: $(`dim-${i}-ext`),
                              label: $(`dim-${i}-label`), text: $(`dim-${i}-text`) })),
  chapters: [...document.querySelectorAll(".chapter[data-in]")].map((el) => ({ el, a: +el.dataset.in, b: +el.dataset.out })),
  rail: [...document.querySelectorAll(".rail__stop")],
};

function setVisible(el, o, y) {
  el.style.setProperty("--o", o.toFixed(3));
  el.style.setProperty("--y", `${y.toFixed(1)}px`);
  el.classList.toggle("is-visible", o > 0.05);
  el.inert = o < 0.5;
  if (o > 0.55 && !el.classList.contains("is-in")) { el.classList.add("is-in"); onEnter(el); }
  else if (o < 0.02 && el.classList.contains("is-in")) el.classList.remove("is-in");
}

/** Başlıkları kelimelere böler: her kelime maskeli bir kutudan sırayla yukarı kayarak belirir. */
function splitWords(root) {
  let i = 0;
  const walk = (node) => {
    for (const child of [...node.childNodes]) {
      if (child.nodeType === Node.TEXT_NODE) {
        const frag = document.createDocumentFragment();
        for (const part of child.textContent.split(/(\s+)/)) {
          if (!part) continue;
          if (/^\s+$/.test(part)) { frag.append(part); continue; }
          const w = document.createElement("span"), inner = document.createElement("span");
          w.className = "w";
          inner.textContent = part;
          inner.style.setProperty("--i", i++);
          w.append(inner);
          frag.append(w);
        }
        child.replaceWith(frag);
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        walk(child);
      }
    }
  };
  walk(root);
}
document.querySelectorAll("[data-split]").forEach(splitWords);
document.querySelectorAll(".callout").forEach((c) => [...c.children].forEach((ch, i) => ch.style.setProperty("--ci", i)));

const numberFmt = (dec) => new Intl.NumberFormat("tr-TR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
function countUp(el) {
  const to = parseFloat(el.dataset.count), dec = +el.dataset.dec || 0, f = numberFmt(dec);
  const pre = el.dataset.prefix || "", suf = el.dataset.suffix || "";
  if (reduced()) { el.textContent = pre + f.format(to) + suf; return; }
  const t0 = performance.now(), dur = 1300;
  const tick = (now) => {
    const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(2, -10 * t);
    el.textContent = pre + f.format(to * (t >= 1 ? 1 : e)) + suf;
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
function onEnter(el) { el.querySelectorAll("[data-count]").forEach(countUp); }

function renderChapters(p) {
  for (const c of els.chapters) {
    const f = 0.02;
    const o = Math.min(range(p, c.a - f, c.a), 1 - range(p, c.b, c.b + f));
    setVisible(c.el, o, (1 - range(p, c.a - f, c.a)) * 24 - range(p, c.b, c.b + f) * 24);
  }
}

const tmp = new THREE.Vector3();
function toScreen(vec, camera, w, h) {
  tmp.copy(vec).project(camera);
  return { x: (tmp.x * 0.5 + 0.5) * w, y: (-tmp.y * 0.5 + 0.5) * h };
}

/** Uçaktaki noktadan kartın kenarına kırık çizgi; çizgi kaydırmayla uzar, kart ucunda belirir. */
function renderLeader(i, p, A, w) {
  const L = els.leaders[i];
  const [start, show, end] = CALLOUTS[i];
  const grow = smooth(range(p, start, show));
  const out = range(p, end, end + 0.015);
  const cardO = range(p, show - 0.006, show + 0.012) * (1 - out);
  setVisible(L.card, cardO, (1 - cardO) * 14);
  const lineO = grow > 0 && out < 1 ? 1 - out : 0;
  L.path.style.opacity = lineO;
  L.dot.style.opacity = lineO;
  if (!lineO || !A) return;
  const r = L.card.getBoundingClientRect();
  let d;
  if (w < 760) {
    d = `M${A.x.toFixed(1)},${A.y.toFixed(1)} L${A.x.toFixed(1)},${r.top.toFixed(1)}`;
  } else {
    const leftSide = A.x < r.left;
    const cx = leftSide ? r.left : r.right, cy = r.top + 28;
    const ex = cx + (leftSide ? -56 : 56);
    d = `M${A.x.toFixed(1)},${A.y.toFixed(1)} L${ex.toFixed(1)},${cy.toFixed(1)} L${cx.toFixed(1)},${cy.toFixed(1)}`;
  }
  if (L.path.getAttribute("d") !== d) {
    L.path.setAttribute("d", d);
    L.len = L.path.getTotalLength();
  }
  L.path.style.strokeDasharray = `${L.len} ${L.len}`;
  L.path.style.strokeDashoffset = `${(L.len * (1 - grow)).toFixed(1)}`;
  L.dot.setAttribute("cx", A.x.toFixed(1));
  L.dot.setAttribute("cy", A.y.toFixed(1));
}

/** Teknik çizim ölçü çizgisi: iki uç nokta, kesikli uzatma çizgileri, oklar ve ortada değer. */
function renderDim(i, A, B, center, amount, label) {
  const D = els.dims[i];
  D.g.style.opacity = amount > 0.001 ? 1 : 0;
  if (amount <= 0.001) return;
  let dx = B.x - A.x, dy = B.y - A.y;
  const len = Math.hypot(dx, dy) || 1;
  dx /= len; dy /= len;
  let nx = -dy, ny = dx;
  const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
  if ((center.x - mx) * nx + (center.y - my) * ny > 0) { nx = -nx; ny = -ny; }   // uçaktan dışarı
  const off = 34, a1 = { x: A.x + nx * off, y: A.y + ny * off }, b1 = { x: B.x + nx * off, y: B.y + ny * off };
  const e = smooth(amount);
  const hl = len / 2 * e;                             // çizgi ortadan iki yana açılır
  const p1 = { x: (a1.x + b1.x) / 2 - dx * hl, y: (a1.y + b1.y) / 2 - dy * hl };
  const p2 = { x: (a1.x + b1.x) / 2 + dx * hl, y: (a1.y + b1.y) / 2 + dy * hl };
  const ah = 9, aw = 3.5;
  const arrow = (P, s) => `M${P.x},${P.y} L${P.x + s * dx * ah + nx * aw},${P.y + s * dy * ah + ny * aw} M${P.x},${P.y} L${P.x + s * dx * ah - nx * aw},${P.y + s * dy * ah - ny * aw}`;
  D.line.setAttribute("d", `M${p1.x},${p1.y} L${p2.x},${p2.y} ${e > 0.98 ? arrow(p1, 1) + " " + arrow(p2, -1) : ""}`);
  const ext = (P) => `M${P.x + nx * 6},${P.y + ny * 6} L${P.x + nx * (off + 8)},${P.y + ny * (off + 8)}`;
  D.ext.setAttribute("d", `${ext(A)} ${ext(B)}`);
  D.ext.style.opacity = e;
  let ang = Math.atan2(dy, dx) * 180 / Math.PI;
  if (ang > 90) ang -= 180; else if (ang < -90) ang += 180;
  const lx = (a1.x + b1.x) / 2 + nx * 16, ly = (a1.y + b1.y) / 2 + ny * 16;
  D.label.setAttribute("transform", `translate(${lx.toFixed(1)} ${ly.toFixed(1)}) rotate(${ang.toFixed(1)})`);
  D.label.style.opacity = range(amount, 0.6, 1);
  D.text.textContent = label;
}

// ------------------------------------------------------------------ mini radar (canlı veri)
const radar = { blips: [], loaded: false, angle: 0 };
async function loadRadar() {
  if (radar.loaded) return;
  radar.loaded = true;
  try {
    const body = await GR.api("/api/flights/world", { timeoutMs: 40000 });
    const F = body.fields.reduce((o, f, i) => ((o[f] = i), o), {});
    const lat0 = 41.0, lon0 = 28.9, kx = 111.32 * Math.cos(lat0 * Math.PI / 180), R = 900;
    let near = 0;
    for (const r of body.ac) {
      const dx = (r[F.lon] - lon0) * kx, dy = (r[F.lat] - lat0) * 110.57, dist = Math.hypot(dx, dy);
      if (dist > R || r[F.kind] === 5) continue;
      near++;
      radar.blips.push([Math.atan2(dx, dy), dist / R]);
    }
    $("radar-count").textContent = GR.fmtNum(body.count);
    $("radar-near").textContent = `${GR.fmtNum(near)} tanesi İstanbul'a 900 km'den yakın`;
  } catch (_) {
    $("radar-count").textContent = "?";
  }
}
function drawRadar(dt) {
  const c = $("radar-canvas"), g = c.getContext("2d"), s = c.width / 2, R = s - 2;
  if (!reduced()) radar.angle = (radar.angle + dt * 1.3) % (Math.PI * 2);
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, c.width, c.height);
  g.translate(s, s);
  g.strokeStyle = "rgba(236, 232, 224, .28)";
  g.lineWidth = 1;
  for (const f of [0.5, 1]) { g.beginPath(); g.arc(0, 0, R * f, 0, Math.PI * 2); g.stroke(); }
  if (g.createConicGradient) {
    const sw = g.createConicGradient(radar.angle - Math.PI / 2 - 0.9, 0, 0);
    sw.addColorStop(0, "rgba(236, 232, 224, 0)"); sw.addColorStop(0.143, "rgba(236, 232, 224, .32)"); sw.addColorStop(0.144, "rgba(236, 232, 224, 0)");
    g.fillStyle = sw; g.beginPath(); g.arc(0, 0, R, 0, Math.PI * 2); g.fill();
  }
  g.fillStyle = "#E8743B";
  for (const [a, d] of radar.blips) {
    const since = (radar.angle - a + Math.PI * 4) % (Math.PI * 2);
    g.globalAlpha = reduced() ? 0.9 : Math.max(0.15, 1 - since / 5);
    g.fillRect(Math.sin(a) * d * R - 1.5, -Math.cos(a) * d * R - 1.5, 3, 3);
  }
  g.globalAlpha = 1;
}

// Kalkış tabelası: harf harf dönen satırlar (bir kez)
const FLAP = " ABCDEFGHIJKLMNOPQRSTUVWXYZÇĞİÖŞÜ0123456789/";
let boardDone = false;
function flipBoard() {
  if (boardDone) return;
  boardDone = true;
  if (reduced()) return;
  document.querySelectorAll("[data-flap]").forEach((el, n) => {
    const text = [...el.dataset.flap];
    let frame = 0;
    const tick = () => {
      frame++;
      el.textContent = text.map((ch, i) => (frame > i * 2 + 4 + n || ch === " " ? ch : FLAP[(Math.random() * FLAP.length) | 0])).join("");
      if (frame <= text.length * 2 + 6 + n) setTimeout(tick, 34);
    };
    tick();
  });
}

// ------------------------------------------------------------------ rota çubuğu
function renderRail(p) {
  $("rail-fill").style.transform = `scaleY(${p.toFixed(4)})`;
  let cur = els.rail[0];
  for (const r of els.rail) if (p + 0.03 >= STOPS[r.dataset.stop]) cur = r;
  for (const r of els.rail) r.toggleAttribute("aria-current", r === cur);
}
els.rail.forEach((r) => r.addEventListener("click", () => {
  const max = document.documentElement.scrollHeight - innerHeight;
  const top = STOPS[r.dataset.stop] * max;
  if (lenis) lenis.scrollTo(top, { duration: 1.8, easing: (t) => 1 - Math.pow(1 - t, 4) });
  else window.scrollTo({ top, behavior: reduced() ? "auto" : "smooth" });
}));

// Yumuşak kaydırma (Lenis): tekerlek adımları akıcı bir harekete dönüşür
const lenis = window.Lenis && !reduced()
  ? new window.Lenis({ lerp: 0.075, wheelMultiplier: 0.9, smoothWheel: true, syncTouch: false })
  : null;
const scrollP = () => {
  const max = document.documentElement.scrollHeight - innerHeight;
  return max > 0 ? clamp01(scrollY / max) : 0;
};

// ------------------------------------------------------------------ 3B sahne
async function start() {
  const canvas = $("stage");
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  } catch (err) {
    document.body.classList.add("no-3d");
    return;
  }
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.5, 2000);
  // Sıcak anahtar ışık (güneş, uçağın arkasında alçakta) + soğuk dolgu + alttan bulut yansıması:
  // gün batımının turuncu/lacivert zıtlığı. Gökyüzü ve uçak aynı güneş yönünü kullanır.
  const hemi = new THREE.HemisphereLight(0x8fa6d8, 0xc9703c, 0.30);
  const sun = new THREE.DirectionalLight(0xffb56e, 3.6);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -40, right: 40, top: 40, bottom: -40, near: 1, far: 300 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.04;
  const fill = new THREE.DirectionalLight(0xffc4a0, 0.75);   // sıcak dolgu: gölge tarafı düz gri kalmasın
  fill.position.set(60, 50, 34);
  const cool = new THREE.DirectionalLight(0x8fb0f0, 0.45);   // gökyüzünden gelen soğuk ışık
  cool.position.set(-30, 80, 60);
  scene.add(hemi, sun, fill, cool);

  let dirty = true;
  const sky = createSky(renderer, scene, SWEEP);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = sky.buildEnvironment(pmrem);
  const sunDir = new THREE.Vector3().copy(SUN_BASE);
  const sunH = new THREE.Vector2(SUN_BASE.x, SUN_BASE.z).normalize();
  const glowEl = $("sunglow"), glowPos = new THREE.Vector3(), camFwd = new THREE.Vector3();

  let model;
  try {
    model = await loadPlane();
  } catch (err) {
    console.warn("Uçak modeli yüklenemedi:", err);
    document.body.classList.add("no-3d");
    return;
  }
  if (model.credit) $("model-credit").textContent = `. 3B model: ${model.credit}`;
  const plane = model.object;
  scene.add(plane);
  sun.target = plane;
  const KEYS = buildCurves(makeKeys(model.anchors));
  const focusCockpit = model.toArt(model.anchors.cockpit), focusWindow = model.toArt(model.anchors.window);
  // Gölgeler yalnızca dönüşüm bitince: taranmamış kısmın gölgesi gerçek gövdeye düşmesin
  renderer.compile(scene, camera);
  document.body.classList.add("has-3d");

  let w = 0, h = 0;
  const resize = () => {
    w = innerWidth; h = innerHeight;
    const dpr = window.devicePixelRatio || 1;
    renderer.setPixelRatio(innerWidth >= 760 ? Math.min(2, Math.max(1.5, dpr)) : Math.min(2, dpr));
    renderer.setSize(w, h, false);
    sky.resize(w < 760 ? 0.38 : 0.5);
    camera.aspect = w / h;
    dirty = true;
  };
  addEventListener("resize", resize);
  resize();

  let shown = scrollP(), last = performance.now(), lastRender = 0, lastSky = 0;
  let intro = reduced() ? Infinity : 0;          // açılış saati (kaydırınca hızlanır)
  let developed = reduced();
  const t0 = last;
  const box = new THREE.Box3(), corner = new THREE.Vector3(), local = new THREE.Vector3();
  const camW = new THREE.Vector3(), tgtW = new THREE.Vector3(), camFrame = new THREE.Matrix4();

  function finishDevelop() {
    developed = true;
    for (const m of model.meshes) m.castShadow = true;
    for (const part of model.artParts) part.visible = false;    // çizim katmanları artık çizilmez
    document.body.classList.remove("is-blueprint");
    document.body.classList.add("is-real");
    els.bp.style.display = "none";
    els.wipe.style.display = "none";
    els.tb.style.display = "none";
  }
  if (developed) finishDevelop();

  function frame(now) {
    requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (lenis) lenis.raf(now);
    const target = scrollP();
    const moving = Math.abs(target - shown) > 1e-5;
    shown = reduced() || !moving ? target : shown + (target - shown) * (1 - Math.exp(-dt * (lenis ? 14 : 8)));
    const p = shown;
    const t = (now - t0) / 1000;
    const introOn = !developed;
    if (introOn) intro += dt * (target > 0.004 ? 3.2 : 1);       // kullanıcı kaydırırsa açılış hızlanır
    if (!moving && !dirty && !introOn && now - lastRender < 33) return;   // durgunken ~30 fps
    lastRender = now;
    dirty = false;
    const k = frameAt(KEYS, p);
    const narrow = w < 760;

    // Kamera uçağın yönüne (yaw/pitch) bağlanır, yatışına (roll) bağlanmaz: uçak ekranda gerçekten yatar
    plane.rotation.order = "YXZ";
    plane.rotation.set(0, k.yaw, k.pitch);
    plane.position.y = reduced() ? 0 : Math.sin(t * 0.5) * 0.35 * (1 - k.attach);
    plane.updateMatrixWorld(true);
    camFrame.copy(plane.matrixWorld);
    plane.rotation.x = k.roll;
    plane.updateMatrixWorld(true);

    local.fromArray(k.cam);
    camW.copy(local).lerp(local.clone().applyMatrix4(camFrame), k.attach);
    local.fromArray(k.tgt);
    tgtW.copy(local).lerp(local.clone().applyMatrix4(camFrame), k.attach);
    if (narrow && k.attach > 0) {
      const anchor = (p < 0.52 ? model.anchors.cockpit : model.anchors.window).clone().applyMatrix4(plane.matrixWorld);
      tgtW.lerp(anchor, 0.75 * k.attach);
    }
    if (narrow) {
      const far = lerp(Math.min(2.0, Math.max(1, 1.1 / camera.aspect)), 1.15, k.attach);
      camW.sub(tgtW).multiplyScalar(far).add(tgtW);
    }
    camera.position.copy(camW);
    camera.up.fromArray(k.up).normalize();
    camera.lookAt(tgtW);
    camera.fov = k.fov;
    camera.setViewOffset(w, h, narrow ? 0 : -k.shift * w, narrow ? (p < 0.3 ? -0.16 : 0.14) * h * (1 - k.attach) : 0, w, h);
    camera.updateProjectionMatrix();

    // Güneş: yolculuğun sonunda yükselir (tepeden bakışta üst yüzeyi aydınlatır); gökyüzü ve ışık birlikte
    const lift = smooth(range(p, 0.70, 0.80)) * 0.5;
    sunDir.set(sunH.x * (1 - lift - 0.075), lift + 0.075, sunH.y * (1 - lift - 0.075)).normalize();
    sky.uniforms.uSun.value.copy(sunDir);
    sun.position.copy(plane.position).addScaledVector(sunDir, 100);
    if (now - lastSky > 110 || moving || introOn || dirty) { sky.render(camera, reduced() ? 0 : t); lastSky = now; }

    if (introOn) {
      // 1) Çizim
      const reveal = 1.2 * (1 - Math.pow(1 - clamp01(intro / T_DRAW), 3));
      for (const m of model.lineMats) m.uniforms.uReveal.value = reveal;
      model.fillMat.opacity = 0.7 * smooth(clamp01((reveal - 0.5) / 0.6));
      // 2) Dönüşüm: silme çizgisi sağ kenardan sola geçer (burun sağda olduğu için önce burun döner)
      const u = easeInOut(clamp01((intro - T_DRAW - T_HOLD) / T_DEVELOP));
      const wx = u <= 0 ? w + 4 : (1 - u) * (w + 8) - 4;
      const pr = renderer.getPixelRatio();
      SWEEP.uWipe.value = wx * pr;
      SWEEP.uGlowW.value = 3 * pr;
      document.body.style.setProperty("--wx", `${wx.toFixed(1)}px`);
      els.wipe.style.setProperty("--wo", (u > 0 && u < 1 ? 1 : 0).toString());
      const tbLeft = w - 308;
      els.tb.style.setProperty("--tbo", clamp01((wx - tbLeft) / 60).toFixed(3));
      if (u > 0.02) els.tbState.textContent = "Dönüştürülüyor";
      if (u >= 1) finishDevelop();
    } else {
      SWEEP.uWipe.value = -1e6;
    }

    // Odak bölgesi (yalnızca çizim katmanı görünürken anlamlı)
    const fCock = bump(p, 0.3, 0.34, 0.45, 0.48), fWin = bump(p, 0.56, 0.59, 0.69, 0.72);
    for (const m of model.lineMats) {
      m.uniforms.uFocusAmt.value = Math.max(fCock, fWin);
      m.uniforms.uFocus.value.copy(fCock >= fWin ? focusCockpit : focusWindow);
      m.uniforms.uFocusR.value = (fCock >= fWin ? 7 : 5) / model.artScale;
    }
    renderer.render(scene, camera);

    // Güneş ışıması: güneş kameranın önündeyse ekran konumuna yumuşak bir parıltı (ekran karışımı)
    camera.getWorldDirection(camFwd);
    const facing = camFwd.dot(sunDir);
    if (developed && facing > 0.2) {
      glowPos.copy(camera.position).addScaledVector(sunDir, 1000);
      const sp = toScreen(glowPos, camera, w, h);
      glowEl.style.transform = `translate3d(${(sp.x - 700).toFixed(1)}px, ${(sp.y - 700).toFixed(1)}px, 0)`;
      glowEl.style.opacity = (smooth(range(facing, 0.2, 0.8)) * (1 - range(p, 0.7, 0.8) * 0.7)).toFixed(3);
    } else {
      glowEl.style.opacity = "0";
    }
    if (developed) els.shade.style.setProperty("--left", (0.55 * (1 - range(p, 0.24, 0.3)) + 0.1).toFixed(3));

    // Ölçü çizgileri: uzunluk ve kanat açıklığı (dönüş sırasında)
    const dimAmt = bump(p, 0.1, 0.16, 0.235, 0.26) * (narrow ? 0 : 1);
    if (dimAmt > 0) {
      const S = (v3) => toScreen(v3.clone().applyMatrix4(plane.matrixWorld), camera, w, h);
      const c = S(new THREE.Vector3());
      const ex = model.ext;
      renderDim(0, S(new THREE.Vector3(ex.nose.x, 0, 0)), S(new THREE.Vector3(ex.tail.x, 0, 0)), c,
                range(dimAmt, 0, 1), DIM_LABELS[0]);
      renderDim(1, S(ex.wl), S(ex.wr), c, range(dimAmt, 0.15, 1), DIM_LABELS[1]);
    } else {
      renderDim(0, null, null, null, 0); renderDim(1, null, null, null, 0);
    }

    // Kılavuz çizgiler: kokpit camı, yolcu penceresi, tanıma çerçevesi
    renderLeader(0, p, toScreen(model.anchors.cockpit.clone().applyMatrix4(plane.matrixWorld), camera, w, h), w);
    renderLeader(1, p, toScreen(model.anchors.window.clone().applyMatrix4(plane.matrixWorld), camera, w, h), w);

    const frameO = bump(p, 0.8, 0.82, 0.955, 0.97);
    let frameAnchor = null;
    if (frameO > 0) {
      box.setFromObject(plane);
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < 4; i++) {
        corner.set(i & 1 ? box.max.x : box.min.x, (box.min.y + box.max.y) / 2, i & 2 ? box.max.z : box.min.z);
        const s = toScreen(corner, camera, w, h);
        x0 = Math.min(x0, s.x); y0 = Math.min(y0, s.y); x1 = Math.max(x1, s.x); y1 = Math.max(y1, s.y);
      }
      const pad = 16;
      x0 = Math.max(8, x0 - pad); y0 = Math.max(80, y0 - pad); x1 = Math.min(w - 8, x1 + pad); y1 = Math.min(h - 8, y1 + pad);
      const f = els.frame;
      f.style.width = `${(x1 - x0).toFixed(1)}px`;
      f.style.height = `${(y1 - y0).toFixed(1)}px`;
      f.style.transform = `translate3d(${x0.toFixed(1)}px, ${y0.toFixed(1)}px, 0)`;
      f.style.setProperty("--fh", `${(y1 - y0 - 4).toFixed(1)}px`);
      frameAnchor = narrow ? { x: (x0 + x1) / 2, y: y1 } : { x: x1, y: y0 + (y1 - y0) * 0.3 };
    }
    els.frame.style.opacity = frameO.toFixed(3);
    renderLeader(2, p, frameAnchor, w);
    els.guess.style.width = `${(smooth(range(p, 0.835, 0.87)) * 97).toFixed(1)}%`;

    if (p > 0.15) loadRadar();
    if (bump(p, 0.33, 0.345, 0.455, 0.47) > 0) drawRadar(dt);
    if (p > 0.59) flipBoard();
    renderChapters(p);
    renderRail(p);
  }
  requestAnimationFrame(frame);
  motionQuery.addEventListener("change", () => { dirty = true; });
}

// ------------------------------------------------------------------ sistem durumu
function systemStatus() {
  const box = $("sys-status"), text = $("sys-status-text");
  const show = (state, msg) => { box.dataset.state = state; text.textContent = msg; };
  GR.api("/api/health", { timeoutMs: 10000 })
    .then((hh) => {
      const parts = [hh.database === "ok" ? "Veritabanı bağlı" : "Veritabanına ulaşılamıyor"];
      const models = [hh.models && hh.models.delay, hh.models && hh.models.vision].filter(Boolean);
      parts.push(`${models.length} model yüklü`);
      show(hh.status === "ok" ? "ok" : "degraded",
           hh.status === "ok" ? `Sistem çalışıyor, ${parts.join(", ").toLowerCase()}` : `Kısmi hizmet: ${parts.join(", ").toLowerCase()}`);
    })
    .catch((err) => show("error", `Sistem durumu alınamadı: ${err.message}`));
}

start();
systemStatus();
