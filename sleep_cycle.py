#!/usr/bin/env python3
"""Genera un hipnograma diario con las fases de sueño registradas por Oura."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "oura-matplotlib"),
)

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

from sleep_histogram import (
    DEFAULT_EXPORTS_DIR,
    discover_sleep_files,
    extract_documents,
)


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "exports" / "analysis"

STAGES = {
    "1": {"name": "Profundo", "level": 0, "color": "#3155a4"},
    "2": {"name": "Ligero", "level": 1, "color": "#58a6d6"},
    "3": {"name": "REM", "level": 2, "color": "#9b6bd3"},
    "4": {"name": "Despierto", "level": 3, "color": "#ef8f55"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera un gráfico diario de las fases Profundo, Ligero, REM y "
            "Despierto usando sleep_phase_5_min de Oura."
        )
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Fecha de Oura en formato YYYY-MM-DD. Si se omite, usa la más reciente.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Genera un PNG para cada noche disponible.",
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
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Carpeta para los PNG (predeterminado: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if args.all and args.date:
        parser.error("--all y --date no se pueden usar juntos.")
    return args


def load_sleep_records(paths: Iterable[Path]) -> pd.DataFrame:
    """Combina archivos, elimina duplicados y conserva una noche por fecha."""
    documents: list[dict] = []
    for path in paths:
        documents.extend(extract_documents(path))

    if not documents:
        raise ValueError("Los archivos seleccionados no contienen períodos de sueño.")

    frame = pd.json_normalize(documents)
    required = {"day", "type", "bedtime_start", "sleep_phase_5_min"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            "Faltan campos necesarios en sleep.json: " + ", ".join(sorted(missing))
        )

    if "id" in frame.columns:
        frame = frame.drop_duplicates(subset="id", keep="last")
    else:
        frame = frame.drop_duplicates(
            subset=["day", "type", "bedtime_start", "sleep_phase_5_min"],
            keep="last",
        )

    frame["day"] = pd.to_datetime(frame["day"], errors="coerce")
    frame["bedtime_start"] = frame["bedtime_start"].apply(parse_timestamp)
    if "bedtime_end" in frame.columns:
        frame["bedtime_end"] = frame["bedtime_end"].apply(parse_timestamp)

    frame = frame.loc[
        frame["type"].eq("long_sleep")
        & frame["day"].notna()
        & frame["bedtime_start"].notna()
        & frame["sleep_phase_5_min"].fillna("").astype(str).str.len().gt(0)
    ].copy()

    if frame.empty:
        raise ValueError(
            "No se encontraron noches long_sleep con sleep_phase_5_min."
        )

    frame["_phase_count"] = frame["sleep_phase_5_min"].astype(str).str.len()
    frame = (
        frame.sort_values(["day", "_phase_count"])
        .drop_duplicates(subset="day", keep="last")
        .sort_values("day")
        .reset_index(drop=True)
    )
    return frame


def parse_timestamp(value: object) -> pd.Timestamp:
    """Convierte una fecha conservando el huso horario incluido por Oura."""
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT


def clean_phase_string(value: object) -> str:
    """Valida y limpia la secuencia de fases de 5 minutos."""
    phases = str(value).strip()
    invalid = sorted(set(phases).difference(STAGES))
    if invalid:
        raise ValueError(
            "sleep_phase_5_min contiene códigos desconocidos: " + ", ".join(invalid)
        )
    if not phases:
        raise ValueError("sleep_phase_5_min está vacío.")
    return phases


def phase_segments(
    phases: str,
    bedtime_start: pd.Timestamp,
    bedtime_end: pd.Timestamp | None = None,
) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """Agrupa estados consecutivos en segmentos de cinco minutos."""
    phases = clean_phase_string(phases)
    interval = pd.Timedelta(minutes=5)
    maximum_end = (
        bedtime_end
        if bedtime_end is not None and not pd.isna(bedtime_end)
        else bedtime_start + len(phases) * interval
    )
    segments: list[tuple[pd.Timestamp, pd.Timestamp, str]] = []
    run_start = bedtime_start
    current = phases[0]

    for index, phase in enumerate(phases[1:], start=1):
        if phase == current:
            continue
        run_end = min(bedtime_start + index * interval, maximum_end)
        if run_end > run_start:
            segments.append((run_start, run_end, current))
        run_start = run_end
        current = phase

    final_end = min(bedtime_start + len(phases) * interval, maximum_end)
    if final_end > run_start:
        segments.append((run_start, final_end, current))
    return segments


def format_duration(seconds: object, fallback_minutes: int) -> str:
    try:
        total_minutes = round(float(seconds) / 60)
    except (TypeError, ValueError):
        total_minutes = fallback_minutes

    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes:02d} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"


def stage_summary(record: pd.Series, phases: str) -> str:
    """Crea el resumen de tiempos que aparece debajo del título."""
    phase_minutes = {
        code: phases.count(code) * 5
        for code in STAGES
    }
    duration_fields = {
        "1": "deep_sleep_duration",
        "2": "light_sleep_duration",
        "3": "rem_sleep_duration",
        "4": "awake_time",
    }
    parts = []
    for code in ("1", "2", "3", "4"):
        field = duration_fields[code]
        seconds = record.get(field) if field in record.index else None
        parts.append(
            f"{STAGES[code]['name']} "
            f"{format_duration(seconds, phase_minutes[code])}"
        )
    return "  ·  ".join(parts)


def render_sleep_cycle(record: pd.Series, output_dir: Path) -> Path:
    """Renderiza un hipnograma para una sola noche."""
    phases = clean_phase_string(record["sleep_phase_5_min"])
    bedtime_start = record["bedtime_start"]
    bedtime_end = record.get("bedtime_end")
    segments = phase_segments(phases, bedtime_start, bedtime_end)
    if not segments:
        raise ValueError(f"No fue posible construir el ciclo del día {record['day'].date()}.")

    start = segments[0][0]
    end = segments[-1][1]
    day_text = record["day"].date().isoformat()

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(12, 6.4))
    fig.patch.set_facecolor("#f7f5f0")
    ax.set_facecolor("#f7f5f0")

    for segment_start, segment_end, code in segments:
        stage = STAGES[code]
        left = mdates.date2num(segment_start.to_pydatetime())
        right = mdates.date2num(segment_end.to_pydatetime())
        ax.broken_barh(
            [(left, right - left)],
            (stage["level"] - 0.32, 0.64),
            facecolors=stage["color"],
            edgecolors=stage["color"],
        )

    for previous, current in zip(segments, segments[1:]):
        transition_time = mdates.date2num(previous[1].to_pydatetime())
        previous_level = STAGES[previous[2]]["level"]
        current_level = STAGES[current[2]]["level"]
        ax.vlines(
            transition_time,
            min(previous_level, current_level),
            max(previous_level, current_level),
            color="#9aa5b1",
            linewidth=1.1,
            alpha=0.8,
        )

    ax.set_xlim(
        mdates.date2num(start.to_pydatetime()),
        mdates.date2num(end.to_pydatetime()),
    )
    ax.set_ylim(-0.65, 3.65)
    ax.set_yticks(
        [STAGES[code]["level"] for code in ("4", "3", "2", "1")],
        [STAGES[code]["name"] for code in ("4", "3", "2", "1")],
    )
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=start.tzinfo))
    ax.grid(axis="x", color="#ddd8ce", linewidth=0.8)
    ax.grid(axis="y", color="#ddd8ce", linewidth=0.8)
    ax.set_axisbelow(True)

    ax.set_title(
        f"Ciclo del sueño · {day_text}",
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
            f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}  ·  "
            f"{stage_summary(record, phases)}"
        ),
        transform=ax.transAxes,
        fontsize=10.5,
        color="#52606d",
        va="bottom",
    )
    ax.set_xlabel("Hora", fontsize=11)
    ax.set_ylabel("Fase", fontsize=11)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#9aa5b1")
    ax.tick_params(colors="#52606d")

    legend = [
        Patch(facecolor=STAGES[code]["color"], label=STAGES[code]["name"])
        for code in ("1", "2", "3", "4")
    ]
    ax.legend(
        handles=legend,
        frameon=False,
        ncol=4,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
    )
    fig.text(
        0.985,
        0.018,
        "Cada bloque representa la clasificación de Oura para un intervalo de 5 minutos",
        ha="right",
        va="bottom",
        fontsize=8,
        color="#7b8794",
    )
    fig.tight_layout(rect=(0.02, 0.07, 0.98, 0.98))

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"sleep_cycle_{day_text}.png"
    fig.savefig(output, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return output


def select_records(frame: pd.DataFrame, requested_date: date | None, all_days: bool) -> pd.DataFrame:
    if all_days:
        return frame
    if requested_date is None:
        return frame.tail(1)

    selected = frame.loc[frame["day"].dt.date.eq(requested_date)]
    if selected.empty:
        first = frame["day"].min().date().isoformat()
        last = frame["day"].max().date().isoformat()
        raise ValueError(
            f"No hay un long_sleep para {requested_date.isoformat()}. "
            f"El rango disponible es {first} a {last}."
        )
    return selected


def main() -> int:
    args = parse_args()
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
        selected = select_records(frame, args.date, args.all)
        outputs = [
            render_sleep_cycle(record, args.output_dir)
            for _, record in selected.iterrows()
        ]
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Archivos combinados: {len(paths)}")
    print(f"Noches generadas:    {len(outputs)}")
    for output in outputs:
        print(f"  {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())