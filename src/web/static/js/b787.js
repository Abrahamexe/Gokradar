/* Boeing 787-9 Dreamliner — koddan üretilen 3B model (Three.js). Ölçüler metre, gerçek uçağa yakın:
   62,8 m boy, 60,1 m kanat açıklığı, 5,8 m gövde çapı.
   Eksenler: +x burun yönü, +y yukarı, +z sağ kanat. Gövde istasyonu s: burun ucunda 0, kuyrukta 62,8.

   787'yi tanıtan ayrıntılar: dört parçalı, kaşsız kokpit camı; büyük yolcu pencereleri; yukarı esneyen
   uzun kanat ve geriye kıvrık (raked) kanat ucu; motor kaportasının tırtıklı (chevron) çıkış kenarı. */
import * as THREE from "three";

export const LENGTH = 62.8;
export const R = 2.9;                       // gövde yarıçapı
export const X = (s) => LENGTH / 2 - s;     // istasyon → yerel x
const TAU = Math.PI * 2;

// ------------------------------------------------------------------ gövde
/** İstasyondaki yarıçap ve merkez yüksekliği: elips burun, sabit kabin, yukarı kıvrılan kuyruk konisi. */
export function section(s) {
  const NOSE = 7.6, TAIL = 44.5;
  if (s < NOSE) {
    const t = 1 - s / NOSE;
    return { r: R * Math.pow(1 - t * t, 0.5) * (1 - 0.04 * t), cy: -0.95 * t * t };
  }
  if (s <= TAIL) return { r: R, cy: 0 };
  const t = (s - TAIL) / (LENGTH - TAIL);
  const r = R * (1 - 0.86 * Math.pow(t, 1.25));
  return { r, cy: (R - r) * 0.9 };
}

/** Gövde yüzeyindeki nokta. θ=0 alt, π/2 sağ yan (+z), π üst, 3π/2 sol yan. */
export function hullPoint(s, theta, out = new THREE.Vector3(), lift = 0) {
  const { r, cy } = section(s);
  const rr = r + lift;
  return out.set(X(s), cy - rr * 1.03 * Math.cos(theta), rr * Math.sin(theta));
}

function stationList() {
  const list = [];
  for (let s = 0; s < 8; s += 0.2) list.push(s);
  for (let s = 8; s < 44; s += 1.5) list.push(s);
  for (let s = 44; s < LENGTH; s += 0.6) list.push(s);
  list.push(LENGTH);
  return list;
}

