#!/usr/bin/env python3
"""Genera un histograma del sueño nocturno exportado desde Oura."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "oura-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import MaxNLocator, MultipleLocator


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_EXPORTS_DIR = PROJECT_DIR / "exports"
DEFAULT_OUTPUT = DEFAULT_EXPORTS_DIR / "analysis" / "sleep_histogram.png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combina los datos de sueño exportados por Oura y genera un "
            "histograma de la duración del sueño nocturno."
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
        "--bin-minutes",
        type=int,
        default=30,
        help="Ancho de cada intervalo del histograma en minutos (predeterminado: 30).",
    )
    return parser.parse_args()


def discover_sleep_files(exports_dir: Path = DEFAULT_EXPORTS_DIR) -> list[Path]:
    """Encuentra todos los sleep.json de las carpetas de exportación."""
    return sorted(
        path
        for path in exports_dir.glob("oura_*/sleep.json")
        if path.is_file()
    )


def extract_documents(path: Path) -> list[dict]:
    """Lee un sleep.json, aceptando tanto una lista como {"data": [...]}."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"No existe el archivo: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"El archivo no contiene JSON válido: {path}") from exc

    if isinstance(payload, list):
        documents = payload
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        documents = payload["data"]
    else:
        raise ValueError(
            f"Formato inesperado en {path}; se esperaba una lista o un objeto con 'data'."
        )

    return [document for document in documents if isinstance(document, dict)]


def load_nightly_sleep(paths: Iterable[Path]) -> pd.DataFrame:
    """Combina exportaciones y conserva una observación principal por noche."""
    documents: list[dict] = []
    sources: list[str] = []

    for path in paths:
        file_documents = extract_documents(path)
        documents.extend(file_documents)
        sources.extend([str(path)] * len(file_documents))

    if not documents:
        raise ValueError("Los archivos seleccionados no contienen períodos de sueño.")

    frame = pd.json_normalize(documents)
    frame["_source"] = sources

    required = {"day", "type", "total_sleep_duration"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            "Faltan campos necesarios en sleep.json: " + ", ".join(sorted(missing))
        )

    if "id" in frame.columns:
        frame = frame.drop_duplicates(subset="id", keep="last")
    else:
        frame = frame.drop_duplicates(
            subset=["day", "type", "bedtime_start", "total_sleep_duration"],
            keep="last",
        )

    frame["day"] = pd.to_datetime(frame["day"], errors="coerce")
    frame["total_sleep_duration"] = pd.to_numeric(
        frame["total_sleep_duration"],
        errors="coerce",
    )

    frame = frame.loc[
        frame["type"].eq("long_sleep")
        & frame["day"].notna()
        & frame["total_sleep_duration"].gt(0)
    ].copy()

    if frame.empty:
        raise ValueError(
            "No se encontraron registros type='long_sleep' con una duración válida."
        )

    # Si Oura entrega más de un long_sleep para una fecha, consideramos la
    # observación de mayor duración como el sueño nocturno principal.
    frame = (
        frame.sort_values(["day", "total_sleep_duration"])
        .drop_duplicates(subset="day", keep="last")
        .sort_values("day")
        .reset_index(drop=True)
    )
    frame["sleep_hours"] = frame["total_sleep_duration"] / 3600
    return frame


def build_bins(values: pd.Series, bin_minutes: int) -> list[float]:
    """Construye intervalos regulares que cubren todos los valores."""
    if bin_minutes <= 0:
        raise ValueError("--bin-minutes debe ser mayor que cero.")

    step = bin_minutes / 60
    lower = math.floor(float(values.min()) / step) * step
    upper = math.ceil(float(values.max()) / step) * step
    if math.isclose(lower, upper):
        upper = lower + step

    count = int(round((upper - lower) / step))
    return [lower + index * step for index in range(count + 1)]


def calculate_stats(frame: pd.DataFrame) -> dict[str, float | int | str]:
    hours = frame["sleep_hours"]
    return {
        "nights": int(hours.count()),
        "start_day": frame["day"].min().date().isoformat(),
        "end_day": frame["day"].max().date().isoformat(),
        "mean": float(hours.mean()),
        "median": float(hours.median()),
        "p25": float(hours.quantile(0.25)),
        "p75": float(hours.quantile(0.75)),
        "reference_percentage": float(hours.between(7, 9, inclusive="both").mean() * 100),
    }


def render_histogram(
    frame: pd.DataFrame,
    output: Path,
    bin_minutes: int = 30,
) -> dict[str, float | int | str]:
    hours = frame["sleep_hours"]
    bins = build_bins(hours, bin_minutes)
    stats = calculate_stats(frame)

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 6.5))
    fig.patch.set_facecolor("#f7f5f0")
    ax.set_facecolor("#f7f5f0")

    ax.axvspan(7, 9, color="#8fb996", alpha=0.18, label="Referencia 7–9 h")
    ax.hist(
        hours,
        bins=bins,
        color="#416788",
        edgecolor="#f7f5f0",
        linewidth=1.2,
        alpha=0.92,
    )
    ax.axvline(
        stats["mean"],
        color="#d1495b",
        linewidth=2.2,
        label=f"Media: {stats['mean']:.2f} h",
    )
    ax.axvline(
        stats["median"],
        color="#edae49",
        linewidth=2.2,
        linestyle="--",
        label=f"Mediana: {stats['median']:.2f} h",
    )

    ax.set_title(
        "Distribución del sueño nocturno",
        loc="left",
        fontsize=18,
        fontweight="bold",
        color="#1f2933",
        pad=22,
    )
    ax.text(
        0,
        1.015,
        (
            f"{stats['start_day']} a {stats['end_day']}  ·  "
            f"{stats['nights']} noches  ·  "
            f"{stats['reference_percentage']:.1f}% entre 7 y 9 horas"
        ),
        transform=ax.transAxes,
        fontsize=10.5,
        color="#52606d",
        va="bottom",
    )
    ax.set_xlabel("Duración total del sueño (horas)", fontsize=11)
    ax.set_ylabel("Cantidad de noches", fontsize=11)
    ax.xaxis.set_major_locator(MultipleLocator(bin_minutes / 60))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="x", visible=False)
    ax.grid(axis="y", color="#d9d5cc", linewidth=0.8)
    ax.set_axisbelow(True)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#9aa5b1")
    ax.tick_params(colors="#52606d")
    ax.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.15))

    fig.text(
        0.985,
        0.015,
        "Fuente: exportación personal de Oura · referencia visual, no evaluación médica",
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
    return stats


def main() -> int:
    args = parse_args()
    paths = [path.expanduser().resolve() for path in args.input or []]
    if not paths:
        paths = discover_sleep_files()

    if not paths:
        print(
            "ERROR: no encontré archivos exports/oura_*/sleep.json. "
            "Ejecutá primero una descarga o indicá --input."
        )
        return 1

    try:
        frame = load_nightly_sleep(paths)
        stats = render_histogram(
            frame,
            output=args.output,
            bin_minutes=args.bin_minutes,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Archivos combinados: {len(paths)}")
    print(f"Noches analizadas:  {stats['nights']}")
    print(f"Período:             {stats['start_day']} a {stats['end_day']}")
    print(f"Media:               {stats['mean']:.2f} h")
    print(f"Mediana:             {stats['median']:.2f} h")
    print(f"Rango central:       {stats['p25']:.2f}–{stats['p75']:.2f} h (P25–P75)")
    print(f"Entre 7 y 9 horas:   {stats['reference_percentage']:.1f}%")
    print(f"Gráfico guardado en: {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())