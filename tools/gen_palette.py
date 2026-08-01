"""Generate M3-style tonal palettes seeded from the ODDITY LABS anchor.

Tones are M3's steps. Lightness is OKLab L (perceptually uniform), hue and chroma are
taken from the seed colour, so every ramp is the same colour at different lightnesses
rather than a hand-picked set.
"""
import math

def srgb_to_lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def lin_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055

def hex_to_oklch(h):
    h = h.lstrip('#')
    r, g, b = (srgb_to_lin(int(h[i:i+2], 16) / 255) for i in (0, 2, 4))
    l = (0.4122214708*r + 0.5363325363*g + 0.0514459929*b) ** (1/3)
    m = (0.2119034982*r + 0.6806995451*g + 0.1073969566*b) ** (1/3)
    s = (0.0883024619*r + 0.2817188376*g + 0.6299787005*b) ** (1/3)
    L = 0.2104542553*l + 0.7936177850*m - 0.0040720468*s
    a = 1.9779984951*l - 2.4285922050*m + 0.4505937099*s
    bb = 0.0259040371*l + 0.7827717662*m - 0.8086757660*s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360

def oklch_to_hex(L, C, H):
    a, bb = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l = (L + 0.3963377774*a + 0.2158037573*bb) ** 3
    m = (L - 0.1055613458*a - 0.0638541728*bb) ** 3
    s = (L - 0.0894841775*a - 1.2914855480*bb) ** 3
    r = +4.0767416621*l - 3.3077115913*m + 0.2309699292*s
    g = -1.2684380046*l + 2.6097574011*m - 0.3413193965*s
    b = -0.0041960863*l - 0.7034186147*m + 1.7076147010*s
    out = ''
    for v in (r, g, b):
        out += '%02x' % max(0, min(255, round(lin_to_srgb(max(0.0, min(1.0, v))) * 255)))
    return '#' + out

# M3 tone -> OKLab L. Tone is nominally L* in CIELAB; these are the OKLab equivalents.
TONE_L = {0: 0.0, 4: 0.055, 6: 0.075, 10: 0.126, 12: 0.145, 17: 0.196, 20: 0.226,
          22: 0.245, 24: 0.263, 30: 0.320, 40: 0.418, 50: 0.516, 60: 0.615,
          70: 0.715, 80: 0.815, 87: 0.884, 90: 0.917, 92: 0.937, 94: 0.957,
          95: 0.967, 96: 0.977, 98: 0.992, 99: 0.997, 100: 1.0}

SEED = '#080331'      # --base-color-brand--blue-dark
ACCENT = '#d3da54'    # --base-color-brand--bright-yellow-green
FOCUS = '#2d62ff'     # --base-color-system--focus-state

sL, sC, sH = hex_to_oklch(SEED)
aL, aC, aH = hex_to_oklch(ACCENT)
fL, fC, fH = hex_to_oklch(FOCUS)
print(f'/* seed  {SEED}  oklch({sL:.3f} {sC:.3f} {sH:.1f}) */')
print(f'/* accent {ACCENT} oklch({aL:.3f} {aC:.3f} {aH:.1f}) */')
print(f'/* focus  {FOCUS}  oklch({fL:.3f} {fC:.3f} {fH:.1f}) */')

# Chroma per M3 key colour, held constant across the ramp and clipped at the light end
# where high chroma would leave gamut.
RAMPS = [
    ('p',  sH, 0.130),   # primary, the anchor hue
    ('s',  sH, 0.045),   # secondary, same hue desaturated
    ('t',  aH, 0.105),   # tertiary, the LABS yellow-green
    ('n',  sH, 0.008),   # neutral, barely tinted toward the anchor
    ('nv', sH, 0.022),   # neutral variant
    ('e',  25.0, 0.150), # error
]

for name, H, C in RAMPS:
    print()
    for tone in sorted(TONE_L):
        L = TONE_L[tone]
        # Chroma falls away at both ends; pure black and pure white carry none.
        k = min(1.0, 2.2 * min(L, 1 - L) + 0.08)
        print(f'  --{name}{tone}: {oklch_to_hex(L, C * k, H)};')
