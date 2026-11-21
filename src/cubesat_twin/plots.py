"""Plots for the digital twin."""
import matplotlib

matplotlib.use("Agg")  # save to file without opening windows (handy in WSL)
import matplotlib.pyplot as plt
import pandas as pd


def plot_ground_track(df: pd.DataFrame, path: str, title: str = "") -> None:
    """Draw the ground track (lon, lat) coloured by hour and save it as a PNG."""
    hours = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600

    fig, ax = plt.subplots(figsize=(10, 5))
    points = ax.scatter(df.lon_deg, df.lat_deg, c=hours, s=2, cmap="viridis")
    ax.scatter(df.lon_deg.iloc[0], df.lat_deg.iloc[0], marker="*", s=150,
               color="red", zorder=3, label="start")

    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.set_xticks(range(-180, 181, 30))
    ax.set_yticks(range(-90, 91, 30))
    ax.set_xlabel("Longitude [°]")
    ax.set_ylabel("Latitude [°]")
    ax.set_title(title or "Ground track")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left")
    fig.colorbar(points, ax=ax, label="Hours from start")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_sunlight(df: pd.DataFrame, path: str, title: str = "",
                  hours: float = 6) -> None:
    """Sunlight (1) or shadow (0) over time, for both eclipse models."""
    h = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600
    window = h <= hours

    fig, ax = plt.subplots(figsize=(10, 3))
    ax.step(h[window], df.sunlit[window].astype(int), where="post",
            label="skyfield")
    if "sunlit_cyl" in df:
        ax.step(h[window], df.sunlit_cyl[window].astype(int) * 0.95,
                where="post", linestyle="--", label="cylindrical")
    ax.set_yticks([0, 1], ["shadow", "sun"])
    ax.set_xlabel("Hours from start")
    ax.set_title(title or "Sunlight and shadow")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_power_soc(df: pd.DataFrame, path: str, title: str = "") -> None:
    """Generated power, load and battery state of charge over time."""
    days = (df.time - df.time.iloc[0]).dt.total_seconds() / 86400

    fig, (ax_p, ax_s) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax_p.plot(days, df.power_w, linewidth=0.6, label="solar array")
    ax_p.plot(days, df.load_w, linewidth=1.2, label="load")
    ax_p.set_ylabel("Power [W]")
    ax_p.set_title(title or "Power and battery")
    ax_p.grid(alpha=0.3)
    ax_p.legend(loc="upper right")

    ax_s.plot(days, df.soc * 100, color="tab:green")
    ax_s.set_ylabel("State of charge [%]")
    ax_s.set_ylim(0, 105)
    ax_s.set_xlabel("Days from start")
    ax_s.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    from datetime import datetime

    from orbit import propagate, read_tle

    name, l1, l2 = read_tle("data/tle/funcube1.tle")
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 86400, 30)
    plot_ground_track(df, "results/ground_track.png", f"{name}: ground track, 24 hours")
    print("Saved results/ground_track.png")