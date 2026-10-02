// 787 modelinin teknik çizim kenarlarını önceden hesaplar (tarayıcıda ~1,5 sn sürdüğü için).
// Kullanım: node tools/build_edges.mjs <model.glb> <çıktı.bin> [eşik_derece=30]
// Çıktı: "EDG1" | uint32 segment sayısı | float32 min[3] | float32 ölçek[3] | uint16 konumlar (seg*2*3)
// Konumlar glTF sahnesinin kök uzayındadır (düğüm dönüşümleri uygulanmış), kutu içinde 16 bit nicemlenmiş.
// Gereken paketler: @gltf-transform/core, @gltf-transform/extensions, meshoptimizer, draco3dgltf
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import draco3d from 'draco3dgltf';
import { writeFileSync } from 'node:fs';

const [inp, out, thr = '30'] = process.argv.slice(2);
const cosT = Math.cos((Number(thr) * Math.PI) / 180);
await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.decoder': MeshoptDecoder, 'draco3d.decoder': await draco3d.createDecoderModule() });
const doc = await io.read(inp);

const mul = (m, x, y, z) => [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
const segs = [];
for (const node of doc.getRoot().listNodes()) {
  const mesh = node.getMesh();
  if (!mesh) continue;
  const m = node.getWorldMatrix();
  for (const prim of mesh.listPrimitives()) {
    if (prim.getMode() !== 4) continue;               // yalnızca üçgenler
    const pa = prim.getAttribute('POSITION');
    const n = pa.getCount();
    const P = new Float64Array(n * 3);
    const e = [0, 0, 0];
    for (let i = 0; i < n; i++) { pa.getElement(i, e); const w = mul(m, e[0], e[1], e[2]); P.set(w, i * 3); }
    // Aynı konumdaki köşeleri birleştir (EdgesGeometry ile aynı mantık)
    const key = new Map(), id = new Uint32Array(n);
    for (let i = 0; i < n; i++) {
      const k = `${Math.round(P[i * 3] * 1e4)},${Math.round(P[i * 3 + 1] * 1e4)},${Math.round(P[i * 3 + 2] * 1e4)}`;
      let v = key.get(k);
      if (v === undefined) { v = i; key.set(k, i); }
      id[i] = v;
    }
    const idx = prim.getIndices();
    const tri = idx ? idx.getArray() : Uint32Array.from({ length: n }, (_, i) => i);
    const edges = new Map();
    for (let t = 0; t < tri.length; t += 3) {
      const a = id[tri[t]], b = id[tri[t + 1]], c = id[tri[t + 2]];
      if (a === b || b === c || a === c) continue;
      const ux = P[b * 3] - P[a * 3], uy = P[b * 3 + 1] - P[a * 3 + 1], uz = P[b * 3 + 2] - P[a * 3 + 2];
      const vx = P[c * 3] - P[a * 3], vy = P[c * 3 + 1] - P[a * 3 + 1], vz = P[c * 3 + 2] - P[a * 3 + 2];
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const l = Math.hypot(nx, ny, nz) || 1; nx /= l; ny /= l; nz /= l;
      for (const [p, q] of [[a, b], [b, c], [c, a]]) {
        const k = p < q ? p * 4294967296 + q : q * 4294967296 + p;
        const ex = edges.get(k);
        if (!ex) edges.set(k, { p, q, nx, ny, nz, n: 1 });
        else if (ex.n === 1) { ex.n = 2; ex.dot = ex.nx * nx + ex.ny * ny + ex.nz * nz; }
        else ex.n++;
      }
    }
    for (const ex of edges.values()) {
      if (ex.n === 1 || (ex.n === 2 && ex.dot <= cosT)) segs.push(P[ex.p * 3], P[ex.p * 3 + 1], P[ex.p * 3 + 2], P[ex.q * 3], P[ex.q * 3 + 1], P[ex.q * 3 + 2]);
    }
  }
}
const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
for (let i = 0; i < segs.length; i++) { const a = i % 3; min[a] = Math.min(min[a], segs[i]); max[a] = Math.max(max[a], segs[i]); }
const scale = max.map((v, a) => (v - min[a]) / 65535 || 1);
const q = new Uint16Array(segs.length);
for (let i = 0; i < segs.length; i++) { const a = i % 3; q[i] = Math.round((segs[i] - min[a]) / scale[a]); }
const head = Buffer.alloc(32);
head.write('EDG1', 0, 'ascii'); head.writeUInt32LE(segs.length / 6, 4);
[...min, ...scale].forEach((v, i) => head.writeFloatLE(v, 8 + i * 4));
writeFileSync(out, Buffer.concat([head, Buffer.from(q.buffer)]));
console.log(`${segs.length / 6} segment, ${(32 + q.byteLength) / 1024 | 0} KB`);
