"""Generate the explanatory figures used in the README.

Run from the project root:  python docs/make_figures.py
"""
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "cubesat_twin"))

from eclipse import EARTH_RADIUS_KM, sun_direction, sunlit_cylindrical  # noqa: E402
from orbit import propagate, read_tle  # noqa: E402

OUT = ROOT / "docs"
R = EARTH_RADIUS_KM
SUN, SHADE, EARTH, INK = "#d08a12", "#33415c", "#7fa7c9", "#1d2633"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": INK})


def trim_white(path: Path, margin: int = 30) -> None:
    """Crop the empty space that 3D axes leave around the drawing."""
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    left, top, right, bottom = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    im.crop((max(left - margin, 0), max(top - margin, 0),
             min(right + margin, im.width), min(bottom + margin, im.height))).save(path)


def sun_frame(s: np.ndarray, n: np.ndarray) -> np.ndarray:
    """Rotation matrix whose rows are a frame with the Sun along +x and the
    orbit normal in the x-z plane, so the shadow cylinder runs along -x."""
    x = s / np.linalg.norm(s)
    y = np.cross(n, x)
    y /= np.linalg.norm(y)
    z = np.cross(x, y)
    return np.vstack([x, y, z])


def eclipse_geometry() -> float:
    name, l1, l2 = read_tle(str(ROOT / "data/tle/funcube1.tle"))
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 100 * 60, 20)
    r = df[["x_km", "y_km", "z_km"]].to_numpy()
    s = sun_direction(df.time)[0]
    n = np.cross(r[0], r[1] - r[0])
    n /= np.linalg.norm(n)
    beta = np.degrees(np.arcsin(n @ s))
    lit = sunlit_cylindrical(df)

    m = sun_frame(s, n)
    p = r @ m.T / R                              # positions in Earth radii, Sun frame
    nn = m @ n

    fig = plt.figure(figsize=(7.5, 6), dpi=160)
    ax = fig.add_subplot(projection="3d", computed_zorder=False)
    ax.set_proj_type("persp", focal_length=0.35)

    # Shadow cylinder behind the Earth (x < 0), radius 1 Earth radius.
    th, xx = np.meshgrid(np.linspace(0, 2 * np.pi, 60), np.linspace(-3.2, 0, 2))
    ax.plot_surface(xx, np.cos(th), np.sin(th), color=SHADE, alpha=0.28, linewidth=0, zorder=1)

    # Earth: day side lighter than the night side.
    u, v = np.meshgrid(np.linspace(0, 2 * np.pi, 80), np.linspace(0, np.pi, 40))
    ex, ey, ez = np.cos(u) * np.sin(v), np.sin(u) * np.sin(v), np.cos(v)
    shade = np.clip(ex, 0, 1)[..., None]
    rgb = np.array(matplotlib.colors.to_rgb(EARTH))
    colors = np.concatenate([(0.35 + 0.65 * shade) * rgb, np.ones_like(shade)], axis=-1)
    ax.plot_surface(ex, ey, ez, facecolors=colors, linewidth=0, shade=False, zorder=2)

    # Orbit, coloured by sunlight. Matplotlib does not hide lines behind
    # surfaces, so the part of the orbit behind the Earth (as seen from the
    # camera) is drawn below the Earth's layer.
    elev, azim = np.radians(25), np.radians(-25)
    view = np.array([np.cos(elev) * np.cos(azim), np.cos(elev) * np.sin(azim), np.sin(elev)])
    along = p @ view
    hidden = (along < 0) & (np.linalg.norm(p - along[:, None] * view, axis=1) < 1)
    state = lit.astype(int) * 2 + hidden.astype(int)
    edges = np.flatnonzero(np.diff(state)) + 1
    for seg in np.split(np.arange(len(p)), edges):
        seg = np.append(seg, min(seg[-1] + 1, len(p) - 1))
        c = SUN if lit[seg[0]] else INK
        ax.plot(*p[seg].T, color=c, lw=2.6 if lit[seg[0]] else 3.2,
                zorder=1.5 if hidden[seg[0]] else 4)

    # Sun direction and orbit normal.
    ax.quiver(1.25, 0, 0, 1.45, 0, 0, color=SUN, lw=2, arrow_length_ratio=0.15, zorder=5)
    ax.text(2.2, 0, 0.25, "to the Sun", color=SUN, zorder=6)
    ax.quiver(0, 0, 0, *(1.9 * nn), color=INK, lw=1.4, arrow_length_ratio=0.12, zorder=5)
    ax.text(*(2.05 * nn), "orbit normal", color=INK, zorder=6)
    # Beta angle: between the Sun direction and the orbit plane (90° minus the
    # angle to the normal), drawn as an arc from the Sun line towards the plane.
    k = np.cross([1, 0, 0], nn)
    in_plane = np.cross(nn, k)
    in_plane /= np.linalg.norm(in_plane)
    a = np.linspace(0, 1, 30)[:, None]
    arc = (1 - a) * np.array([1, 0, 0]) + a * in_plane
    arc = 1.55 * arc / np.linalg.norm(arc, axis=1, keepdims=True)
    ax.plot(*arc.T, color=INK, lw=1, zorder=5)
    ax.plot(*np.vstack([[0, 0, 0], 1.7 * in_plane]).T, color=INK, lw=0.8, ls=":", zorder=5)
    mid = arc[len(arc) // 2] * 1.12
    ax.text(*mid, f"β = {beta:.0f}°", color=INK, zorder=6)
    ax.text(-2.3, 0, 1.25, "Earth's shadow\n(cylinder model)", color=SHADE, zorder=6)

    lim = 1.75
    ax.set(xlim=(-lim, lim), ylim=(-lim, lim), zlim=(-lim, lim))
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=25, azim=-25)
    ax.set_axis_off()
    ax.set_title(f"{name}: one orbit and the Earth's shadow\n"
                 "orange = sunlit, dark = eclipse (sizes to scale)", color=INK, pad=-4)
    fig.subplots_adjust(0, 0, 1, 1)
    fig.savefig(OUT / "eclipse-geometry.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    trim_white(OUT / "eclipse-geometry.png")
    return beta


def eclipse_vs_beta(beta_now: float) -> None:
    """Eclipse duration against beta angle for a circular orbit."""
    mu = 398600.4418
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=160)
    for h, label, ls in [(544, "544 km (FUNcube-1, mean)", "-"), (420, "420 km (ISS)", "--")]:
        a = R + h
        period = 2 * np.pi * np.sqrt(a**3 / mu) / 60
        beta_star = np.degrees(np.arcsin(R / a))
        b = np.linspace(0, 90, 500)
        arg = np.sqrt(h**2 + 2 * R * h) / (a * np.cos(np.radians(b)))
        frac = np.where(b < beta_star, np.arccos(np.clip(arg, -1, 1)) / np.pi, 0)
        ax.plot(b, frac * period, color=INK, ls=ls, lw=1.6, label=label)
        if h == 544:
            t_now = np.interp(beta_now, b, frac * period)
            ax.plot(beta_now, t_now, "o", color=SUN, zorder=5)
            ax.annotate(f"FUNcube-1 today, β = {beta_now:.0f}°", (beta_now, t_now),
                        (beta_now - 34, t_now - 9), color=INK,
                        arrowprops={"arrowstyle": "-", "color": INK, "lw": 0.8})
    ax.set(xlabel="beta angle β [°]", ylabel="eclipse per orbit [min]", xlim=(0, 90), ylim=(0, 40))
    ax.grid(alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    ax.set_title("Above β* = asin(R / (R + h)) the orbit never enters the shadow", color=INK)
    fig.tight_layout()
    fig.savefig(OUT / "eclipse-vs-beta.png", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    beta = eclipse_geometry()
    eclipse_vs_beta(beta)
    print(f"Saved docs/eclipse-geometry.png and docs/eclipse-vs-beta.png (beta {beta:.1f}°)")
