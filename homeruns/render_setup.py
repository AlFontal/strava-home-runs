"""Render-time settings applied to scene.blend before rendering (not saved into it).

  blender -b <work>/scene.blend -P homeruns/render_setup.py -a -- <work>/config.json

16x anisotropic filtering keeps the tilted street map crisp (a preference, so not stored in the
.blend). With "preview": half resolution, every 2nd frame, fewer samples, heat blur halved to match.
"""
import json
import os
import sys

import bpy

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
bpy.context.preferences.system.anisotropic_filter = "FILTER_16"
sc = bpy.context.scene
if cfg.get("preview"):
    sc.render.resolution_percentage = 50
    sc.frame_step = 2
    sc.eevee.taa_render_samples = 12
    for n in sc.compositing_node_group.nodes:
        if n.bl_idname == "CompositorNodeBlur":
            s = n.inputs["Size"].default_value
            n.inputs["Size"].default_value = (s[0] / 2, s[1] / 2)
sc.render.filepath = os.path.join(cfg["frames"], "f_")