function fuselageGeometry() {
  const stations = stationList();
  const SEG = 72;
  const pos = [], uv = [], idx = [];
  const v = new THREE.Vector3();
  for (const s of stations) {
    for (let j = 0; j <= SEG; j++) {
      hullPoint(s, (j / SEG) * TAU, v);
      pos.push(v.x, v.y, v.z);
      uv.push(s / LENGTH, j / SEG);
    }
  }
  const row = SEG + 1;
  for (let i = 0; i < stations.length - 1; i++) {
    for (let j = 0; j < SEG; j++) {
      const a = i * row + j, b = a + row;
      idx.push(a, a + 1, b, b, a + 1, b + 1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(idx);
  g.computeVertexNormals();
  // Dikiş (θ=0 ve 2π) normallerini eşitle
  const n = g.attributes.normal;
  for (let i = 0; i < stations.length; i++) {
    const a = i * row, b = a + SEG;
    const nx = n.getX(a) + n.getX(b), ny = n.getY(a) + n.getY(b), nz = n.getZ(a) + n.getZ(b);
    const l = Math.hypot(nx, ny, nz) || 1;
    n.setXYZ(a, nx / l, ny / l, nz / l);
    n.setXYZ(b, nx / l, ny / l, nz / l);
  }
  return g;
}

// Yolcu penceresi ve kapı konumları (her iki yanda). Pencere satırı orta çizginin biraz üstünde.
export const WINDOW_THETA = 0.27;           // yan orta çizgiden yukarı açı (rad)
const DOORS = [8.6, 18.2, 33.6, 50.2];
export const WINDOW_STATIONS = (() => {
  const out = [];
  for (let s = 10.2; s < 49.4; s += 0.96) {
    if (DOORS.some((d) => Math.abs(s - d - 0.55) < 1.25)) continue;
    out.push(Number(s.toFixed(2)));
  }
  return out;
})();

/** Boya (map) ve gece pencere ışıkları (emissiveMap) dokuları. u = s/L, v = θ/2π. */
function liveryTextures() {
  const W = 4096, H = 1024;
  const mk = () => { const c = document.createElement("canvas"); c.width = W; c.height = H; return c; };
  const paint = mk(), glow = mk();
  const g = paint.getContext("2d"), e = glow.getContext("2d");
  const U = (s) => (s / LENGTH) * W, V = (th) => (th / TAU) * H;

  g.fillStyle = "#F3F5F8";
  g.fillRect(0, 0, W, H);
  e.fillStyle = "#000";
  e.fillRect(0, 0, W, H);

  // Alt gövde: koyu lacivert; burunda inceden başlayıp kuyruğa doğru yükselen kavis
  const bellyTheta = (s, extra = 0) => {
    const t = s / LENGTH;
    const rise = Math.pow(Math.max(0, t - 0.3) / 0.7, 1.7);
    const nose = Math.min(1, s / 9);                 // burunda sıfıra iner
    return (0.62 + extra) * nose + 0.85 * rise;
  };
  const belly = (sign) => {
    g.beginPath();
    const base = sign > 0 ? 0 : H;
    g.moveTo(0, base);
    for (let s = 0; s <= LENGTH; s += 0.25) {
      const y = V(bellyTheta(s));
      g.lineTo(U(s), sign > 0 ? y : H - y);
    }
    g.lineTo(W, base);
    g.closePath();
    g.fillStyle = "#1B222B";
    g.fill();
    g.beginPath();
    for (let s = 3; s <= LENGTH - 1; s += 0.25) {
      const y = V(bellyTheta(s, 0.1) + 0.06);
      const yy = sign > 0 ? y : H - y;
      if (s === 3) g.moveTo(U(s), yy); else g.lineTo(U(s), yy);
    }
    g.strokeStyle = "#E8743B";
    g.lineWidth = 7;
    g.stroke();
  };
  belly(1);
  belly(-1);

  // Pencereler
  const winH = V(0.165), winW = U(0.3);
  for (const side of [Math.PI / 2 + WINDOW_THETA, 1.5 * Math.PI - WINDOW_THETA]) {
    for (const s of WINDOW_STATIONS) {
      const x = U(s) - winW / 2, y = V(side) - winH / 2;
      g.fillStyle = "#1D242D";
      roundRect(g, x, y, winW, winH, winW / 2);
      g.fill();
      g.fillStyle = "rgba(255,255,255,.18)";
      roundRect(g, x + 2, y + 2, winW * 0.35, winH * 0.45, 3);
      g.fill();
      e.fillStyle = "#FFB58A";
      roundRect(e, x + 1, y + 1, winW - 2, winH - 2, winW / 2);
      e.fill();
    }
    // Kapılar
    for (const d of DOORS) {
      g.strokeStyle = "rgba(40,55,75,.55)";
      g.lineWidth = 2;
      roundRect(g, U(d), V(side) - V(0.42), U(1.07), V(0.66), 10);
      g.stroke();
    }
  }

  // Kokpit camı: iki ön, iki yan panel (787'de kaş camı yok)
  const top = Math.PI;
  const pane = (s0, s1, a0, a1, a2, a3) => {   // açı: üst merkezden sapma
    for (const sign of [1, -1]) {
      g.beginPath();
      g.moveTo(U(s0), V(top + sign * a0));
      g.lineTo(U(s1), V(top + sign * a1));
      g.lineTo(U(s1), V(top + sign * a2));
      g.lineTo(U(s0), V(top + sign * a3));
      g.closePath();
      g.fillStyle = "#0D1015";
      g.fill();
      g.strokeStyle = "#3A4452";
      g.lineWidth = 3;
      g.stroke();
    }
  };
  pane(1.75, 3.05, 0.035, 0.035, 0.6, 0.66);     // ön camlar (s0: alt kenar, s1: üst kenar)
  pane(3.2, 4.55, 0.7, 0.68, 1.0, 1.1);          // yan camlar

  // Kokpit ışığı (gece)
  e.fillStyle = "#C7CED8";
  e.globalAlpha = 0.35;
  e.fillRect(U(1.75), V(top - 1.1), U(2.8), V(2.2));
  e.globalAlpha = 1;

  const tex = (c) => {
    const t = new THREE.CanvasTexture(c);
    t.flipY = false;
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 8;
    return t;
  };
  return { map: tex(paint), emissiveMap: tex(glow) };
}

function roundRect(g, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

// ------------------------------------------------------------------ taşıyıcı yüzeyler
/** Kanat profili (süperkritik benzeri): [kiriş oranı, üst, alt] — kalınlık oranına göre ölçeklenir. */
const AIRFOIL = (() => {
  const pts = [];
  const N = 16;
  for (let i = 0; i <= N; i++) {
    const x = 0.5 - 0.5 * Math.cos((i / N) * Math.PI);   // uçlarda sık
    const t = 5 * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4);
    pts.push([x, t * 1.0, -t * 0.75]);
  }
  return pts;
})();

/**
 * Kesitleri birleştirerek kanat/kuyruk yüzeyi üretir.
 * stations: [{ span, le, chord, t, off }] — le/chord istasyon (s) cinsinden, off: kalınlık eksenindeki kayma.
 * axis "z": yatay yüzey (açıklık z boyunca, kalınlık y); "y": dikey yüzey (açıklık y, kalınlık z).
 */
function surfaceGeometry(stations, { axis = "z", mirror = false } = {}) {
  const pos = [], idx = [];
  const P = AIRFOIL.length;
  const ring = 2 * P - 2;          // üst (öndən arkaya) + alt (arkadan öne), uçlar paylaşılır
  for (const st of stations) {
    const pts = [];
    for (let i = 0; i < P; i++) pts.push([AIRFOIL[i][0], AIRFOIL[i][1]]);
    for (let i = P - 2; i >= 1; i--) pts.push([AIRFOIL[i][0], AIRFOIL[i][2]]);
    for (const [cx, th] of pts) {
      const x = X(st.le + cx * st.chord);
      const thick = th * st.t * st.chord + st.off;
      if (axis === "z") pos.push(x, thick, mirror ? -st.span : st.span);
      else pos.push(x, st.span, thick);
    }
  }
  for (let k = 0; k < stations.length - 1; k++) {
    for (let i = 0; i < ring; i++) {
      const a = k * ring + i, b = k * ring + ((i + 1) % ring);
      const c = a + ring, d = b + ring;
      if (mirror) idx.push(a, c, b, b, c, d); else idx.push(a, b, c, b, d, c);
    }
  }
  // Uç kapağı
  const last = (stations.length - 1) * ring;
  for (let i = 1; i < ring - 1; i++) {
    if (mirror) idx.push(last, last + i + 1, last + i); else idx.push(last, last + i, last + i + 1);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// Ana kanat: 32° ok açısı, dihedral + uçuşta yukarı esneme, kıvrık uç
const WING_ROOT_LE = 21.2, WING_Y = -1.75;
export function wingY(z) {
  const a = Math.abs(z);
  return WING_Y + a * Math.tan((5 * Math.PI) / 180) + 0.0024 * a * a;
}
function wingStations() {
  const out = [];
  const sweep = Math.tan((32 * Math.PI) / 180);
  const spans = [0, 1.5, 3, 5, 7, 9.5, 12, 15, 18, 21, 24, 26.5, 28, 29.2, 30.05];
  for (const z of spans) {
    let le = WING_ROOT_LE + z * sweep;
    let chord;
    if (z <= 9.5) chord = 13.6 - (13.6 - 7.6) * (z / 9.5);        // kök–kırılma (yehudi)
    else if (z <= 26.5) chord = 7.6 - (7.6 - 2.7) * ((z - 9.5) / 17);
    else {                                                          // kıvrık uç
      const t = (z - 26.5) / 3.55;
      le += 2.6 * t * t;
      chord = 2.7 - 1.75 * t;
    }
    out.push({ span: z, le, chord, t: z < 9.5 ? 0.135 - 0.03 * (z / 9.5) : 0.105 - 0.02 * (z / 30), off: wingY(z) });
  }
  return out;
}

// ------------------------------------------------------------------ motor (GEnx-1B)
function chevronNacelle(mat, lipMat) {
  const group = new THREE.Group();
  // Dış kaporta: profil boyunca halkalar; son halka tırtıklı (chevron)
  const prof = [[0.15, 1.56], [0.5, 1.61], [1.3, 1.65], [2.6, 1.62], [3.8, 1.53], [4.9, 1.36], [5.6, 1.22]];
  const SEG = 96, TEETH = 14;
  const pos = [], idx = [];
  prof.forEach(([l, r], k) => {
    for (let j = 0; j <= SEG; j++) {
      const th = (j / SEG) * TAU;
      let ll = l, rr = r;
      if (k === prof.length - 1) {
        const f = (j / SEG) * TEETH % 1;
        const tri = 1 - Math.abs(f * 2 - 1);          // 0..1..0
        ll += 0.42 * tri;
        rr -= 0.06 * tri;
      }
      pos.push(-ll, rr * Math.cos(th), rr * Math.sin(th));
    }
  });
  for (let k = 0; k < prof.length - 1; k++) {
    for (let j = 0; j < SEG; j++) {
      const a = k * (SEG + 1) + j, b = a + SEG + 1;
      idx.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  const cowl = new THREE.Mesh(g, mat);
  group.add(cowl);

  // Giriş dudağı (metal) + iç yüzey
  const lipPts = [[1.36, 1.2], [1.42, 0.4], [1.47, 0.08], [1.52, 0], [1.56, 0.15]]
    .map(([r, y]) => new THREE.Vector2(r, y));
  const lip = new THREE.Mesh(new THREE.LatheGeometry(lipPts, 64), lipMat);
  lip.rotation.z = Math.PI / 2;           // lathe ekseni (+y) → geriye (−x)
  group.add(lip);

  // Fan: koyu disk + kanatçık izleri + sivri göbek
  const fanTex = (() => {
    const c = document.createElement("canvas");
    c.width = c.height = 256;
    const x = c.getContext("2d");
    x.fillStyle = "#12151a";
    x.fillRect(0, 0, 256, 256);
    x.translate(128, 128);
    for (let i = 0; i < 18; i++) {
      x.rotate(TAU / 18);
      x.fillStyle = "#2b3038";
      x.beginPath();
      x.moveTo(0, 0);
      x.quadraticCurveTo(70, -20, 128, -6);
      x.lineTo(128, 6);
      x.quadraticCurveTo(70, 0, 0, 0);
      x.fill();
    }
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  })();
  const fan = new THREE.Mesh(new THREE.CircleGeometry(1.36, 48),
    new THREE.MeshStandardMaterial({ map: fanTex, roughness: 0.6, metalness: 0.6 }));
  fan.rotation.y = Math.PI / 2;
  fan.position.x = -1.15;
  fan.userData.spin = true;
  group.add(fan);
  const spinner = new THREE.Mesh(new THREE.ConeGeometry(0.42, 0.9, 32),
    new THREE.MeshStandardMaterial({ color: 0x9aa4b1, roughness: 0.3, metalness: 0.9 }));
  spinner.rotation.z = -Math.PI / 2;      // uç öne (+x)
  spinner.position.x = -0.75;
  group.add(spinner);

  // Çekirdek kaporta ve egzoz konisi
  const core = new THREE.Mesh(new THREE.LatheGeometry(
    [[1.1, 4.8], [0.98, 5.6], [0.74, 7.1], [0.52, 7.2], [0.38, 7.9], [0.01, 8.7]]
      .map(([r, y]) => new THREE.Vector2(r, y)), 48),
    new THREE.MeshStandardMaterial({ color: 0x59606a, roughness: 0.45, metalness: 0.8, side: THREE.DoubleSide }));
  core.rotation.z = Math.PI / 2;
  group.add(core);
  return group;
}

// ------------------------------------------------------------------ ışıklar
function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.2, "rgba(255,255,255,.65)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

// ------------------------------------------------------------------ montaj
export function build787() {
  const plane = new THREE.Group();
  plane.name = "B787";
  const tex = liveryTextures();

  const hullMat = new THREE.MeshPhysicalMaterial({
    map: tex.map, emissiveMap: tex.emissiveMap, emissive: 0xffffff, emissiveIntensity: 0.0,
    roughness: 0.32, metalness: 0.05, clearcoat: 1, clearcoatRoughness: 0.18,
  });
  const wingMat = new THREE.MeshPhysicalMaterial({ color: 0xc9d0d9, roughness: 0.42, metalness: 0.25,
                                                   clearcoat: 0.4, clearcoatRoughness: 0.3 });
  const navy = new THREE.MeshPhysicalMaterial({ color: 0x0b2545, roughness: 0.3, metalness: 0.1,
                                                clearcoat: 1, clearcoatRoughness: 0.2 });
  const white = new THREE.MeshPhysicalMaterial({ color: 0xf1f3f6, roughness: 0.3, metalness: 0.05,
                                                 clearcoat: 1, clearcoatRoughness: 0.2 });
  const lipMat = new THREE.MeshStandardMaterial({ color: 0xc4ccd6, roughness: 0.2, metalness: 1, side: THREE.DoubleSide });
  // Kesitten üretilen yüzeylerin sarım yönüne bağlı kalmamak için iki yüzlü
  wingMat.side = white.side = navy.side = THREE.DoubleSide;

  const hull = new THREE.Mesh(fuselageGeometry(), hullMat);
  hull.name = "hull";
  plane.add(hull);

  // Kanat–gövde birleşim kaportası
  const fairing = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 24), white);
  fairing.scale.set(10.5, 1.35, 2.95);
  fairing.position.set(X(27.5), -2.05, 0);
  plane.add(fairing);

  // Kanatlar
  const ws = wingStations();
  plane.add(new THREE.Mesh(surfaceGeometry(ws), wingMat));
  plane.add(new THREE.Mesh(surfaceGeometry(ws, { mirror: true }), wingMat));

  // Flap ray kaportaları (kanat altı)
  for (const z of [6.5, 12.5, 17.5, 22]) {
    for (const sign of [1, -1]) {
      const st = ws.find((w) => w.span >= z) || ws[ws.length - 1];
      const f = new THREE.Mesh(new THREE.ConeGeometry(0.32, 3.4, 12), wingMat);
      f.rotation.z = Math.PI / 2;           // uç geriye
      f.position.set(X(st.le + st.chord * 0.92), wingY(z) - 0.32, sign * z);
      f.scale.set(1, 1, 0.7);
      plane.add(f);
    }
  }

  // Yatay stabilizeler
  const hs = [0, 2, 4.5, 7, 9.8].map((z) => {
    const t = z / 9.8;
    return { span: z, le: 54.2 + z * Math.tan((36 * Math.PI) / 180), chord: 6.1 - 4.1 * t, t: 0.1,
             off: 1.25 + z * Math.tan((7 * Math.PI) / 180) };
  });
  plane.add(new THREE.Mesh(surfaceGeometry(hs), wingMat));
  plane.add(new THREE.Mesh(surfaceGeometry(hs, { mirror: true }), wingMat));

  // Dikey stabilize (lacivert kuyruk)
  const vs = [1.6, 3, 5.5, 8, 10.6, 11.3].map((y) => {
    const t = (y - 1.6) / 9.7;
    return { span: y, le: 48.8 + (y - 1.6) * Math.tan((40 * Math.PI) / 180), chord: 9.6 - 6.2 * t, t: 0.1, off: 0 };
  });
  plane.add(new THREE.Mesh(surfaceGeometry(vs, { axis: "y" }), navy));

  // Motorlar + pilonlar
  const engines = [];
  for (const sign of [1, -1]) {
    const z = sign * 9.9;
    const eng = chevronNacelle(white, lipMat);
    eng.position.set(X(17.6), wingY(9.9) - 2.05, z);
    eng.rotation.y = sign * 0.02;
    plane.add(eng);
    engines.push(eng);
    const pylon = new THREE.Mesh(new THREE.CapsuleGeometry(0.5, 7.5, 6, 12), white);
    pylon.rotation.z = Math.PI / 2 - 0.05;
    pylon.scale.set(1.25, 1, 0.45);
    pylon.position.set(X(23.4), wingY(9.9) - 0.95, z);
    plane.add(pylon);
  }

  // Seyir ışıkları
  const glow = glowTexture();
  const light = (color, size, pos) => {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, transparent: true,
                                                           blending: THREE.AdditiveBlending, depthWrite: false }));
    sp.scale.set(size, size, size);
    sp.position.copy(pos);
    plane.add(sp);
    return sp;
  };
  const tipL = new THREE.Vector3(X(ws.at(-1).le + 0.3), wingY(30) + 0.1, -30.1);
  const tipR = new THREE.Vector3(X(ws.at(-1).le + 0.3), wingY(30) + 0.1, 30.1);
  const lights = {
    navL: light(0xff2a3a, 2.2, tipL),
    navR: light(0x2aff8a, 2.2, tipR),
    strobeL: light(0xffffff, 4.5, tipL.clone().add(new THREE.Vector3(-0.6, 0, 0))),
    strobeR: light(0xffffff, 4.5, tipR.clone().add(new THREE.Vector3(-0.6, 0, 0))),
    tail: light(0xffffff, 1.8, new THREE.Vector3(X(LENGTH) - 0.1, section(LENGTH).cy, 0)),
    beaconTop: light(0xff2020, 2.4, hullPoint(30, Math.PI, new THREE.Vector3(), 0.15)),
    beaconBottom: light(0xff2020, 2.4, hullPoint(26, 0, new THREE.Vector3(), 0.15)),
  };

  plane.userData = { hullMat, lights, engines };
  return plane;
}

/** Işıkların yanıp sönmesi ve fanların dönmesi (t: saniye). */
export function animate787(plane, t, { night = 1 } = {}) {
  const { lights, engines, hullMat } = plane.userData;
  const strobe = (t % 1.2) < 0.06 || ((t + 0.15) % 1.2) < 0.05 ? 1 : 0;
  lights.strobeL.material.opacity = strobe;
  lights.strobeR.material.opacity = strobe;
  const beacon = Math.max(0, Math.sin(t * 2 * Math.PI * 0.9)) ** 6;
  lights.beaconTop.material.opacity = beacon;
  lights.beaconBottom.material.opacity = Math.max(0, Math.sin(t * 2 * Math.PI * 0.9 + Math.PI)) ** 6;
  lights.navL.material.opacity = lights.navR.material.opacity = lights.tail.material.opacity = 0.35 + 0.65 * night;
  hullMat.emissiveIntensity = 0.15 + 1.1 * night;
  for (const e of engines) {
    for (const c of e.children) if (c.userData.spin) c.rotation.x = t * 9;
  }
}
