"""Build the 3D scene in Blender (run by the CLI, not by hand).

  blender -b --factory-startup -P homeruns/scene.py -- <work>/config.json

Terrain from the elevation grid with the street texture, one tube per run growing on a shared clock
(each at its own pace), a face card at the head of each run, an auto-framing camera and a heat-map
compositor (overlapping runs add up). Saves <work>/scene.blend.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timewarp  # noqa: E402

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
WORK = cfg["work"]
RES = tuple(cfg["res"])
FPS, INTRO, RUN_FRAMES, OUTRO = cfg["fps"], cfg["intro"], cfg["run_frames"], cfg["outro"]
TIME_K, RAMP = cfg["time_k"], cfg["ramp"]
SNAP_START = cfg["snap_start"]
S_ = cfg["scale"]                     # world-size factor: 1.0 for a ~12 km area
TUBE_R = 0.0045 * S_                  # km
FACE_H = 0.11 * S_                    # face card height, km
D_MIN = 0.9 * S_                      # opening camera distance, km
FACE_EMIT = 1.3
LINE_V = cfg["line_strength"]         # emission of one route before the heat ramp
HEAT_GAIN, HEAT_POW, HEAT_SCALE = 1.8, 1.0, 1.0
BLUR_PX = 2.25 * min(RES) / 1080      # heat kernel radius (tuned at 1080 px wide)
DENS_W, SHARP_W, BLOOM = 2.0, 4.5, 0.25

data = json.load(open(os.path.join(WORK, "routes.json")))
WKM, HKM, EXAG = data["WKM"], data["HKM"], data["EXAG"]
routes = data["routes"]
tmax = max(r["duration"] for r in routes)


def run_time(f):
    return timewarp.run_time(f, INTRO, RUN_FRAMES, TIME_K, tmax, RAMP)


def frame_at(t):
    return timewarp.frame_at(t, INTRO, RUN_FRAMES, TIME_K, tmax, RAMP)


bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
sc = bpy.context.scene
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
sc.frame_start = 1
sc.frame_end = int(math.ceil(timewarp.end_frame_of_clock(INTRO, RUN_FRAMES, RAMP))) + OUTRO
sc.render.fps = FPS
sc.render.engine = "BLENDER_EEVEE"
sc.render.resolution_x, sc.render.resolution_y = RES
sc.eevee.taa_render_samples = 32
try:
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Punchy"
except TypeError:
    pass

# ---------- world ----------
world = bpy.data.worlds.new("Night")
sc.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.006, 0.008, 0.014, 1)

# ---------- terrain ----------
dem = np.load(os.path.join(WORK, "basemap", "dem.npy"))
dem = dem[::2, ::2] if max(dem.shape) > 1200 else dem
R, C = dem.shape
X, Y = np.meshgrid(np.linspace(-WKM / 2, WKM / 2, C), np.linspace(HKM / 2, -HKM / 2, R))
Z = np.where(dem <= 0.5, -0.002, np.maximum(dem, 0) / 1000 * EXAG) if cfg["sea"] else np.maximum(dem, 0) / 1000 * EXAG
verts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)
r, c = np.meshgrid(np.arange(R - 1), np.arange(C - 1), indexing="ij")
i00 = (r * C + c).ravel()
quads = np.column_stack([i00, i00 + C, i00 + C + 1, i00 + 1]).astype(np.int32)
me = bpy.data.meshes.new("Terrain")
me.vertices.add(len(verts)); me.vertices.foreach_set("co", verts.ravel())
me.loops.add(quads.size); me.loops.foreach_set("vertex_index", quads.ravel())
me.polygons.add(len(quads)); me.polygons.foreach_set("loop_start", np.arange(0, quads.size, 4, dtype=np.int32))
uv = me.uv_layers.new(name="UVMap")
uvs = np.column_stack([(verts[:, 0] + WKM / 2) / WKM, (verts[:, 1] + HKM / 2) / HKM])
uv.data.foreach_set("uv", uvs[quads.ravel()].ravel().astype(np.float32))
me.update(); me.validate()
me.polygons.foreach_set("use_smooth", np.ones(len(quads), bool))
terrain = bpy.data.objects.new("Terrain", me)

m = bpy.data.materials.new("TerrainMat"); m.use_nodes = True
nt = m.node_tree; N = nt.nodes; L = nt.links
bsdf = N["Principled BSDF"]
tex = N.new("ShaderNodeTexImage")
tex.image = bpy.data.images.load(os.path.join(WORK, "streets.png"))
tex.image.colorspace_settings.name = "Non-Color"
tex.interpolation = "Linear"   # "Cubic" is a B-spline in Eevee and blurs the streets
sep = N.new("ShaderNodeSeparateColor"); L.new(tex.outputs["Color"], sep.inputs["Color"])
land = N.new("ShaderNodeMix"); land.data_type = "RGBA"
land.inputs["A"].default_value = (0.020, 0.022, 0.030, 1)
land.inputs["B"].default_value = (0.020, 0.040, 0.030, 1)   # parks, faint green
L.new(sep.outputs["Green"], land.inputs["Factor"])
sea = N.new("ShaderNodeMix"); sea.data_type = "RGBA"
L.new(land.outputs["Result"], sea.inputs["A"])
sea.inputs["B"].default_value = (0.004, 0.008, 0.018, 1)
L.new(sep.outputs["Blue"], sea.inputs["Factor"])
rough = N.new("ShaderNodeMapRange")
rough.inputs["To Min"].default_value, rough.inputs["To Max"].default_value = 0.85, 0.25
L.new(sep.outputs["Blue"], rough.inputs["Value"]); L.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
st = N.new("ShaderNodeMath"); st.operation = "MULTIPLY"        # streets glow faintly, not on water
L.new(sep.outputs["Red"], st.inputs[0])
inv = N.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.inputs[0].default_value = 1
L.new(sep.outputs["Blue"], inv.inputs[1]); L.new(inv.outputs[0], st.inputs[1])
# fade the map out towards its edges so there is no hard border against the night
tc = N.new("ShaderNodeTexCoord"); sx = N.new("ShaderNodeSeparateXYZ")
L.new(tc.outputs["UV"], sx.inputs[0])
fade = None
for axis in ("X", "Y"):
    a = N.new("ShaderNodeMath"); a.operation = "MULTIPLY_ADD"; a.inputs[1].default_value = 2; a.inputs[2].default_value = -1
    L.new(sx.outputs[axis], a.inputs[0])
    b = N.new("ShaderNodeMath"); b.operation = "ABSOLUTE"; L.new(a.outputs[0], b.inputs[0])
    p = N.new("ShaderNodeMath"); p.operation = "POWER"; p.inputs[1].default_value = 5; L.new(b.outputs[0], p.inputs[0])
    q = N.new("ShaderNodeMath"); q.operation = "SUBTRACT"; q.inputs[0].default_value = 1; L.new(p.outputs[0], q.inputs[1])
    if fade is None:
        fade = q
    else:
        mm = N.new("ShaderNodeMath"); mm.operation = "MULTIPLY"
        L.new(fade.outputs[0], mm.inputs[0]); L.new(q.outputs[0], mm.inputs[1]); fade = mm
sq = N.new("ShaderNodeMath"); sq.operation = "POWER"; sq.inputs[1].default_value = 2.0
L.new(fade.outputs[0], sq.inputs[0])
es = N.new("ShaderNodeMath"); es.operation = "MULTIPLY"; es.inputs[1].default_value = 0.32
L.new(st.outputs[0], es.inputs[0])
es2 = N.new("ShaderNodeMath"); es2.operation = "MULTIPLY"
L.new(es.outputs[0], es2.inputs[0]); L.new(sq.outputs[0], es2.inputs[1])
L.new(es2.outputs[0], bsdf.inputs["Emission Strength"])
bc = N.new("ShaderNodeMix"); bc.data_type = "RGBA"; bc.inputs["A"].default_value = (0, 0, 0, 1)
L.new(sq.outputs[0], bc.inputs["Factor"]); L.new(sea.outputs["Result"], bc.inputs["B"])
L.new(bc.outputs["Result"], bsdf.inputs["Base Color"])
bsdf.inputs["Emission Color"].default_value = (0.08, 0.13, 0.26, 1)
terrain.data.materials.append(m)

sun = bpy.data.lights.new("Moon", "SUN"); sun.energy = 3.5; sun.color = (0.75, 0.82, 1.0)
sun.angle = math.radians(2)
moon = bpy.data.objects.new("Moon", sun)
moon.rotation_euler = (math.radians(62), 0, math.radians(-125))  # low light from the northwest

# ---------- routes ----------
# Routes live in their own view layer, drawn additively in white at low strength so overlapping
# runs sum up; the compositor maps that sum onto a heat colour ramp.
base_col = bpy.data.collections.new("Base"); sc.collection.children.link(base_col)
heat_col = bpy.data.collections.new("Heat"); sc.collection.children.link(heat_col)
base_col.objects.link(terrain); base_col.objects.link(moon)

rm = bpy.data.materials.new("RouteMat"); rm.use_nodes = True
rm.surface_render_method = "BLENDED"; rm.use_backface_culling = True
nt = rm.node_tree; N = nt.nodes; L = nt.links
for n in list(N):
    N.remove(n)
out = N.new("ShaderNodeOutputMaterial")
em = N.new("ShaderNodeEmission"); em.inputs["Color"].default_value = (1, 1, 1, 1)
em.inputs["Strength"].default_value = LINE_V
tp = N.new("ShaderNodeBsdfTransparent")
add = N.new("ShaderNodeAddShader")
L.new(tp.outputs[0], add.inputs[0]); L.new(em.outputs[0], add.inputs[1]); L.new(add.outputs[0], out.inputs["Surface"])

# Head marker: a camera-facing card with the face sprite, self-lit, in the Base layer (no heat tint).
face_img = bpy.data.images.load(os.path.join(WORK, "face.png"))
hm = bpy.data.materials.new("HeadMat"); hm.use_nodes = True
hm.surface_render_method = "BLENDED"
nt = hm.node_tree; N = nt.nodes
for n in list(N):
    N.remove(n)
o2 = N.new("ShaderNodeOutputMaterial"); e2 = N.new("ShaderNodeEmission")
ti = N.new("ShaderNodeTexImage"); ti.image = face_img; ti.extension = "CLIP"
e2.inputs["Strength"].default_value = FACE_EMIT
nt.links.new(ti.outputs["Color"], e2.inputs["Color"])
tp2 = N.new("ShaderNodeBsdfTransparent"); mx = N.new("ShaderNodeMixShader")
nt.links.new(ti.outputs["Alpha"], mx.inputs["Fac"])
nt.links.new(tp2.outputs[0], mx.inputs[1]); nt.links.new(e2.outputs[0], mx.inputs[2])
nt.links.new(mx.outputs[0], o2.inputs["Surface"])

card = bpy.data.meshes.new("FaceCard")
fw = FACE_H * face_img.size[0] / face_img.size[1]
y0, y1 = -0.15 * FACE_H, 0.85 * FACE_H   # chin just below the line, face sitting on top of it
card.from_pydata([(-fw / 2, y0, 0), (fw / 2, y0, 0), (fw / 2, y1, 0), (-fw / 2, y1, 0)], [], [(0, 1, 2, 3)])
uvl = card.uv_layers.new(name="UVMap")
for li, uvc in zip(range(4), [(0, 0), (1, 0), (1, 1), (0, 1)]):
    uvl.data[li].uv = uvc
card.materials.append(hm)

# All runs leave from one shared point (the mean start), so the video opens on a single face.
HOME_PT = np.mean([r["pts"][0] for r in routes], axis=0).astype(np.float32)
if SNAP_START:
    for r in routes:
        r["pts"][0] = HOME_PT.tolist()

heads = []
for rt in routes:
    pts = np.array(rt["pts"], np.float32)
    cu = bpy.data.curves.new(f"run_{rt['id']}", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_mode = "ROUND"; cu.bevel_depth = TUBE_R; cu.bevel_resolution = 2
    cu.use_fill_caps = True
    # Grow the tube by path length, keyed on the same frames as the head, so the line always ends
    # at the face. (Blender drops duplicate points, so mapping by segment count would drift.)
    cu.bevel_factor_mapping_end = "SPLINE"
    keep = np.concatenate([[True], np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-7])
    sp = cu.splines.new("POLY"); sp.points.add(int(keep.sum()) - 1)
    sp.points.foreach_set("co", np.column_stack([pts[keep], np.ones(int(keep.sum()), np.float32)]).ravel())
    ob = bpy.data.objects.new(f"run_{rt['id']}", cu); heat_col.objects.link(ob)
    ob.data.materials.append(rm)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg)]); total = max(cum[-1], 1e-9)
    end_f = frame_at(rt["duration"])
    hd = bpy.data.objects.new(f"head_{rt['id']}", card); base_col.objects.link(hd)
    heads.append(hd)
    n = len(pts)
    for f in list(range(INTRO, int(end_f) + 1, 2)) + [end_f]:
        u = min(run_time(f) / rt["step"], n - 1.0001)
        k = int(u); a = u - k
        hd.location = Vector(pts[k] * (1 - a) + pts[k + 1] * a)
        hd.keyframe_insert("location", frame=f)
        cu.bevel_factor_end = float((cum[k] + a * seg[k]) / total)
        cu.keyframe_insert("bevel_factor_end", frame=f)
    cu.bevel_factor_end = 1.0; cu.keyframe_insert("bevel_factor_end", frame=end_f)
    hd.scale = (1, 1, 1); hd.keyframe_insert("scale", frame=end_f)
    hd.scale = (0, 0, 0); hd.keyframe_insert("scale", frame=end_f + 6)
    hd.scale = (0, 0, 0); hd.keyframe_insert("scale", frame=1)
    hd.scale = (0, 0, 0); hd.keyframe_insert("scale", frame=8)
    hd.scale = (1, 1, 1); hd.keyframe_insert("scale", frame=16)

# ---------- camera ----------
pivot = bpy.data.objects.new("Pivot", None); sc.collection.objects.link(pivot)
cam_d = bpy.data.cameras.new("Cam"); cam_d.lens = 35
cam = bpy.data.objects.new("Cam", cam_d); sc.collection.objects.link(cam); sc.camera = cam
cam.parent = pivot
tr = cam.constraints.new("TRACK_TO"); tr.target = pivot; tr.track_axis = "TRACK_NEGATIVE_Z"; tr.up_axis = "UP_Y"

# The camera follows the drawn network: at each frame, the closest distance at which every route
# point drawn so far (and the face cards) fits in the clear band between the title and the clock.
cam_d.sensor_fit = "HORIZONTAL"
W_, H_ = RES
TX = 18 / cam_d.lens                      # tan(hfov/2), sensor 36 mm across the width
TY = TX * H_ / W_
X_LIM = 0.92                              # NDC half-width allowed (4% side margins)
Y_LO, Y_HI = 2 * 0.29 - 1, 2 * 0.76 - 1   # NDC band between the clock block and the title block
SUB = 3
PP, PT = [], []
for r in routes:
    p = np.array(r["pts"], np.float32)[::SUB]
    PP.append(p); PT.append(np.arange(len(p)) * SUB * r["step"])
PP = np.concatenate(PP); PT = np.concatenate(PT)
PP_top = PP + np.array([0, 0, 0.85 * FACE_H], np.float32)


def fits(D, piv, theta, elev, Q):
    ct, st_ = math.cos(theta), math.sin(theta)
    C_ = piv + np.array([st_ * D * math.cos(elev), -ct * D * math.cos(elev), D * math.sin(elev)])
    fwd = (piv - C_) / np.linalg.norm(piv - C_)
    right = np.cross(fwd, [0, 0, 1]); right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    v = Q - C_
    z = v @ fwd
    xs = (v @ right) / z / TX; ys = (v @ up) / z / TY
    return np.all(z > 0) and np.all(np.abs(xs) <= X_LIM) and np.all((ys >= Y_LO) & (ys <= Y_HI))


def elev_at(f):
    u = min(max((f - INTRO) / RUN_FRAMES, 0), 1) ** 0.5
    return math.radians(52 + (66 - 52) * u)


def theta_at(f):
    return math.radians(18 * min(max((f - INTRO) / RUN_FRAMES, 0), 1))


STEP_F = 5
fr = list(range(1, sc.frame_end + 1, STEP_F))
piv_req, d_req = [], []
for f in fr:
    sel = PT <= run_time(f)
    Q = np.concatenate([PP[sel], PP_top[sel], [HOME_PT, HOME_PT + [0, 0, 0.85 * FACE_H]]])
    lo, hi = Q[:, :2].min(0), Q[:, :2].max(0)
    piv = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0.05])
    a, b = D_MIN, 60.0 * S_
    if not fits(a, piv, theta_at(f), elev_at(f), Q):
        for _ in range(40):
            mid = (a + b) / 2
            a, b = (a, mid) if fits(mid, piv, theta_at(f), elev_at(f), Q) else (mid, b)
        a = b
    piv_req.append(piv); d_req.append(a)
d_req = np.maximum.accumulate(np.array(d_req))
piv_req = np.array(piv_req)
# look ~1 s ahead so the camera is already wide when a route arrives, then smooth
LA = max(1, 30 // STEP_F)
d_ahead = np.concatenate([d_req[LA:], np.repeat(d_req[-1], LA)])
k = 7
pad = np.concatenate([np.repeat(d_ahead[0], k), d_ahead, np.repeat(d_ahead[-1], k)])
d_s = np.convolve(pad, np.ones(2 * k + 1) / (2 * k + 1), mode="valid")
d_s = np.maximum(d_s, d_req) * 1.04
# opening: hold close on the single face, then ease out (geometric) into the fitted curve
fr_a = np.array(fr)
E0, E1 = INTRO, INTRO + 80
j1 = min(int(np.searchsorted(fr_a, E1)), len(fr_a) - 1)
for j, f in enumerate(fr_a):
    d0 = max(D_MIN, d_req[0] * 1.04)   # without a shared start, the opening must fit every start marker
    if f <= E0:
        d_s[j] = d0
    elif f < E1:
        u = (f - E0) / (E1 - E0); u = u * u * (3 - 2 * u)
        d_s[j] = max(d0 * (d_s[j1] / d0) ** u, d_ahead[j] * 1.04)
padp = np.concatenate([np.repeat(piv_req[:1], k, 0), piv_req, np.repeat(piv_req[-1:], k, 0)])
piv_s = np.column_stack([np.convolve(padp[:, i], np.ones(2 * k + 1) / (2 * k + 1), mode="valid") for i in range(3)])
for f, D, pv in zip(fr, d_s, piv_s):
    e, th = elev_at(f), theta_at(f)
    # during the opening hold, aim at the middle of the face card, then ease back to ground level
    u = min(max((f - E0) / (E1 - E0), 0), 1); u = u * u * (3 - 2 * u)
    pv = np.array(pv); pv[2] += 0.4 * FACE_H * (1 - u)
    pivot.location = Vector(pv); pivot.keyframe_insert("location", frame=f)
    pivot.rotation_euler = (0, 0, th); pivot.keyframe_insert("rotation_euler", frame=f)
    cam.location = (0, -D * math.cos(e), D * math.sin(e)); cam.keyframe_insert("location", frame=f)
print(f"camera distance km: start {d_s[0]:.2f}, end {d_s[-1]:.2f}")

for hd in heads:   # face cards turn to the camera, staying upright
    c_ = hd.constraints.new("TRACK_TO"); c_.target = cam; c_.track_axis = "TRACK_Z"; c_.up_axis = "UP_Y"

# ---------- compositor: heat ramp + bloom ----------
vl_base = sc.view_layers[0]; vl_base.name = "Base"
vl_heat = sc.view_layers.new("Heat"); vl_heat.use_sky = False
vl_base.layer_collection.children["Heat"].exclude = True
vl_heat.layer_collection.children["Base"].holdout = True  # terrain still hides routes behind hills

ng = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
nn, ln = ng.nodes, ng.links
rb = nn.new("CompositorNodeRLayers"); rb.layer = "Base"
rh = nn.new("CompositorNodeRLayers"); rh.layer = "Heat"
bw = nn.new("CompositorNodeRGBToBW"); ln.new(rh.outputs["Image"], bw.inputs["Image"])
# density: a small blur lets near-parallel GPS traces sum, like a heat-map kernel
blur = nn.new("CompositorNodeBlur"); blur.inputs["Size"].default_value = (BLUR_PX, BLUR_PX)
ln.new(rh.outputs["Image"], blur.inputs["Image"])
bw2 = nn.new("CompositorNodeRGBToBW"); ln.new(blur.outputs["Image"], bw2.inputs["Image"])
dens = nn.new("ShaderNodeMath"); dens.operation = "MULTIPLY_ADD"
ln.new(bw2.outputs[0], dens.inputs[0]); dens.inputs[1].default_value = DENS_W
sharp = nn.new("ShaderNodeMath"); sharp.operation = "MULTIPLY"
ln.new(bw.outputs[0], sharp.inputs[0]); sharp.inputs[1].default_value = SHARP_W
ln.new(sharp.outputs[0], dens.inputs[2])
pw = nn.new("ShaderNodeMath"); pw.operation = "POWER"; pw.inputs[1].default_value = HEAT_POW
ln.new(dens.outputs[0], pw.inputs[0])
sc_ = nn.new("ShaderNodeMath"); sc_.operation = "MULTIPLY"; sc_.inputs[1].default_value = HEAT_SCALE
ln.new(pw.outputs[0], sc_.inputs[0])
ramp = nn.new("ShaderNodeValToRGB"); cr = ramp.color_ramp
cr.interpolation = "EASE"
stops = [(0.0, (0, 0, 0)), (0.035, (0.30, 0.012, 0.03)), (0.12, (0.85, 0.07, 0.02)),
         (0.30, (1.0, 0.30, 0.02)), (0.55, (1.0, 0.65, 0.10)), (0.80, (1.0, 0.92, 0.50)), (1.0, (1, 1, 1))]
cr.elements[0].position, cr.elements[0].color = stops[0][0], (*stops[0][1], 1)
cr.elements[1].position, cr.elements[1].color = stops[-1][0], (*stops[-1][1], 1)
for p_, c3 in stops[1:-1]:
    e_ = cr.elements.new(p_); e_.color = (*c3, 1)
ln.new(sc_.outputs[0], ramp.inputs["Factor"])
gain = nn.new("ShaderNodeMix"); gain.data_type = "RGBA"; gain.blend_type = "MULTIPLY"
gain.inputs["Factor"].default_value = 1.0; gain.inputs[7].default_value = (HEAT_GAIN,) * 3 + (1,)
ln.new(ramp.outputs["Color"], gain.inputs[6])
mixa = nn.new("ShaderNodeMix"); mixa.data_type = "RGBA"; mixa.blend_type = "ADD"
mixa.inputs["Factor"].default_value = 1.0
ln.new(rb.outputs["Image"], mixa.inputs[6]); ln.new(gain.outputs[2], mixa.inputs[7])
gl = nn.new("CompositorNodeGlare")
gl.inputs["Type"].default_value = "Bloom"
gl.inputs["Threshold"].default_value = 0.8
gl.inputs["Strength"].default_value = BLOOM
gl.inputs["Size"].default_value = 0.6
go = nn.new("NodeGroupOutput")
ln.new(mixa.outputs[2], gl.inputs["Image"]); ln.new(gl.outputs["Image"], go.inputs[0])
sc.compositing_node_group = ng
sc.render.use_compositing = True

sc.render.image_settings.file_format = "PNG"
sc.render.filepath = os.path.join(WORK, "frames", "f_")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(WORK, "scene.blend"))
print("frames", sc.frame_end)
