"""GÖKRADAR ana sayfa sahnesi: 787 + alacakaranlık gökyüzü + kamera yolu.

blender -b --python scene.py -- [--still hero|cockpit|window|top] [--pct 50] [--samples 64]
blender -b --python scene.py -- --anim 1 --pct 100 --samples 256   # final: frames/f_####.png
Sahneyi kurar, scene.blend olarak kaydeder; --still verilirse o istasyonu render alır.

Girdiler bu betikle aynı klasörde olmalı:
  b787.glb  → repoda bu klasörde (siteye sunulmaz, yalnızca sahne girdisi)
  sky.hdr   → Poly Haven "qwantani_dusk_2_puresky" 4K HDR (CC0)
Kareler sonra: python tools/encode_frames.py <klasör>/frames
Blender 4.5 LTS ile denendi (Cycles, OptiX).
"""
import math
import os
import sys

import bpy
import mathutils
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
opt = {args[i].lstrip("-"): args[i + 1] for i in range(0, len(args) - 1, 2)}

FRAMES = 180
# İstasyonlar: kare numarası (kaydırma sırası)
STATIONS = {"hero": 1, "cockpit": 60, "window": 115, "top": 180}

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
bpy.ops.import_scene.gltf(filepath=os.path.join(HERE, "b787.glb"))
meshes = [o for o in scene.objects if o.type == "MESH"]


def world_bbox(objs):
    mn = Vector((1e9,) * 3)
    mx = -mn
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mn = Vector(map(min, mn, w))
            mx = Vector(map(max, mx, w))
    return mn, mx


# --- uçağı kökene, burun +X, kanatlar Y, yukarı Z olacak şekilde hizala --------------------
root = bpy.data.objects.new("Plane787", None)
scene.collection.objects.link(root)
tops = {o for o in scene.objects if o.parent is None and o is not root}
for o in tops:
    o.parent = root
mn, mx = world_bbox(meshes)
size = mx - mn
length_axis = max(range(3), key=lambda i: size[i] if i != 2 else -1)   # X ya da Y (Z yükseklik)
cockpit = [o for o in meshes if "CockpitWindowFrame" in (o.parent.name if o.parent else o.name) or "CockpitWindowFrame" in o.name]
cmn, cmx = world_bbox(cockpit)
center = (mn + mx) / 2
nose_sign = 1 if ((cmn + cmx) / 2)[length_axis] > center[length_axis] else -1
# Burun +X'e bakacak şekilde Z etrafında döndür
yaw = {(0, 1): 0, (0, -1): math.pi, (1, 1): -math.pi / 2, (1, -1): math.pi / 2}[(length_axis, nose_sign)]
root.rotation_euler = (0, 0, yaw)
bpy.context.view_layer.update()
mn, mx = world_bbox(meshes)
root.location = -(mn + mx) / 2
bpy.context.view_layer.update()
mn, mx = world_bbox(meshes)
cmn, cmx = world_bbox(cockpit)
NOSE_X, TAIL_X = mx.x, mn.x
COCKPIT = (cmn + cmx) / 2
print("BBOX", tuple(round(v, 2) for v in mn), tuple(round(v, 2) for v in mx), "COCKPIT", tuple(round(v, 2) for v in COCKPIT))

# Yumuşak gölgeleme: model ayrık köşelerle geliyor (yakın çekimde basamaklı görünür).
# Çakışan köşeler birleştirilir, dosyadaki sabit normaller silinir, açıya göre yumuşatılır.
import bmesh
BIG_PARTS = ("Hull", "Wing", "Stabilizer", "Engine", "Hatch", "Door")   # normal aktarımı yapılan parçalar
for o in meshes:
    owner = o.parent.name if o.parent and o.parent is not root else o.name
    if o.data.has_custom_normals:
        bpy.context.view_layer.objects.active = o
        with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    for p in o.data.polygons:
        p.use_smooth = True
    with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
        bpy.ops.object.shade_auto_smooth(angle=math.radians(40))

