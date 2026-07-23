#!/usr/bin/env python3
"""Genera el ciclo de sueño promedio de todas las noches exportadas."""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from pathlib import Path

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "oura-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator, PercentFormatter

from sleep_cycle import STAGES, clean_phase_string, load_sleep_records
from sleep_histogram import DEFAULT_EXPORTS_DIR, discover_sleep_files


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR / "exports" / "analysis" / "sleep_cycle_average.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Alinea las noches desde la hora de acostarse y calcula qué "
            "porcentaje estaba en cada fase cada cinco minutos."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        help=(
            "Archivo sleep.json que se quiere analizar. Se puede repetir. "
            "Si se omite, se combinan todos los exports/oura_*/sleep.json."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"PNG de salida (predeterminado: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--last",
        type=int,
        help="Usa solamente las últimas N noches; si se omite, usa todas.",
    )
    parser.add_argument(
        "--min-support",
        type=float,
        default=25,
        help=(
            "Porcentaje mínimo de noches que debe seguir aportando datos "
            "en cada punto (predeterminado: 25)."
        ),
    )
    parser.add_argument(
        "--smooth-minutes",
        type=int,
        default=15,
        help=(
            "Suavizado visual en minutos; use 0 para desactivarlo "
            "(predeterminado: 15)."
        ),
    )
    return parser.parse_args()


def build_average_cycle(
    frame: pd.DataFrame,
    min_support_percent: float = 25,
    smooth_minutes: int = 15,
) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    """Calcula la proporción de cada fase por tiempo desde acostarse."""
    if frame.empty:
        raise ValueError("No hay noches disponibles para calcular el promedio.")
    if not 0 < min_support_percent <= 100:
        raise ValueError("--min-support debe estar entre 0 y 100.")
    if smooth_minutes < 0:
        raise ValueError("--smooth-minutes no puede ser negativo.")

    phase_strings = [
        clean_phase_string(value)
        for value in frame["sleep_phase_5_min"]
    ]
    maximum_length = max(map(len, phase_strings))
    counts = pd.DataFrame(
        0.0,
        index=range(maximum_length),
        columns=list(STAGES),
    )

    for phases in phase_strings:
        for index, code in enumerate(phases):
            counts.at[index, code] += 1

    support = counts.sum(axis=1)
    minimum_nights = max(
        1,
        math.ceil(len(phase_strings) * min_support_percent / 100),
    )
    included = support.ge(minimum_nights)
    if not included.any():
        raise ValueError("Ningún intervalo alcanza el soporte mínimo solicitado.")

    counts = counts.loc[included].copy()
    support = support.loc[included]
    percentages = counts.div(support, axis=0).mul(100)

    window = max(1, round(smooth_minutes / 5)) if smooth_minutes else 1
    if window > 1:
        percentages = percentages.rolling(
            window=window,
            center=True,
            min_periods=1,
        ).mean()
        percentages = percentages.div(percentages.sum(axis=1), axis=0).mul(100)

    result = percentages.rename(
        columns={code: STAGES[code]["name"] for code in STAGES}
    )
    result["elapsed_hours"] = result.index.to_series().mul(5 / 60)
    result["support_nights"] = support.astype(int)

    lengths_hours = pd.Series([len(phases) * 5 / 60 for phases in phase_strings])
    stats: dict[str, float | int | str] = {
        "nights": len(phase_strings),
        "start_day": frame["day"].min().date().isoformat(),
        "end_day": frame["day"].max().date().isoformat(),
        "median_duration": float(lengths_hours.median()),
        "minimum_nights": minimum_nights,
        "minimum_support_percent": float(min_support_percent),
        "smooth_minutes": smooth_minutes,
    }
    return result.reset_index(drop=True), stats


def format_hours(hours: float) -> str:
    total_minutes = round(hours * 60)
    whole_hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"{whole_hours} h {minutes:02d} min"
    return f"{whole_hours} h"


