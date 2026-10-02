/* Ana sayfa gökyüzü: fotoğraf değil, uçakla aynı sahnede çizilen gün batımı.
   Üst katmanda ince bulut şeritleri, ufkun altında ışıkla boyanmış bir bulut denizi, daha aşağıda
   seyrek şehir ışıkları. Uçağın ışığı ve yansımaları da bu aynı gökyüzünden üretilir (PMREM), bu yüzden
   uçak ile arka plan aynı atmosferi paylaşır: güneş arkadan vurur, bulut denizi alttan sıcak bir
   yansıma verir, kamera dönünce gökyüzü de perspektife uygun kayar.

   Gökyüzü düşük çözünürlüklü bir hedefe çizilir ve ekrana yükseltilerek basılır; bulutlar yumuşak
   olduğu için fark edilmez, maliyet düşük kalır. */
import * as THREE from "three";

export const SUN_BASE = new THREE.Vector3(-0.527, 0.075, -0.847).normalize();   // ufka yakın, uçağın arkasında

const GLSL = /* glsl */ `
uniform vec3 uSun;
uniform float uTime;

float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float vnoise(vec2 p){
  vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p){
  float s = 0.0, a = 0.5; mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++) { s += a * vnoise(p); p = m * p; a *= 0.5; }
  return s;
}

const vec3 SUN_COL = vec3(1.0, 0.42, 0.16);

// Gökyüzü: ufukta dar sıcak bir bant, hemen üstünde alacakaranlık, tepede derin lacivert.
vec3 skyGrad(vec3 rd){
  float k = clamp(rd.y, 0.0, 1.0);
  float sd = max(dot(rd, uSun), 0.0);
  vec3 hor = vec3(0.95, 0.46, 0.22);
  vec3 mid = vec3(0.30, 0.20, 0.34);
  vec3 zen = vec3(0.020, 0.036, 0.095);
  vec3 c = mix(hor, mid, smoothstep(0.0, 0.075, k));
  c = mix(c, zen, smoothstep(0.035, 0.30, k));
  float g = pow(sd, 6.0) * 0.30 + pow(sd, 36.0) * 0.42 + pow(sd, 2400.0) * 1.5;
  c += SUN_COL * g * (1.0 - smoothstep(0.0, 0.5, k) * 0.7);
  return c;
}

// Bir yükseklikteki bulut düzlemi: güneşe bakan kenarlar sıcak, gölge tarafı mor-lacivert.
vec4 cloudLayer(vec3 ro, vec3 rd, float alt, float sc, float cov, float stretch, float seed){
  float t = (alt - ro.y) / rd.y;
  if (t < 0.0) return vec4(0.0);
  vec3 p = ro + rd * t;
  float zoom = 1.0 + 6.0 * rd.y * rd.y;
  vec2 uv = p.xz * sc * zoom; uv.x *= stretch; uv += vec2(uTime * 0.0035, uTime * 0.0022) + seed;
  vec2 q = vec2(fbm(uv * 0.6 + 3.1), fbm(uv * 0.6 + 7.7));
  float d = fbm(uv + 1.8 * q);
  vec2 toSun = normalize(uSun.xz + 1e-4) * 0.17;
  float dl = fbm(uv + 1.8 * q + toSun);
  float dens = smoothstep(cov, cov + 0.22, d);
  float lit = clamp(0.38 + (d - dl) * 8.0, 0.0, 1.0);
  float sunSide = pow(max(dot(normalize(vec3(rd.x, 0.0, rd.z) + 1e-5), normalize(vec3(uSun.x, 0.0, uSun.z))), 0.0), 2.0);
  sunSide = mix(0.45, sunSide, smoothstep(0.12, 0.5, length(rd.xz)));   // yukarıdan bakışta yön hesabı sütun üretmesin
  vec3 shade = vec3(0.075, 0.095, 0.215);
  vec3 body  = vec3(0.38, 0.21, 0.31);
  vec3 hi    = vec3(1.0, 0.60, 0.32);
  vec3 col = mix(shade, body, smoothstep(0.05, 0.55, lit));
  col = mix(col, hi, smoothstep(0.55, 1.0, lit) * (0.35 + 0.65 * sunSide));
  col += vec3(1.0, 0.62, 0.36) * (pow(sunSide, 3.0) * 0.28 * lit + 0.05) * dens;
  float far = 1.0 - exp(-t * 0.0013);
  float a = dens * (1.0 - far * 0.9) * smoothstep(0.0, 0.05, abs(rd.y));
  return vec4(col, a);
}

vec3 skyColor(vec3 ro, vec3 rd){
  vec3 col = skyGrad(rd);
  if (rd.y > 0.0) {
    vec4 c = cloudLayer(ro, rd, 340.0, 0.0017, 0.52, 0.50, 11.0);       // ince, uzun bulut şeritleri
    col = mix(col, c.rgb, c.a * 0.7);
  } else {
    // En altta koyu arazi ve seyrek şehir ışıkları (tepeden bakışta görünür)
    float down = clamp(-rd.y, 0.0, 1.0);
    float tg = (-1500.0 - ro.y) / min(rd.y, -0.0001);
    vec3 g = ro + rd * tg;
    vec3 land = mix(vec3(0.016, 0.022, 0.045), vec3(0.050, 0.050, 0.080), fbm(g.xz * 0.0007));
    vec2 gc = g.xz * 0.060; vec2 ci = floor(gc), cf = fract(gc) - 0.5;
    float cluster = smoothstep(0.40, 0.66, fbm(g.xz * 0.0016 + 4.0));
    float on = step(0.55, hash(ci)) * cluster;
    float lamp = on * smoothstep(0.16, 0.0, length(cf - (vec2(hash(ci + 3.1), hash(ci + 8.7)) - 0.5) * 0.5));
    land += vec3(1.0, 0.60, 0.26) * lamp * 2.2;
    // Ufuk: gökyüzünün ufuk rengiyle kesintisiz birleşir
    vec3 hz = mix(skyGrad(normalize(vec3(rd.x, 0.0, rd.z))), vec3(0.52, 0.27, 0.19), smoothstep(0.05, 0.55, down));   // yukarıdan bakışta yönden bağımsız
    vec3 haze = mix(hz * 0.30, hz, smoothstep(-0.20, 0.0, rd.y));
    float fogT = 1.0 - exp(-tg * 0.0006);
    col = mix(land, haze, clamp(fogT * 1.2, 0.0, 1.0));
    float cov = 0.40 + down * 0.12;
    vec4 far = cloudLayer(ro, rd, -170.0, 0.0010, cov, 1.0, 3.0);       // uzak bulut denizi
    col = mix(col, far.rgb, far.a * 0.97);
    vec4 near = cloudLayer(ro, rd, -62.0, 0.0023, cov + 0.10, 1.0, 19.0); // yakın, seyrek bulutlar
    col = mix(col, near.rgb, near.a * 0.9);
    col = mix(col, haze, (1.0 - smoothstep(0.0, 0.05, -rd.y)) * 0.85);   // ufka yaklaştıkça sis
  }
  return col;
}
`;

