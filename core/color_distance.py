"""CIEDE2000 color distance for hidden-text detection.

Converts sRGB pixel values to CIELAB and computes CIEDE2000 (ΔE₀₀)
color difference.  Used alongside WCAG contrast ratio to separate
hidden text (low ΔE — both luminance and chroma match background)
from legitimate colored text (low CR but high ΔE — e.g. hyperref
green links on white).

Reference implementation follows:
  Sharma, Wu, Dalal (2005) "The CIEDE2000 color-difference formula:
  Implementation notes, supplementary test data, and mathematical
  observations." Color Research & Application 30(1), 21-30.
"""

from __future__ import annotations

import math

import numpy as np


# ---------------------------------------------------------------------------
# sRGB → linear RGB → CIE XYZ → CIELAB
# ---------------------------------------------------------------------------

# D65 illuminant reference white (CIE 1931 2° observer)
_XN, _YN, _ZN = 0.95047, 1.00000, 1.08883


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """Linearize sRGB channel values (0-255 → linear 0-1)."""
    c = c.astype(np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def srgb_to_xyz(rgb: np.ndarray) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIE XYZ (D65).

    Parameters
    ----------
    rgb : array-like of shape (3,) — [R, G, B] in 0-255.

    Returns
    -------
    (X, Y, Z) tuple.
    """
    lin = _srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    r, g, b = float(lin[0]), float(lin[1]), float(lin[2])
    # sRGB → XYZ matrix (IEC 61966-2-1, D65)
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    return x, y, z


def _lab_f(t: float) -> float:
    """CIELAB nonlinear mapping function."""
    delta = 6.0 / 29.0
    if t > delta ** 3:
        return t ** (1.0 / 3.0)
    return t / (3.0 * delta * delta) + 4.0 / 29.0


def xyz_to_lab(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert CIE XYZ to CIELAB (D65 illuminant)."""
    fx = _lab_f(x / _XN)
    fy = _lab_f(y / _YN)
    fz = _lab_f(z / _ZN)
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return L, a, b


def srgb_to_lab(rgb: np.ndarray) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIELAB (D65)."""
    return xyz_to_lab(*srgb_to_xyz(rgb))


# ---------------------------------------------------------------------------
# CIEDE2000
# ---------------------------------------------------------------------------

def ciede2000(
    lab1: tuple[float, float, float],
    lab2: tuple[float, float, float],
    kL: float = 1.0,
    kC: float = 1.0,
    kH: float = 1.0,
) -> float:
    """Compute CIEDE2000 color difference ΔE₀₀.

    Parameters
    ----------
    lab1, lab2 : (L*, a*, b*) tuples in CIELAB.
    kL, kC, kH : parametric weighting factors (default 1:1:1).

    Returns
    -------
    ΔE₀₀ : float
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    # Step 1: Calculate C'ab, h'ab
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    C_bar = (C1 + C2) / 2.0
    C_bar7 = C_bar ** 7
    G = 0.5 * (1.0 - math.sqrt(C_bar7 / (C_bar7 + 25.0 ** 7)))

    a1p = a1 * (1.0 + G)
    a2p = a2 * (1.0 + G)

    C1p = math.hypot(a1p, b1)
    C2p = math.hypot(a2p, b2)

    h1p = math.degrees(math.atan2(b1, a1p)) % 360.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360.0

    # Step 2: Calculate ΔL', ΔC', ΔH'
    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180.0:
        dhp = h2p - h1p
    elif h2p - h1p > 180.0:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0

    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))

    # Step 3: Calculate CIEDE2000 ΔE₀₀
    Lp_bar = (L1 + L2) / 2.0
    Cp_bar = (C1p + C2p) / 2.0

    if C1p * C2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        hp_bar = (h1p + h2p) / 2.0
    elif h1p + h2p < 360.0:
        hp_bar = (h1p + h2p + 360.0) / 2.0
    else:
        hp_bar = (h1p + h2p - 360.0) / 2.0

    T = (1.0
         - 0.17 * math.cos(math.radians(hp_bar - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * hp_bar))
         + 0.32 * math.cos(math.radians(3.0 * hp_bar + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * hp_bar - 63.0)))

    SL = 1.0 + 0.015 * (Lp_bar - 50.0) ** 2 / math.sqrt(20.0 + (Lp_bar - 50.0) ** 2)
    SC = 1.0 + 0.045 * Cp_bar
    SH = 1.0 + 0.015 * Cp_bar * T

    Cp_bar7 = Cp_bar ** 7
    RC = 2.0 * math.sqrt(Cp_bar7 / (Cp_bar7 + 25.0 ** 7))
    d_theta = 30.0 * math.exp(-((hp_bar - 275.0) / 25.0) ** 2)
    RT = -math.sin(math.radians(2.0 * d_theta)) * RC

    dE = math.sqrt(
        (dLp / (kL * SL)) ** 2
        + (dCp / (kC * SC)) ** 2
        + (dHp / (kH * SH)) ** 2
        + RT * (dCp / (kC * SC)) * (dHp / (kH * SH))
    )
    return dE


# ---------------------------------------------------------------------------
# Convenience: sRGB pair → ΔE₀₀
# ---------------------------------------------------------------------------

def cie76(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIE76 color difference (Euclidean distance in CIELAB)."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2)))


def srgb_delta_e(rgb1: np.ndarray, rgb2: np.ndarray) -> float:
    """Compute CIEDE2000 ΔE₀₀ between two sRGB colors (0-255 each)."""
    return ciede2000(srgb_to_lab(rgb1), srgb_to_lab(rgb2))


def srgb_cie76(rgb1: np.ndarray, rgb2: np.ndarray) -> float:
    """Compute CIE76 ΔE*ab between two sRGB colors (0-255 each)."""
    return cie76(srgb_to_lab(rgb1), srgb_to_lab(rgb2))


# ---------------------------------------------------------------------------
# Vectorised: compute ΔE₀₀ from render-diff crop pairs
# ---------------------------------------------------------------------------

def compute_delta_e(
    crop_w: np.ndarray,
    crop_wo: np.ndarray,
    mask: np.ndarray,
) -> float | None:
    """Compute median CIEDE2000 ΔE₀₀ between fg and bg pixels.

    Parameters
    ----------
    crop_w  : RGB image crop with text, shape (H, W, 3).
    crop_wo : RGB image crop without text, shape (H, W, 3).
    mask    : Boolean mask of glyph pixels, shape (H, W).

    Returns
    -------
    Median ΔE₀₀ across masked pixels, or None if mask is empty.
    """
    if not np.any(mask):
        return None

    fg_pixels = crop_w[mask]   # (N, 3)
    bg_pixels = crop_wo[mask]  # (N, 3)

    # Sample if too many pixels (ΔE₀₀ is expensive per-pixel)
    n = len(fg_pixels)
    if n > 200:
        rng = np.random.default_rng(42)
        idx = rng.choice(n, 200, replace=False)
        fg_pixels = fg_pixels[idx]
        bg_pixels = bg_pixels[idx]

    deltas = np.array([
        srgb_delta_e(fg, bg)
        for fg, bg in zip(fg_pixels, bg_pixels)
    ])
    return float(np.median(deltas))