def render_average_cycle(
    average: pd.DataFrame,
    stats: dict[str, float | int | str],
    output: Path,
) -> Path:
    """Renderiza un área apilada con la distribución promedio de fases."""
    plot_average = average.copy()
    terminal = plot_average.iloc[-1].copy()
    terminal["elapsed_hours"] = float(terminal["elapsed_hours"]) + 5 / 60
    plot_average = pd.concat(
        [plot_average, terminal.to_frame().T],
        ignore_index=True,
    )
    x = plot_average["elapsed_hours"].astype(float)
    stage_order = ("Profundo", "Ligero", "REM", "Despierto")
    colors = [
        STAGES[code]["color"]
        for code in ("1", "2", "3", "4")
    ]

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(12, 6.7))
    fig.patch.set_facecolor("#f7f5f0")
    ax.set_facecolor("#f7f5f0")

    ax.stackplot(
        x,
        *(plot_average[stage].astype(float) for stage in stage_order),
        colors=colors,
        alpha=0.94,
    )

    median_duration = float(stats["median_duration"])
    ax.axvline(
        median_duration,
        color="#34495e",
        linewidth=1.8,
        linestyle="--",
        alpha=0.9,
    )
    ax.text(
        median_duration,
        102,
        f"Mediana: {format_hours(median_duration)}",
        ha="center",
        va="bottom",
        fontsize=9,
        color="#34495e",
    )

    ax.set_xlim(0, max(float(x.max()), 0.5))
    ax.set_ylim(0, 100)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.yaxis.set_major_formatter(PercentFormatter(100))
    ax.grid(axis="x", which="major", color="#ddd8ce", linewidth=0.8)
    ax.grid(axis="x", which="minor", color="#e9e5dd", linewidth=0.5)
    ax.grid(axis="y", color="#ddd8ce", linewidth=0.8)
    ax.set_axisbelow(True)

    ax.set_title(
        "Ciclo del sueño promedio",
        loc="left",
        fontsize=18,
        fontweight="bold",
        color="#1f2933",
        pad=26,
    )
    ax.text(
        0,
        1.025,
        (
            f"{stats['start_day']} a {stats['end_day']}  ·  "
            f"{stats['nights']} noches  ·  "
            "alineadas desde la hora de acostarse"
        ),
        transform=ax.transAxes,
        fontsize=10.5,
        color="#52606d",
        va="bottom",
    )
    ax.set_xlabel("Tiempo desde que te acostaste (horas)", fontsize=11)
    ax.set_ylabel("Porcentaje de noches en cada fase", fontsize=11)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#9aa5b1")
    ax.tick_params(colors="#52606d")

    legend_handles = [
        Patch(facecolor=color, label=stage)
        for stage, color in zip(stage_order, colors)
    ]
    legend_handles.append(
        Line2D(
            [0],
            [0],
            color="#34495e",
            linestyle="--",
            linewidth=1.8,
            label="Duración mediana",
        )
    )
    ax.legend(
        handles=legend_handles,
        frameon=False,
        ncol=5,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
    )

    smooth_text = (
        f"suavizado {stats['smooth_minutes']} min"
        if stats["smooth_minutes"]
        else "sin suavizado"
    )
    fig.text(
        0.985,
        0.018,
        (
            f"Intervalos de 5 min · {smooth_text} · se muestra mientras aportan "
            f"al menos {stats['minimum_nights']} noches "
            f"({stats['minimum_support_percent']:g}%)"
        ),
        ha="right",
        va="bottom",
        fontsize=8,
        color="#7b8794",
    )
    fig.tight_layout(rect=(0.02, 0.07, 0.98, 0.98))

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output


def main() -> int:
    args = parse_args()
    if args.last is not None and args.last <= 0:
        print("ERROR: --last debe ser mayor que cero.")
        return 1

    paths = [path.expanduser().resolve() for path in args.input or []]
    if not paths:
        paths = discover_sleep_files(DEFAULT_EXPORTS_DIR)
    if not paths:
        print(
            "ERROR: no encontré archivos exports/oura_*/sleep.json. "
            "Ejecutá primero una descarga o indicá --input."
        )
        return 1

    try:
        frame = load_sleep_records(paths)
        if args.last is not None:
            frame = frame.tail(args.last).reset_index(drop=True)
        average, stats = build_average_cycle(
            frame,
            min_support_percent=args.min_support,
            smooth_minutes=args.smooth_minutes,
        )
        output = render_average_cycle(average, stats, args.output)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Archivos combinados: {len(paths)}")
    print(f"Noches promediadas:  {stats['nights']}")
    print(f"Período:             {stats['start_day']} a {stats['end_day']}")
    print(f"Duración mediana:    {format_hours(float(stats['median_duration']))}")
    print(f"Gráfico guardado en: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())