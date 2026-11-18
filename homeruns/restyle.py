"""Recolour the saved scene in one of the map styles in styles.py (imported by the Blender scripts
stills.py and render_setup.py; changes the scene in memory only).

The terrain material, the sky and light, and the heat ramp are rebuilt; "dark" leaves the scene as built.
Light styles paint the routes like ink over the map instead of letting them glow.
"""
import bpy


def col(v):
    """'#rrggbb' (sRGB) -> linear RGBA."""
    c = [int(v[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c) + (1.0,)


# node helpers: each input is either a constant or a socket to link from
def link_or_set(L, sock, v):
    if isinstance(v, (tuple, float, int)):
        sock.default_value = v
    else:
        L.new(v, sock)


def mixc(N, L, a, b, fac, clamp=True):
    m = N.new("ShaderNodeMix"); m.data_type = "RGBA"; m.clamp_factor = clamp
    for sock, v in ((m.inputs[6], a), (m.inputs[7], b), (m.inputs[0], fac)):
        link_or_set(L, sock, v)
    return m.outputs[2]


def op(N, L, kind, a, b, clamp=False):
    m = N.new("ShaderNodeMath"); m.operation = kind; m.use_clamp = clamp
    link_or_set(L, m.inputs[0], a); link_or_set(L, m.inputs[1], b)
    return m.outputs[0]


def style_terrain(st, img):
    nt = bpy.data.materials["TerrainMat"].node_tree; N, L = nt.nodes, nt.links
    for n in list(N):
        N.remove(n)
    out = N.new("ShaderNodeOutputMaterial"); bsdf = N.new("ShaderNodeBsdfPrincipled")
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    tex = N.new("ShaderNodeTexImage"); tex.image = img; tex.interpolation = "Linear"
    sep = N.new("ShaderNodeSeparateColor"); L.new(tex.outputs["Color"], sep.inputs["Color"])
    R, G, B = sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"]
    # edge fade, as in scene.py: 1 inside, 0 at the border
    tc = N.new("ShaderNodeTexCoord"); sx = N.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["UV"], sx.inputs[0])
    fade = None
    for axis in ("X", "Y"):
        a = op(N, L, "MULTIPLY_ADD", sx.outputs[axis], 2.0); a.node.inputs[2].default_value = -1
        q = op(N, L, "SUBTRACT", 1.0, op(N, L, "POWER", op(N, L, "ABSOLUTE", a, 0.0), 5.0))
        fade = q if fade is None else op(N, L, "MULTIPLY", fade, q)
    sq = op(N, L, "POWER", fade, 2.0)
    c = mixc(N, L, col(st["land"]), col(st["park"]), G)
    c = mixc(N, L, c, col(st["water"]), B)
    if st["street"]:   # drawn over water, so bridges stay
        c = mixc(N, L, c, col(st["street"]), op(N, L, "MULTIPLY", R, st["street_k"]))
    e_k, b_k = st["flat"] or (0.0, 1.0)
    L.new(mixc(N, L, (0, 0, 0, 1), c, op(N, L, "MULTIPLY", sq, b_k)), bsdf.inputs["Base Color"])
    # emission: glowing streets or the self-lit share of the land inside; the background colour at the
    # faded edge, so the map melts into it
    inner = mixc(N, L, (0, 0, 0, 1), c, e_k) if e_k else (0, 0, 0, 1)
    if st["glow"]:
        g = mixc(N, L, (0, 0, 0, 1), col(st["glow"]), op(N, L, "MULTIPLY", R, st["glow_k"]), clamp=False)
        if e_k:
            inner = mixc(N, L, inner, g, 1.0); inner.node.blend_type = "ADD"
        else:
            inner = g
    L.new(mixc(N, L, col(st["bg"]), inner, sq), bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 1.0
    rough = N.new("ShaderNodeMapRange")
    rough.inputs["To Min"].default_value, rough.inputs["To Max"].default_value = 0.85, 0.3
    L.new(B, rough.inputs["Value"]); L.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    L.new(op(N, L, "MULTIPLY", sq, 0.5), bsdf.inputs["Specular IOR Level"])


def style_world(st, sc):
    # the camera sees the background colour; the terrain is lit by a separate ambient colour
    nt = sc.world.node_tree; N, L = nt.nodes, nt.links
    for n in list(N):
        N.remove(n)
    out = N.new("ShaderNodeOutputWorld")
    cam_bg = N.new("ShaderNodeBackground"); cam_bg.inputs["Color"].default_value = col(st["bg"])
    amb = N.new("ShaderNodeBackground"); amb.inputs["Color"].default_value = col(st["amb"])
    lp = N.new("ShaderNodeLightPath"); mx = N.new("ShaderNodeMixShader")
    L.new(lp.outputs["Is Camera Ray"], mx.inputs["Fac"])
    L.new(amb.outputs[0], mx.inputs[1]); L.new(cam_bg.outputs[0], mx.inputs[2])
    L.new(mx.outputs[0], out.inputs["Surface"])
    sun = bpy.data.lights["Moon"]; sun.energy = st["sun"]; sun.color = col(st["sun_col"])[:3]


def style_heat(st, comp, out_node, bloom_out):
    N, L = comp.nodes, comp.links
    ramp = next(n for n in N if n.bl_idname == "ShaderNodeValToRGB")
    cr = ramp.color_ramp
    while len(cr.elements) > 1:
        cr.elements.remove(cr.elements[-1])
    cr.elements[0].position, cr.elements[0].color = st["ramp"][0][0], col(st["ramp"][0][1])
    for p, h in st["ramp"][1:]:
        cr.elements.new(p).color = col(h)
    if st["heat"] == "add":
        L.new(bloom_out, out_node.inputs[0])
        return
    dens = ramp.inputs["Factor"].links[0].from_socket
    rb = next(n for n in N if n.bl_idname == "CompositorNodeRLayers" and n.layer == "Base")
    alpha = op(N, L, "MULTIPLY", dens, st["alpha_k"], clamp=True)
    L.new(mixc(N, L, rb.outputs["Image"], ramp.outputs["Color"], alpha), out_node.inputs[0])   # no bloom


def style_markers(st, sc):
    """Multiply the plain dot marker's colour by st["marker"]: the pale glowing dot of the dark map
    disappears on a light one. A face sprite is left as it is: it reads well on light maps, and the tint
    turns the skin flat red."""
    nt = bpy.data.materials["HeadMat"].node_tree; N, L = nt.nodes, nt.links
    em = next(n for n in N if n.bl_idname == "ShaderNodeEmission")
    old = N.get("MarkerTint")   # stills.py restyles one scene several times: replace the tint, don't stack it
    if old:
        L.new(old.inputs[6].links[0].from_socket, em.inputs["Color"])
        N.remove(old)
    if not st.get("marker") or sc.get("face"):
        return
    tinted = mixc(N, L, em.inputs["Color"].links[0].from_socket, col(st["marker"]), 1.0)
    tinted.node.blend_type = "MULTIPLY"; tinted.node.name = "MarkerTint"
    L.new(tinted, em.inputs["Color"])


def heat_output(sc):
    """The bloom socket that feeds the compositor output, as scene.py built it. Take it before restyling:
    an "over" style relinks the output past it."""
    out_node = next(n for n in sc.compositing_node_group.nodes if n.bl_idname == "NodeGroupOutput")
    return out_node.inputs[0].links[0].from_socket


def apply(sc, st, bloom_out):
    """Restyle the scene with st, a styles.SCENE entry (None: leave it as built)."""
    if not st:
        return
    comp = sc.compositing_node_group
    out_node = next(n for n in comp.nodes if n.bl_idname == "NodeGroupOutput")
    img = next(n.image for n in bpy.data.materials["TerrainMat"].node_tree.nodes if n.bl_idname == "ShaderNodeTexImage")
    sc.view_settings.view_transform = st["view"]
    sc.view_settings.look = "AgX - Punchy" if st["view"] == "AgX" else "None"
    style_terrain(st, img)
    style_world(st, sc)
    style_heat(st, comp, out_node, bloom_out)
    style_markers(st, sc)
