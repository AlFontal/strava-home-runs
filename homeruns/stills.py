"""Render the last frame of the scene (the finished heat map, no overlay) in several map styles
(run by the CLI, not by hand).

  blender -b <work>/scene.blend -P homeruns/stills.py -- <out_dir> <percent> <style> [<style> ...]

Writes <out_dir>/<style>.png at <percent> of the scene resolution. The styles are in styles.py and
restyle.py applies them. Nothing is saved back into the .blend.
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import restyle  # noqa: E402
import styles  # noqa: E402


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_dir, pct, names = argv[0], int(argv[1]), argv[2:]
    sc = bpy.context.scene
    bpy.context.preferences.system.anisotropic_filter = "FILTER_16"
    sc.frame_set(sc.frame_end)
    sc.render.resolution_percentage = pct
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    for n in sc.compositing_node_group.nodes:
        if n.bl_idname == "CompositorNodeBlur":   # the heat kernel is in pixels
            s = n.inputs["Size"].default_value
            n.inputs["Size"].default_value = (s[0] * pct / 100, s[1] * pct / 100)
    bloom_out = restyle.heat_output(sc)
    # styles change the scene in place, so the untouched "dark" one goes first
    for name in sorted(names, key=styles.NAMES.index):
        restyle.apply(sc, styles.SCENE[name], bloom_out)
        sc.render.filepath = os.path.join(out_dir, f"{name}.png")
        bpy.ops.render.render(write_still=True)


main()
