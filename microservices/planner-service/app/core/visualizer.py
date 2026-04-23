"""Renders a map + planned path to a PNG blob.

Uses matplotlib Agg backend (headless). Mirrors the visual language of
pathformer/scripts/visualize.py so results look familiar.
"""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def render_plan(
    arrays: dict[str, np.ndarray],
    waypoints_pixels: np.ndarray,
    start_pixels: tuple[float, float],
    goal_pixels: tuple[float, float],
    success: bool,
    n_repairs: int,
    max_current_global: float,
) -> bytes:
    intensity = arrays["current_intensity"]
    direction = arrays["current_direction"]
    land_mask = arrays["land_mask"]
    H, W = land_mask.shape

    u = intensity * np.cos(direction) / max_current_global
    v = intensity * np.sin(direction) / max_current_global
    magnitude = np.sqrt(u**2 + v**2)

    fig, ax = plt.subplots(figsize=(8, 8))

    ax.imshow(magnitude, cmap="Blues", origin="upper", alpha=0.75)
    ax.imshow(np.ma.masked_where(land_mask < 0.5, land_mask), cmap="YlOrBr", origin="upper", alpha=0.6)

    step = max(1, H // 20)
    rows, cols = np.meshgrid(
        np.arange(0, H, step),
        np.arange(0, W, step),
        indexing="ij",
    )
    ax.quiver(
        cols,
        rows,
        u[::step, ::step],
        -v[::step, ::step],  # flip v: matplotlib y axis grows downward
        color="navy",
        alpha=0.4,
        scale=15,
    )

    style = "b-o" if success else "r-o"
    ax.plot(
        waypoints_pixels[:, 1],
        waypoints_pixels[:, 0],
        style,
        markersize=2,
        linewidth=1.5,
        label="Path" + ("" if success else " (failed)"),
    )

    ax.plot(start_pixels[1], start_pixels[0], "gs", markersize=10, label="Start")
    ax.plot(goal_pixels[1], goal_pixels[0], "r*", markersize=14, label="Goal")

    ax.set_xlim(-0.5, W - 0.5)
    ax.set_ylim(H - 0.5, -0.5)  # inverted y for image coordinates
    ax.set_title(
        f"Planned path (success={success}, "
        f"{len(waypoints_pixels)} waypoints, {n_repairs} repairs)"
    )
    ax.legend(loc="upper right")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