# Pürüzsüz normaller: dosya biçimi büyük parçaları 32 bin yüzde böler; gövde kabuğu da gövde
# yarıları + kapı + kapak parçalarından oluşur. Her grup için parçalar tek görünmez kalıpta
# birleştirilir (köşeler kaynaştırılır) ve normaller bu ortak kalıptan aktarılır: parça
# sınırlarında ışık kesintisiz olur, asıl geometri değişmez.
FUSELAGE = ("Hull", "Hatch", "Door", "CockpitWindowFrame", "Gear")
groups = {}
for o in meshes:
    owner = o.parent.name if o.parent and o.parent is not root else o.name
    if not any(k in owner for k in BIG_PARTS + FUSELAGE):
        continue
    if any(s.material and s.material.name != "Body" for s in o.material_slots):
        continue   # metal/cam parçalar kendi yumuşatmasıyla kalır
    key = "fuselage" if any(k in owner for k in FUSELAGE) else owner.split(".")[0]
    groups.setdefault(key, []).append(o)
for key, objs in groups.items():
    bm = bmesh.new()
    for o in objs:
        tmp = o.data.copy(); tmp.transform(o.matrix_world)
        bm.from_mesh(tmp); bpy.data.meshes.remove(tmp)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    me = bpy.data.meshes.new(f"nrm_{key}"); bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    src = bpy.data.objects.new(f"nrm_{key}", me); scene.collection.objects.link(src)
    if key == "fuselage" and int(opt.get("lapit", 0)) > 0:   # deneme: varsayılan kapalı
        # Burun üstünde model minik basamaklardan oluşuyor; kalıp yumuşatılınca basamaklar
        # ışıkta görünmez olur (hacmi koruyan Laplace yumuşatma)
        ls = src.modifiers.new("Laplace", "LAPLACIANSMOOTH")
        ls.lambda_factor = float(opt.get("lap", 1.0)); ls.iterations = int(opt.get("lapit", 12))
        ls.use_volume_preserve = True
        with bpy.context.temp_override(object=src, active_object=src, selected_objects=[src], selected_editable_objects=[src]):
            bpy.ops.object.modifier_apply(modifier="Laplace")
    src.hide_render = True; src.hide_viewport = True
    for o in objs:
        dt = o.modifiers.new("SmoothNormals", "DATA_TRANSFER")
        dt.object = src; dt.use_loop_data = True; dt.data_types_loops = {"CUSTOM_NORMAL"}
        dt.loop_mapping = "POLYINTERP_NEAREST"
        dt.use_max_distance = True; dt.max_distance = 0.05
print("NORMAL GROUPS", {k: len(v) for k, v in groups.items()})


if "probe" in opt:   # teşhis: kokpit çevresindeki küçük parçalar
    for o in meshes:
        omn, omx = world_bbox([o]); c = (omn + omx) / 2
        if COCKPIT.x - 8 < c.x < NOSE_X + 1 and abs(c.y) < 3.5:
            owner = o.parent.name if o.parent and o.parent is not root else o.name
            print("NEAR", o.name, owner, len(o.data.polygons), tuple(round(v, 2) for v in c), [m.material.name for m in o.material_slots if m.material])
if "below" in opt:   # teşhis: gövde altına sarkan parçalar
    hull = [o for o in meshes if "Hull" in (o.parent.name if o.parent and o.parent is not root else o.name)]
    hmn, hmx = world_bbox(hull)
    for o in meshes:
        owner = o.parent.name if o.parent and o.parent is not root else o.name
        omn, omx = world_bbox([o])
        if omn.z < hmn.z + 0.4 and abs((omn.y + omx.y) / 2) < 6:
            print("BELOW", o.name, owner, len(o.data.polygons), round(omn.z - hmn.z, 2), tuple(round(v, 1) for v in (omn + omx) / 2), [m.material.name for m in o.material_slots if m.material])
if "ray" in opt:   # teşhis: burnun üstüne yukarıdan ışın gönder
    dg = bpy.context.evaluated_depsgraph_get()
    for dx in (-3.5, -3.0, -2.5, -2.0, -1.5, -1.0):
        for dy in (-0.6, 0.0, 0.6):
            hit, loc, nrm, idx, ob, _ = scene.ray_cast(dg, Vector((COCKPIT.x + dx, dy, 10)), Vector((0, 0, -1)))
            if hit:
                print("RAY", dx, dy, ob.name, (ob.parent.name if ob.parent else ""), tuple(round(v, 2) for v in loc), tuple(round(v, 2) for v in nrm))
