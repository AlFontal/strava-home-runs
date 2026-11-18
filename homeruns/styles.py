"""Map styles for the video and the still images (plain Python, so the CLI, the overlay and the Blender
scripts can all import it).

SCENE: how restyle.py recolours the 3D scene. "dark" is the scene as built by scene.py.
  marker: multiplies the colour of the plain dot marker (not a face), so it stands out on light maps.
  flat: (self-lit, lit) shares of the land colour. Light maps are mostly self-lit so the land matches the
        background, with some hill shading on top. heat: "add" glows on a dark map, "over" is ink on paper.
TEXT: overlay colours (RGB 0-255). The scrim behind the text fades the frame towards "scrim" by up to
  "scrim_k", so routes passing under the text don't hurt legibility.
"""

SCENE = {
    "dark": None,
    "blueprint": dict(view="AgX", bg="#06101F", amb="#06101F", sun=3.0, sun_col="#CFE0FF",
                      land="#0E1E36", park="#0E2633", water="#040A16", street=None,
                      glow="#3F6FA6", glow_k=0.45, flat=None, heat="add",
                      ramp=[(0.0, "#000000"), (0.035, "#0A2A6E"), (0.12, "#1260D8"), (0.30, "#2FA0FF"),
                            (0.55, "#8AD8FF"), (0.80, "#E0F6FF"), (1.0, "#FFFFFF")]),
    "paper": dict(view="Standard", bg="#EFEBE3", amb="#5E5C58", sun=4.0, sun_col="#FFF6EA",
                  land="#ECE7DE", park="#DCE3CC", water="#BFD5E1", street="#CDC4B4", street_k=0.9,
                  glow=None, flat=(0.75, 0.35), heat="over", alpha_k=9.0, marker="#FF6614",
                  ramp=[(0.0, "#F2935F"), (0.10, "#E2482B"), (0.30, "#B8132F"), (0.60, "#72092B"), (1.0, "#3C0418")]),
    "mono": dict(view="Standard", bg="#F7F7F5", amb="#626262", sun=4.0, sun_col="#FFFFFF",
                 land="#F4F4F2", park="#EAEDE6", water="#DCE2E6", street="#C2C2C2", street_k=0.85,
                 glow=None, flat=(0.75, 0.35), heat="over", alpha_k=11.0, marker="#FF6614",
                 ramp=[(0.0, "#5C5C5C"), (0.15, "#2B2B2B"), (0.5, "#0B0B0B"), (1.0, "#000000")]),
}

# title and clock, subtitle and labels, finished runs in the histogram, its ghost, axis and credit
TEXT = {
    "dark": dict(main=(232, 232, 238), sub=(160, 165, 184), hot=(255, 110, 30), dim=(35, 20, 17),
                 axis=(100, 104, 120), scrim=(0, 0, 0), scrim_k=0.72),
    "blueprint": dict(main=(232, 238, 248), sub=(150, 172, 204), hot=(80, 165, 255), dim=(16, 30, 54),
                      axis=(92, 112, 142), scrim=(6, 16, 31), scrim_k=0.72),
    "paper": dict(main=(46, 26, 24), sub=(112, 96, 88), hot=(184, 19, 47), dim=(232, 196, 186),
                  axis=(150, 138, 126), scrim=(239, 235, 227), scrim_k=0.8),
    "mono": dict(main=(20, 20, 20), sub=(100, 100, 100), hot=(30, 30, 30), dim=(214, 214, 214),
                 axis=(150, 150, 150), scrim=(247, 247, 245), scrim_k=0.8),
}

NAMES = tuple(SCENE)
