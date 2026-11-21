"""Grafici del digital twin."""
import matplotlib

matplotlib.use("Agg")  # salva su file senza aprire finestre (utile in WSL)
import matplotlib.pyplot as plt
import pandas as pd


def plot_ground_track(df: pd.DataFrame, path: str, title: str = "") -> None:
    """Disegna la traccia a terra (lon, lat) colorata per ora e la salva in un PNG."""
    hours = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600

    fig, ax = plt.subplots(figsize=(10, 5))
    points = ax.scatter(df.lon_deg, df.lat_deg, c=hours, s=2, cmap="viridis")
    ax.scatter(df.lon_deg.iloc[0], df.lat_deg.iloc[0], marker="*", s=150,
               color="red", zorder=3, label="inizio")

    ax.set_xlim(-180, 180)
    ax.set_ylim(-90, 90)
    ax.set_xticks(range(-180, 181, 30))
    ax.set_yticks(range(-90, 91, 30))
    ax.set_xlabel("Longitudine [°]")
    ax.set_ylabel("Latitudine [°]")
    ax.set_title(title or "Traccia a terra")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left")
    fig.colorbar(points, ax=ax, label="Ore dall'inizio")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_sunlight(df: pd.DataFrame, path: str, title: str = "",
                  hours: float = 6) -> None:
    """Sole (1) o ombra (0) nel tempo, per i due modelli di eclissi."""
    h = (df.time - df.time.iloc[0]).dt.total_seconds() / 3600
    window = h <= hours

    fig, ax = plt.subplots(figsize=(10, 3))
    ax.step(h[window], df.sunlit[window].astype(int), where="post",
            label="skyfield")
    if "sunlit_cyl" in df:
        ax.step(h[window], df.sunlit_cyl[window].astype(int) * 0.95,
                where="post", linestyle="--", label="cilindrico")
    ax.set_yticks([0, 1], ["ombra", "sole"])
    ax.set_xlabel("Ore dall'inizio")
    ax.set_title(title or "Sole e ombra")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    from datetime import datetime

    from orbit import propagate, read_tle

    name, l1, l2 = read_tle("data/tle/funcube1.tle")
    df = propagate(name, l1, l2, datetime(2026, 11, 21), 86400, 30)
    plot_ground_track(df, "results/ground_track.png", f"{name}: traccia a terra, 24 ore")
    print("Salvato results/ground_track.png")