# İniş takımı içeride (uçuşta) ama modelde takıma bağlı parçalar dışarıda kalmış: burun takımı
# lambaları ve servis paneli, ana takım bağlantıları. Bunlar render dışı bırakılır.
GEAR_PARTS = opt.get("gearparts", "LandingLight,TaxiLight,P40Service,Cube.027_11,Cube.028_12,Cylinder.002_97,Cube.016_137,Wingbox.002_119").split(",")
for o in meshes:
    owner = o.parent.name if o.parent and o.parent is not root else o.name
    if any(g and g in owner for g in GEAR_PARTS) or o.name in opt.get("hide", "").split(","):
        o.hide_render = True

# --- malzemeler ---------------------------------------------------------------------------
def node_mat(name):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return m, nt, bsdf


def simple(name, color, metallic=0.0, rough=0.4, coat=0.0, emission=None, strength=0.0, ior=1.5):
    m, nt, b = node_mat(name)
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["IOR"].default_value = ior
    if emission:
        b.inputs["Emission Color"].default_value = (*emission, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


def paint(name, color, grime=True):
    """Vernikli uçak boyası: pürüzlülükte hafif dalgalanma, çok ince yüzey kabarıklığı,
    alt gövdede hafif kirlenme. UV olmadığı için nesne koordinatları kullanılır."""
    m, nt, b = node_mat(name)
    N = nt.nodes.new
    tc = N("ShaderNodeTexCoord")
    # Pürüzlülük dalgalanması (büyük ölçekli)
    n1 = N("ShaderNodeTexNoise"); n1.inputs["Scale"].default_value = 0.35; n1.inputs["Detail"].default_value = 6
    nt.links.new(tc.outputs["Object"], n1.inputs["Vector"])
    r = N("ShaderNodeMapRange"); r.inputs[3].default_value = 0.22; r.inputs[4].default_value = 0.34
    nt.links.new(n1.outputs["Fac"], r.inputs[0]); nt.links.new(r.outputs[0], b.inputs["Roughness"])
    # İnce yüzey dokusu (portakal kabuğu boya)
    n2 = N("ShaderNodeTexNoise"); n2.inputs["Scale"].default_value = 60; n2.inputs["Detail"].default_value = 2
    nt.links.new(tc.outputs["Object"], n2.inputs["Vector"])
    bump = N("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.025; bump.inputs["Distance"].default_value = 0.002
    nt.links.new(n2.outputs["Fac"], bump.inputs["Height"]); nt.links.new(bump.outputs[0], b.inputs["Normal"])
    base = N("ShaderNodeRGB"); base.outputs[0].default_value = (*color, 1)
    col_out = base.outputs[0]
    if grime:
        # Alt gövdede ve arka kısımda hafif kir/egzoz izi (dünya Z'sine göre)
        sep = N("ShaderNodeSeparateXYZ"); nt.links.new(tc.outputs["Object"], sep.inputs[0])
        zr = N("ShaderNodeMapRange"); zr.inputs[1].default_value = -1.0; zr.inputs[2].default_value = -3.2
        nt.links.new(sep.outputs["Z"], zr.inputs[0])
        n3 = N("ShaderNodeTexNoise"); n3.inputs["Scale"].default_value = 2.5; n3.inputs["Detail"].default_value = 8
        nt.links.new(tc.outputs["Object"], n3.inputs["Vector"])
        mul = N("ShaderNodeMath"); mul.operation = "MULTIPLY"
        nt.links.new(zr.outputs[0], mul.inputs[0]); nt.links.new(n3.outputs["Fac"], mul.inputs[1])
        mix = N("ShaderNodeMix"); mix.data_type = "RGBA"
        mix.inputs[7].default_value = (0.42, 0.40, 0.37, 1)
        f = N("ShaderNodeMath"); f.operation = "MULTIPLY"; f.inputs[1].default_value = 0.35
        nt.links.new(mul.outputs[0], f.inputs[0]); nt.links.new(f.outputs[0], mix.inputs[0])
        nt.links.new(col_out, mix.inputs[6]); col_out = mix.outputs[2]
    nt.links.new(col_out, b.inputs["Base Color"])
    b.inputs["Coat Weight"].default_value = 0.6
    b.inputs["Coat Roughness"].default_value = 0.06
    b.inputs["Specular IOR Level"].default_value = 0.5
    return m


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


TAIL = opt.get("tail", "#10304F")
MATS = {
    "Body": paint("Body", srgb("#E6E9EC")),
    "Window": simple("Window", srgb("#0B0F14"), metallic=0.0, rough=0.04, coat=1.0, ior=1.52),
    "Glass": simple("Glass", srgb("#141A21"), metallic=0.0, rough=0.02, coat=1.0, ior=1.52),
    "Mirror": simple("Mirror", (0.9, 0.9, 0.9), metallic=1.0, rough=0.08),
    "Silver": simple("Silver", srgb("#B9BEC4"), metallic=1.0, rough=0.28),
    "RawMetal": simple("RawMetal", srgb("#8D939A"), metallic=1.0, rough=0.38),
    "BurnedMetal": simple("BurnedMetal", srgb("#3A332E"), metallic=1.0, rough=0.45),
    "Material.001": simple("Material.001", srgb("#2A2E33"), metallic=1.0, rough=0.32),
    "BlackParts": simple("BlackParts", srgb("#5A6067"), metallic=0.7, rough=0.4),   # sensörler: delik gibi durmasın
    "material_0": simple("material_0", srgb("#D9DCDF"), rough=0.5),
    "RedParts": simple("RedParts", srgb("#B3261E"), rough=0.45),
    "Red_Beacon": simple("Red_Beacon", srgb("#FF2A1A"), rough=0.3, emission=srgb("#FF2A1A"), strength=25),
    "NAV_Light": simple("NAV_Light", (1, 1, 1), rough=0.3, emission=(1, 1, 1), strength=15),
}
tail_paint = paint("TailPaint", srgb(TAIL), grime=False)
for o in meshes:
    owner = o.parent.name if o.parent and o.parent is not root else o.name
    for s in o.material_slots:
        if not s.material:
            continue
        if s.material.name == "Body" and "VerticalStabilizer" in owner:
            s.material = tail_paint
        elif s.material.name in MATS:
            s.material = MATS[s.material.name]

# --- dünya: HDRI gökyüzü + uyumlu güneş -----------------------------------------------------
world = bpy.data.worlds.new("Sky"); scene.world = world
world.use_nodes = True
wt = world.node_tree; wt.nodes.clear()
env = wt.nodes.new("ShaderNodeTexEnvironment")
env.image = bpy.data.images.load(os.path.join(HERE, "sky.hdr"))
mapping = wt.nodes.new("ShaderNodeMapping"); tc = wt.nodes.new("ShaderNodeTexCoord")
SKY_ROT = math.radians(float(opt.get("skyrot", 110)))
mapping.inputs["Rotation"].default_value[2] = SKY_ROT
bg = wt.nodes.new("ShaderNodeBackground"); bg.inputs["Strength"].default_value = float(opt.get("sky", 0.15))
out = wt.nodes.new("ShaderNodeOutputWorld")
wt.links.new(tc.outputs["Generated"], mapping.inputs[0]); wt.links.new(mapping.outputs[0], env.inputs[0])
wt.links.new(env.outputs[0], bg.inputs[0])
# Kameranın doğrudan gördüğü gök koyulaştırılır (arayüzün koyu zeminiyle birleşsin);
# uçağı aydınlatan ve camlarda yansıyan gök tam parlaklıkta kalır.
bg_cam = wt.nodes.new("ShaderNodeBackground"); bg_cam.inputs["Strength"].default_value = 1.0
# Bakış yönünün yüksekliğine göre gece mavisi geçişi; ufukta sönük sıcak şerit
sepw = wt.nodes.new("ShaderNodeSeparateXYZ"); wt.links.new(tc.outputs["Generated"], sepw.inputs[0])
zmap = wt.nodes.new("ShaderNodeMapRange"); zmap.inputs[1].default_value = -1.0   # z: -1..1 → 0..1
wt.links.new(sepw.outputs["Z"], zmap.inputs[0])
ramp = wt.nodes.new("ShaderNodeValToRGB"); wt.links.new(zmap.outputs[0], ramp.inputs[0])
ramp.color_ramp.interpolation = "B_SPLINE"
els = ramp.color_ramp.elements
STOPS = [(0.0, "#05080E"), (0.485, "#0A111C"), (0.5, "#232536"), (0.515, "#141F33"), (0.6, "#0E1828"), (1.0, "#05090F")]
els[0].position, els[0].color = STOPS[0][0], (*srgb(STOPS[0][1]), 1)
els[1].position, els[1].color = STOPS[-1][0], (*srgb(STOPS[-1][1]), 1)
for p, h in STOPS[1:-1]:
    e = els.new(p); e.color = (*srgb(h), 1)
# Gerçek göğün dokusu çok az karıştırılır (tamamen düz geçiş yapay durur)
tex = wt.nodes.new("ShaderNodeMix"); tex.data_type = "RGBA"; tex.blend_type = "SCREEN"
tex.inputs[0].default_value = float(opt.get("camsky", 0.003))
wt.links.new(ramp.outputs[0], tex.inputs[6]); wt.links.new(env.outputs[0], tex.inputs[7])
wt.links.new(tex.outputs[2], bg_cam.inputs[0])
lp = wt.nodes.new("ShaderNodeLightPath"); mixw = wt.nodes.new("ShaderNodeMixShader")
wt.links.new(lp.outputs["Is Camera Ray"], mixw.inputs[0])
wt.links.new(bg.outputs[0], mixw.inputs[1]); wt.links.new(bg_cam.outputs[0], mixw.inputs[2])
wt.links.new(mixw.outputs[0], out.inputs[0])

sun_data = bpy.data.lights.new("Sun", "SUN")
sun_data.energy = float(opt.get("sun", 9.0)); sun_data.color = srgb("#FFC9A0"); sun_data.angle = math.radians(1.2)
sun = bpy.data.objects.new("Sun", sun_data); scene.collection.objects.link(sun)
# Güneş: ufka yakın (alacakaranlık), gökyüzündeki parlak bölgeyle aynı yönde
sun_el, sun_az = math.radians(float(opt.get("sunel", 12))), SKY_ROT + math.radians(float(opt.get("sunaz", 150)))
sun.rotation_euler = (math.pi / 2 - sun_el, 0, sun_az + math.pi / 2)

# --- kamera yolu ----------------------------------------------------------------------------
cam_data = bpy.data.cameras.new("Cam"); cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam); scene.camera = cam
cam_data.sensor_width = 36
cam_data.dof.use_dof = True
target = bpy.data.objects.new("Target", None); scene.collection.objects.link(target)
track = cam.constraints.new("TRACK_TO"); track.target = target; track.track_axis = "TRACK_NEGATIVE_Z"; track.up_axis = "UP_Y"
cam_data.dof.focus_object = target

L = NOSE_X - TAIL_X
# Yolcu penceresi sırası: modelin pencere bağlantı noktası (gövde yan yüzü, kanat önü)
WIN = Vector((float(opt.get("winx", 4.0)), -2.98, COCKPIT.z - 0.05))
# (kamera konumu, hedef, odak uzaklığı mm, f-stop)
KEYS = {
    "hero": (Vector((NOSE_X + 55, -70, float(opt.get("heroz", -14)))), Vector((-4, 6, 2.5)), 70, 16),
    "cockpit": (Vector((NOSE_X + 26, 21, COCKPIT.z + 0.4)), Vector((COCKPIT.x - 3.0, COCKPIT.y, COCKPIT.z - 0.7)), 85, 5.6),   # sağ taraf: sol üstteki model kusuru kadraj dışında
    "window": (WIN + Vector((-5.5, -2.6, 0.9)), WIN + Vector((0.6, 0.0, 0.0)), 35, 2.8),
    "top": (Vector((-4, -6, 95)), Vector((-2, 0, 0)), 50, 22),
}
# Ara noktalar: kamera istasyonlar arasında gövdeye yaklaşmadan dışarıdan dolaşır
VIA = {
    30: (Vector((NOSE_X + 48, -6, -6)), Vector((0, 0, -1)), 60, 14),                          # burnun önünden dönüş
    74: (Vector((COCKPIT.x - 4, 14, COCKPIT.z + 9)), Vector((COCKPIT.x - 10, 0, COCKPIT.z)), 55, 9),        # sağ üstten tırmanış
    88: (Vector((COCKPIT.x - 10, 0, COCKPIT.z + 16)), Vector((COCKPIT.x - 16, 0, COCKPIT.z)), 45, 10),   # gövdenin üstünden
    101: (WIN + Vector((-10, -11, 6.5)), WIN + Vector((2, 0, -0.5)), 40, 8),                    # pencereye inişte uzak dur
    148: (Vector((-2, -40, 45)), Vector((-2, 0, 0)), 45, 16),
}
for name, (pos, tgt, lens, fstop) in list(KEYS.items()) + list(VIA.items()):
    f = STATIONS[name] if name in STATIONS else name
    cam.location = pos; cam.keyframe_insert("location", frame=f)
    target.location = tgt; target.keyframe_insert("location", frame=f)
    cam_data.lens = lens; cam_data.keyframe_insert("lens", frame=f)
    cam_data.dof.aperture_fstop = fstop; cam_data.dof.keyframe_insert("aperture_fstop", frame=f)
for ob in (cam, target, cam_data):
    ad = ob.animation_data
    if ad and ad.action:
        for fc in ad.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"; kp.easing = "EASE_IN_OUT"
                kp.handle_left_type = kp.handle_right_type = "AUTO_CLAMPED"

# --- render ayarları ------------------------------------------------------------------------
scene.frame_start, scene.frame_end = 1, FRAMES
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
prefs.compute_device_type = "OPTIX"
prefs.get_devices()
for d in prefs.devices:
    d.use = d.type == "OPTIX"
scene.cycles.device = "GPU"
scene.cycles.samples = int(opt.get("samples", 64))
scene.cycles.use_denoising = True
scene.cycles.denoiser = "OPTIX"
scene.cycles.max_bounces = 8
scene.render.resolution_x, scene.render.resolution_y = 2560, 1440
scene.render.resolution_percentage = int(opt.get("pct", 50))
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = opt.get("look", "AgX - Medium High Contrast")
scene.view_settings.exposure = float(opt.get("exposure", -0.1))
scene.render.film_transparent = False
scene.render.image_settings.file_format = "PNG"

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "scene.blend"))

if "under" in opt:   # teşhis: alttan bakış
    for fc in (cam.animation_data.action.fcurves, target.animation_data.action.fcurves):
        pass
    cam.animation_data_clear(); target.animation_data_clear(); cam_data.animation_data_clear()
    cam.location = Vector(tuple(float(v) for v in opt.get("ucam", "8,-30,-26").split(","))); target.location = Vector(tuple(float(v) for v in opt.get("utgt", "8,0,-4").split(","))); cam_data.lens = float(opt.get("ulens", 35)); cam_data.dof.use_dof = False

if "still" in opt:
    for name in opt["still"].split(","):
        scene.frame_set(STATIONS[name] if name in STATIONS else int(name))
        scene.render.filepath = os.path.join(HERE, "test", f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print("RENDERED", name)

if "anim" in opt:
    # Final: her kare ayrı PNG. Var olan kareler atlanır (yarıda kalırsa aynı komut kaldığı
    # yerden sürer); yer tutucu dosya kullanılmaz, yarım kare diske hiç yazılmaz.
    scene.render.filepath = os.path.join(HERE, "frames", "f_####")
    scene.render.use_overwrite = False
    scene.render.use_placeholder = False
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 15
    bpy.ops.render.render(animation=True)
    print("ANIM DONE")
