"""Runner marker sprite: a tinted face cut-out, or a glowing dot when no image is given."""
import numpy as np
from PIL import Image, ImageFilter

# duotone ramp: deep brown -> orange -> pale peach
DARK, MID, LIGHT = np.array([0.18, 0.05, 0.02]), np.array([0.99, 0.32, 0.01]), np.array([1.0, 0.86, 0.66])


def face_sprite(src, out, tint=0.55, size=256):
    """Crop a transparent PNG/WebP to its visible pixels and tint it orange (tint 0 keeps the colours)."""
    im = Image.open(src)
    im.seek(0)
    im = im.convert("RGBA")
    if im.getchannel("A").getextrema()[0] == 255:
        raise SystemExit(f"{src} has no transparent background. Cut the face out first "
                         "(e.g. 'Copy Subject' on macOS/iOS, or remove.bg) and save it as PNG.")
    im = im.crop(im.getchannel("A").getbbox())
    a = np.asarray(im).astype(np.float32) / 255
    rgb, alpha = a[..., :3], a[..., 3:]
    lum = (rgb @ np.array([0.299, 0.587, 0.114]))[..., None]
    duo = np.where(lum < 0.5, DARK + (MID - DARK) * (lum / 0.5), MID + (LIGHT - MID) * ((lum - 0.5) / 0.5))
    rgb = rgb * (1 - tint) + duo * tint
    img = Image.fromarray((np.concatenate([rgb, alpha], -1) * 255).astype(np.uint8))
    img.thumbnail((size, size), Image.LANCZOS)
    img.save(out)
    return img.size


def dot_sprite(out, size=128):
    """An orange disc with a soft rim, used when no face is given."""
    yy, xx = np.mgrid[:size, :size]
    r = np.hypot(xx - size / 2 + 0.5, yy - size / 2 + 0.5) / (size / 2)
    core = np.clip((0.55 - r) / 0.06, 0, 1)
    glow = np.clip(1 - r, 0, 1) ** 2 * 0.6
    alpha = np.maximum(core, glow)
    rgb = np.where(core[..., None] > 0.5, LIGHT, MID)
    img = Image.fromarray((np.dstack([rgb * 255 * np.ones((size, size, 1)), alpha * 255])).astype(np.uint8), "RGBA")
    img.filter(ImageFilter.SMOOTH).save(out)
    return img.size