export function createSky(renderer, scene, sweep) {
  const U = {
    uSun: { value: SUN_BASE.clone() }, uTime: { value: 0 },
    uCamPos: { value: new THREE.Vector3() }, uCamWorld: { value: new THREE.Matrix4() },
    uInvProj: { value: new THREE.Matrix4() },
  };

  // 1) Düşük çözünürlüklü hedefe gökyüzünü çiz (kameranın her pikseli için bakış yönü)
  const rt = new THREE.WebGLRenderTarget(2, 2, { minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false });
  const skyScene = new THREE.Scene();
  const rtMat = new THREE.ShaderMaterial({
    uniforms: U, depthTest: false, depthWrite: false,
    vertexShader: "varying vec2 vP; void main(){ vP = position.xy; gl_Position = vec4(position.xy, 0.0, 1.0); }",
    fragmentShader: `${GLSL}
      uniform vec3 uCamPos; uniform mat4 uCamWorld, uInvProj; varying vec2 vP;
      void main(){
        vec4 v = uInvProj * vec4(vP, 1.0, 1.0);
        vec3 rd = normalize((uCamWorld * vec4(v.xyz / v.w, 0.0)).xyz);
        gl_FragColor = vec4(skyColor(uCamPos, rd), 1.0);
      }`,
  });
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), rtMat);
  quad.frustumCulled = false;
  skyScene.add(quad);
  const skyCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  // 2) Ana sahnede ekrana bas: teknik çizimden gerçeğe silme çizgisinin sağında görünür
  const dispMat = new THREE.ShaderMaterial({
    uniforms: { ...sweep, uTex: { value: rt.texture }, uRes: { value: new THREE.Vector2(1, 1) } },
    depthTest: false, depthWrite: false,
    vertexShader: "void main(){ gl_Position = vec4(position.xy, 0.0, 1.0); }",
    fragmentShader: `uniform sampler2D uTex; uniform vec2 uRes; uniform float uWipe; uniform float uGlowW;
      float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
      void main(){
        float sweepD = gl_FragCoord.x - uWipe;
        if (sweepD < 0.0) discard;
        vec3 c = texture2D(uTex, gl_FragCoord.xy / uRes).rgb;
        c += vec3(1.0, 0.74, 0.38) * (1.0 - smoothstep(0.0, uGlowW * 8.0, sweepD)) * 1.3;
        c += (h(gl_FragCoord.xy) - 0.5) / 160.0;                // bantlanmayı önleyen çok ince titreşim
        gl_FragColor = vec4(c, 1.0);
      }`,
  });
  const backdrop = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), dispMat);
  backdrop.frustumCulled = false;
  backdrop.renderOrder = -100;
  scene.add(backdrop);

  // 3) Uçağın ortamı: aynı gökyüzü bir küreye çizilip PMREM'e dönüştürülür
  function buildEnvironment(pmrem) {
    const envScene = new THREE.Scene();
    const mat = new THREE.ShaderMaterial({
      uniforms: { uSun: U.uSun, uTime: U.uTime }, side: THREE.BackSide, depthWrite: false,
      vertexShader: "varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: `${GLSL}
        varying vec3 vD;
        void main(){
          vec3 c = skyColor(vec3(0.0, 13.0, 0.0), normalize(vD));
          gl_FragColor = vec4(pow(c, vec3(2.2)) * 1.15, 1.0);     // doğrusal ışık değerine
        }`,
    });
    envScene.add(new THREE.Mesh(new THREE.SphereGeometry(500, 48, 24), mat));
    const env = pmrem.fromScene(envScene, 0.015);
    mat.dispose();
    return env.texture;
  }

  const size = new THREE.Vector2();
  return {
    uniforms: U,
    buildEnvironment,
    resize(scale) {
      renderer.getDrawingBufferSize(size);
      dispMat.uniforms.uRes.value.copy(size);
      rt.setSize(Math.max(2, Math.round(size.x * scale)), Math.max(2, Math.round(size.y * scale)));
    },
    /** Kamera güncellendikten sonra çağrılır; gökyüzü hedefini yeniler. */
    render(camera, time) {
      camera.updateMatrixWorld();
      U.uTime.value = time;
      U.uCamPos.value.copy(camera.position);
      U.uCamWorld.value.copy(camera.matrixWorld);
      U.uInvProj.value.copy(camera.projectionMatrixInverse);
      const prev = renderer.getRenderTarget();
      renderer.setRenderTarget(rt);
      renderer.render(skyScene, skyCam);
      renderer.setRenderTarget(prev);
    },
  };
}
