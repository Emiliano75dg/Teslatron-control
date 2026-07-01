from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

THIS_DIR = Path(__file__).resolve().parent
DEFAULT_LOG_DIRECTORY = (THIS_DIR.parent / "data").resolve()
DEFAULT_LOG_PATTERN = "cryostat_environment_*.csv"
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from environment_log_reader import (
    LOCAL_TIMEZONE,
    MAGNETICS_SERIES_LEFT,
    MAGNETICS_SERIES_RIGHT,
    TEMPERATURE_SERIES,
    build_event_list,
    load_and_prepare_logs,
    summarize_log,
)


st.set_page_config(page_title="Cryostat Environment Log", layout="wide")


def get_theme_tokens(theme: str) -> dict[str, str]:
    """Return explicit colors for the selected visual theme."""
    if theme == "Dark":
        return {
            "template": "plotly_dark",
            "paper_bgcolor": "#0f172a",
            "plot_bgcolor": "#111827",
            "font_color": "#e5eefb",
            "grid_color": "#334155",
            "axis_color": "#94a3b8",
            "title_color": "#e5eefb",
        }
    return {
        "template": "plotly",
        "paper_bgcolor": "#ffffff",
        "plot_bgcolor": "#ffffff",
        "font_color": "#000000",
        "grid_color": "#d7e3ef",
        "axis_color": "#000000",
        "title_color": "#000000",
    }


def apply_app_theme(theme: str) -> None:
    """Apply a visible light/dark theme to the Streamlit app shell."""
    if theme == "Dark":
        st.markdown(
            """
            <style>
            .stApp {
                background: linear-gradient(180deg, #020617 0%, #0f172a 100%);
                color: #e5eefb;
            }
            [data-testid="stSidebar"] {
                background: #111827;
            }
            [data-testid="stMetric"] {
                background: rgba(15, 23, 42, 0.55);
                border: 1px solid #334155;
                border-radius: 12px;
                padding: 0.6rem 0.8rem;
            }
            [data-testid="stMetricLabel"],
            [data-testid="stMetricValue"] {
                color: #e5eefb;
            }
            div[data-baseweb="select"] > div,
            div[data-baseweb="base-input"] > div,
            div[data-baseweb="tag"] {
                background: #0f172a;
                color: #e5eefb;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        """
        <style>
        .stApp {
            background: linear-gradient(180deg, #f8fbff 0%, #eef4fb 100%);
            color: #16324f;
        }
        [data-testid="stHeader"] {
            background: rgba(248, 251, 255, 0.95);
        }
        [data-testid="stToolbar"] {
            background: transparent;
        }
        [data-testid="stSidebar"] {
            background: #f6f9fd;
        }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.92);
            border: 1px solid #d7e3ef;
            border-radius: 12px;
            padding: 0.6rem 0.8rem;
        }
        .stApp,
        .stApp p,
        .stApp label,
        .stApp span,
        .stApp div,
        .stApp h1,
        .stApp h2,
        .stApp h3 {
            color: #16324f;
        }
        [data-testid="stMetricLabel"],
        [data-testid="stMetricValue"] {
            color: #16324f;
        }
        [data-testid="stFileUploader"] section {
            background: #ffffff;
            border: 1px solid #d7e3ef;
            color: #16324f;
        }
        [data-testid="stFileUploader"] section small,
        [data-testid="stFileUploader"] section span,
        [data-testid="stFileUploader"] section div,
        [data-testid="stFileUploader"] label {
            color: #16324f;
        }
        [data-testid="stFileUploader"] button {
            background: #ffffff;
            color: #16324f;
            border: 1px solid #c8d8ea;
        }
        [data-testid="stFileUploaderDropzone"] {
            background: #ffffff;
        }
        .stButton > button {
            background: #ffffff;
            color: #16324f;
            border: 1px solid #c8d8ea;
        }
        .stButton > button:hover {
            background: #eef5fc;
            color: #16324f;
        }
        [data-baseweb="radio"] label,
        [data-baseweb="radio"] div {
            color: #16324f;
        }
        [data-baseweb="input"] {
            background: #ffffff;
        }
        [data-baseweb="input"] input {
            background: #ffffff !important;
            color: #16324f !important;
            -webkit-text-fill-color: #16324f !important;
        }
        [data-baseweb="base-input"] {
            background: #ffffff;
            border: 1px solid #c8d8ea;
            border-radius: 10px;
        }
        div[data-baseweb="select"] > div,
        div[data-baseweb="base-input"] > div,
        div[data-baseweb="tag"] {
            background: #ffffff;
            color: #16324f;
        }
        [data-baseweb="select"] input,
        [data-baseweb="select"] span,
        [data-baseweb="select"] svg,
        [data-baseweb="select"] div {
            color: #16324f !important;
            fill: #16324f !important;
        }
        div[data-baseweb="popover"] {
            background: #ffffff;
            color: #16324f;
        }
        [data-baseweb="popover"] * {
            color: #16324f !important;
        }
        [data-testid="stNumberInput"] button {
            background: #ffffff;
            color: #16324f;
            border: 1px solid #c8d8ea;
        }
        [data-testid="stNumberInput"] input {
            background: #ffffff !important;
            color: #16324f !important;
            -webkit-text-fill-color: #16324f !important;
        }
        [data-testid="stTextInput"] input {
            background: #ffffff !important;
            color: #16324f !important;
            -webkit-text-fill-color: #16324f !important;
        }
        [data-testid="stMultiSelect"] {
            background: transparent;
        }
        .stDataFrame, .stTable {
            background: transparent;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def available_series(df: pd.DataFrame, series_map: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Return only the series actually present in the dataframe."""
    return {key: props for key, props in series_map.items() if key in df.columns}


def classify_event(description: str) -> str:
    """Map an event description to a compact category label."""
    lowered = description.lower()
    if "safety" in lowered:
        return "Safety"
    if "field" in lowered or "switch heater" in lowered:
        return "Field"
    if "sample" in lowered:
        return "Sample"
    if "vti" in lowered:
        return "VTI"
    if "pressure" in lowered or "needle" in lowered:
        return "Pressure"
    if "mode" in lowered or "state" in lowered or "acquisition" in lowered:
        return "State"
    return "Other"


EVENT_CATEGORY_COLORS: dict[str, str] = {
    "Safety": "#dc2626",
    "Field": "#16a34a",
    "Sample": "#c2410c",
    "VTI": "#2563eb",
    "Pressure": "#7c3aed",
    "State": "#475569",
    "Other": "#a16207",
}


EVENT_CATEGORY_ORDER: list[str] = [
    "Safety",
    "Field",
    "Sample",
    "VTI",
    "Pressure",
    "State",
    "Other",
]


def shorten_event_label(description: str, limit: int = 28) -> str:
    """Create a short label for dense event markers."""
    compact = " ".join(str(description).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def event_rail_y(df: pd.DataFrame, series_keys: Iterable[str]) -> float:
    """Choose a visible y-position for the event rail near the lower plot edge."""
    numeric_values: list[float] = []
    for key in series_keys:
        if key not in df.columns:
            continue
        series = pd.to_numeric(df[key], errors="coerce").dropna()
        if not series.empty:
            numeric_values.extend(series.tolist())

    if not numeric_values:
        return 0.0

    min_y = min(numeric_values)
    max_y = max(numeric_values)
    span = max(max_y - min_y, 1e-9)
    return min_y + span * 0.04


def add_event_overlay(
    fig: go.Figure,
    df: pd.DataFrame,
    time_col: str,
    events_df: pd.DataFrame,
    series_keys: Iterable[str],
) -> None:
    """Render a compact event rail inside the main plot area."""
    if events_df.empty:
        return

    rail_y = event_rail_y(df, series_keys)
    fig.add_shape(
        type="line",
        x0=df[time_col].min(),
        x1=df[time_col].max(),
        xref="x",
        y0=rail_y,
        y1=rail_y,
        yref="y",
        line={"color": "rgba(220, 38, 38, 0.35)", "width": 2},
        layer="below",
    )
    ordered_categories = [category for category in EVENT_CATEGORY_ORDER if category in set(events_df["category"])]
    for category in ordered_categories:
        category_df = events_df[events_df["category"] == category]
        color = EVENT_CATEGORY_COLORS.get(category, EVENT_CATEGORY_COLORS["Other"])
        fig.add_trace(
            go.Scatter(
                x=category_df["timestamp"],
                y=[rail_y] * len(category_df),
                mode="markers",
                name=f"Event: {category}",
                marker={
                    "color": color,
                    "size": 9,
                    "symbol": "diamond",
                    "line": {"color": color, "width": 1},
                },
                customdata=category_df[["category", "event"]],
                hovertemplate="%{x}<br><b>%{customdata[0]}</b><br>%{customdata[1]}<extra>Event</extra>",
                showlegend=True,
            )
        )


def build_line_chart(
    df: pd.DataFrame,
    time_col: str,
    series_map: dict[str, dict[str, str]],
    title: str,
    y_title: str,
    theme: str,
    events_df: pd.DataFrame | None = None,
    show_events: bool = True,
) -> go.Figure:
    """Create a Plotly line chart for the selected series."""
    theme_tokens = get_theme_tokens(theme)
    fig = go.Figure()
    for key, props in series_map.items():
        fig.add_trace(
            go.Scatter(
                x=df[time_col],
                y=df[key],
                mode="lines",
                name=props["label"],
                line={"color": props["color"], "width": 2.2},
                hovertemplate="%{x}<br>%{y}<extra>" + props["label"] + "</extra>",
            )
        )

    fig.update_layout(
        title={"text": title, "font": {"color": theme_tokens["title_color"]}},
        xaxis_title="Timestamp",
        yaxis_title=y_title,
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 60, "b": 20},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"color": theme_tokens["font_color"]},
        },
        template=theme_tokens["template"],
        paper_bgcolor=theme_tokens["paper_bgcolor"],
        plot_bgcolor=theme_tokens["plot_bgcolor"],
        font={"color": theme_tokens["font_color"]},
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor=theme_tokens["grid_color"],
        linecolor=theme_tokens["axis_color"],
        tickfont={"color": theme_tokens["font_color"]},
        title_font={"color": theme_tokens["title_color"]},
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=theme_tokens["grid_color"],
        linecolor=theme_tokens["axis_color"],
        tickfont={"color": theme_tokens["font_color"]},
        title_font={"color": theme_tokens["title_color"]},
    )
    if show_events and events_df is not None:
        add_event_overlay(fig, df, time_col, events_df, series_map.keys())
    return fig


def build_overview_subplot(
    df: pd.DataFrame,
    time_col: str,
    panels: list[tuple[dict[str, dict[str, str]], str, str]],
    theme: str,
    events_df: pd.DataFrame | None = None,
    show_events: bool = True,
) -> go.Figure:
    """Create a single figure with shared x-axis so all panels zoom together.

    panels is a list of (series_map, y_axis_title, panel_title) tuples.
    """
    theme_tokens = get_theme_tokens(theme)
    n_rows = len(panels)

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=[p[2] for p in panels],
    )

    for row_idx, (series_map, _y_title, _panel_title) in enumerate(panels, start=1):
        for key, props in series_map.items():
            fig.add_trace(
                go.Scatter(
                    x=df[time_col],
                    y=df[key],
                    mode="lines",
                    name=props["label"],
                    line={"color": props["color"], "width": 2.2},
                    hovertemplate="%{x}<br>%{y}<extra>" + props["label"] + "</extra>",
                ),
                row=row_idx, col=1,
            )

        if show_events and events_df is not None and not events_df.empty:
            rail_y = event_rail_y(df, list(series_map.keys()))
            fig.add_shape(
                type="line",
                x0=df[time_col].min(),
                x1=df[time_col].max(),
                y0=rail_y,
                y1=rail_y,
                line={"color": "rgba(220, 38, 38, 0.35)", "width": 2},
                layer="below",
                row=row_idx, col=1,
            )
            for category in EVENT_CATEGORY_ORDER:
                cat_df = events_df[events_df["category"] == category]
                if cat_df.empty:
                    continue
                color = EVENT_CATEGORY_COLORS.get(category, EVENT_CATEGORY_COLORS["Other"])
                fig.add_trace(
                    go.Scatter(
                        x=cat_df["timestamp"],
                        y=[rail_y] * len(cat_df),
                        mode="markers",
                        name=f"Event: {category}",
                        marker={
                            "color": color,
                            "size": 9,
                            "symbol": "diamond",
                            "line": {"color": color, "width": 1},
                        },
                        customdata=cat_df[["category", "event"]],
                        hovertemplate="%{x}<br><b>%{customdata[0]}</b><br>%{customdata[1]}<extra>Event</extra>",
                        showlegend=(row_idx == 1),
                        legendgroup=f"event_{category}",
                    ),
                    row=row_idx, col=1,
                )

    fig.update_layout(
        hovermode="x unified",
        height=max(640, 520 * n_rows),
        margin={"l": 20, "r": 20, "t": 40, "b": 20},
        template=theme_tokens["template"],
        paper_bgcolor=theme_tokens["paper_bgcolor"],
        plot_bgcolor=theme_tokens["plot_bgcolor"],
        font={"color": theme_tokens["font_color"]},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"color": theme_tokens["font_color"]},
        },
    )

    for row_idx, (_series_map, y_title, _panel_title) in enumerate(panels, start=1):
        is_bottom = row_idx == n_rows
        fig.update_xaxes(
            showgrid=True,
            gridcolor=theme_tokens["grid_color"],
            linecolor=theme_tokens["axis_color"],
            tickfont={"color": theme_tokens["font_color"]},
            title_font={"color": theme_tokens["title_color"]},
            title_text="Timestamp" if is_bottom else "",
            showticklabels=is_bottom,
            row=row_idx, col=1,
        )
        fig.update_yaxes(
            title_text=y_title,
            showgrid=True,
            gridcolor=theme_tokens["grid_color"],
            linecolor=theme_tokens["axis_color"],
            tickfont={"color": theme_tokens["font_color"]},
            title_font={"color": theme_tokens["title_color"]},
            row=row_idx, col=1,
        )

    return fig


def to_event_frame(events: Iterable[tuple[pd.Timestamp, str]]) -> pd.DataFrame:
    """Convert reconstructed events into a table-friendly dataframe."""
    rows = []
    for timestamp, description in events:
        rows.append(
            {
                "timestamp": timestamp,
                "category": classify_event(description),
                "label": shorten_event_label(description),
                "event": description,
            }
        )
    return pd.DataFrame(rows)


def render_metrics(summary: dict[str, object]) -> None:
    """Render top-level summary metrics."""
    start_text = summary["start"].strftime("%Y-%m-%d %H:%M:%S") if summary["start"] is not None else "--"
    end_text = summary["end"].strftime("%Y-%m-%d %H:%M:%S") if summary["end"] is not None else "--"
    timezone_text = str(summary.get("timezone") or getattr(LOCAL_TIMEZONE, "tzname", lambda _dt: str(LOCAL_TIMEZONE))(None))
    latest_mode = str(summary.get("latest_mode") or "--")
    backend = str(summary.get("backend") or "--")
    safety_level = str(summary.get("safety_level") or "--")

    top_cols = st.columns(5)
    top_cols[0].metric("Samples", f"{summary['samples']:,}")
    top_cols[1].metric("Duration", f"{summary['duration_minutes']} min")
    top_cols[2].metric("Files", str(summary["files"]))
    top_cols[3].metric("Latest mode", latest_mode)
    top_cols[4].metric("Safety level", safety_level)

    bottom_cols = st.columns(4)
    bottom_cols[0].metric("Start", start_text)
    bottom_cols[1].metric("End", end_text)
    bottom_cols[2].metric("Timezone", timezone_text)
    bottom_cols[3].metric("Backend", backend)


def render_shutdown_controls() -> None:
    """Render a guarded control that terminates the local Streamlit process."""
    st.sidebar.divider()
    st.sidebar.subheader("Application")
    armed = st.sidebar.checkbox("Enable close button", value=False)
    close_clicked = st.sidebar.button(
        "Close application",
        type="secondary",
        disabled=not armed,
        use_container_width=True,
    )
    if close_clicked:
        st.sidebar.warning("Closing Streamlit application...")
        os._exit(0)


@st.cache_data(show_spinner=False)
def read_directory_logs(file_paths: tuple[str, ...], reload_token: int) -> tuple[pd.DataFrame, str | None, int]:
    """Read filesystem log files with cache invalidation support."""
    _ = reload_token
    return load_and_prepare_logs(list(file_paths))


@st.cache_data(show_spinner=False)
def read_uploaded_logs(file_blobs: tuple[tuple[str, bytes], ...], reload_token: int) -> tuple[pd.DataFrame, str | None, int]:
    """Read uploaded files through the shared data loader with cache invalidation support."""
    _ = reload_token
    sources = []
    for name, content in file_blobs:
        buffer = io.StringIO(content.decode("utf-8"))
        buffer.name = name
        sources.append(buffer)
    return load_and_prepare_logs(sources)


def discover_directory_files(directory: str, pattern: str) -> list[Path]:
    """Resolve matching CSV files from a local directory."""
    base = Path(directory).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"Directory not found: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {base}")
    return sorted(path for path in base.glob(pattern) if path.is_file())


def select_files_by_name(paths: list[Path], selected_names: list[str]) -> list[Path]:
    """Return files whose names are present in the current selection."""
    selected_set = set(selected_names)
    return [path for path in paths if path.name in selected_set]


def current_day_log_files(paths: list[Path]) -> list[Path]:
    """Return the selected files that belong to the current local day."""
    today = datetime.now().strftime("%Y-%m-%d")
    prefix = f"cryostat_environment_{today}"
    return [path for path in paths if path.stem.startswith(prefix)]


def current_day_uploaded_names(file_names: Iterable[str]) -> list[str]:
    """Return uploaded filenames that belong to the current local day."""
    today = datetime.now().strftime("%Y-%m-%d")
    prefix = f"cryostat_environment_{today}"
    return [name for name in file_names if Path(name).stem.startswith(prefix)]


def refreshable_current_day_paths(file_names: Iterable[str], directory: Path) -> list[Path]:
    """Resolve uploaded current-day filenames to local disk paths when available."""
    available = {path.name: path for path in discover_directory_files(str(directory), DEFAULT_LOG_PATTERN)}
    return [available[name] for name in current_day_uploaded_names(file_names) if name in available]


def render_auto_refresh(enabled: bool, interval_seconds: int) -> None:
    """Inject a timed page refresh for directory-watch mode."""
    if not enabled:
        return
    refresh_ms = max(1, interval_seconds) * 1000
    st.markdown(
        f"""
        <script>
        setTimeout(function() {{
            window.parent.location.reload();
        }}, {refresh_ms});
        </script>
        """,
        unsafe_allow_html=True,
    )


def init_session_value(key: str, value: object) -> None:
    """Initialize a Streamlit session value only once."""
    if key not in st.session_state:
        st.session_state[key] = value


def reset_ui_filters() -> None:
    """Restore the sidebar controls to their default values."""
    st.session_state["selected_theme"] = "Light"
    st.session_state["auto_refresh_seconds"] = 60
    st.session_state["show_event_markers"] = True
    st.session_state["selected_event_categories"] = list(EVENT_CATEGORY_ORDER)
    st.session_state["selected_temperature_keys"] = []
    st.session_state["selected_magnetics_keys"] = []
    st.session_state["selected_pressure_keys"] = []
    st.session_state["uploaded_selected_files"] = []


def main() -> None:
    init_session_value("selected_theme", "Light")
    init_session_value("auto_refresh_seconds", 60)
    init_session_value("show_event_markers", True)
    init_session_value("selected_event_categories", list(EVENT_CATEGORY_ORDER))
    init_session_value("selected_temperature_keys", [])
    init_session_value("selected_magnetics_keys", [])
    init_session_value("selected_pressure_keys", [])
    init_session_value("uploaded_selected_files", [])
    init_session_value("reload_counter", 0)

    with st.sidebar:
        st.header("Display")
        selected_theme = st.radio(
            "Theme",
            options=["Light", "Dark"],
            horizontal=True,
            key="selected_theme",
        )
        auto_refresh_seconds = 60
        st.caption("Current-day log refresh: every 60 s")

        if st.button("Reload selected files", use_container_width=True):
            read_uploaded_logs.clear()
            read_directory_logs.clear()
            st.session_state["reload_counter"] += 1
            st.rerun()

        st.button(
            "Reset filters",
            use_container_width=True,
            on_click=reset_ui_filters,
        )

        st.header("Trace Selection")
        st.caption("Use Plotly pan, zoom, and hover directly on the charts.")

    apply_app_theme(selected_theme)

    st.title("Cryostat Environment Log")
    st.caption("Local Streamlit + Plotly reader for cryostat environment CSV logs.")

    uploaded_files = st.file_uploader(
        "Browse one or more cryostat environment CSV files",
        type=["csv"],
        accept_multiple_files=True,
    )
    if not uploaded_files:
        st.info("Choose one or more CSV files to inspect the cryostat environment logs.")
        st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
        return

    try:
        uploaded_names = [uploaded_file.name for uploaded_file in uploaded_files]
        st.session_state["uploaded_selected_files"] = [
            name for name in st.session_state["uploaded_selected_files"] if name in uploaded_names
        ]
        if not st.session_state["uploaded_selected_files"] and uploaded_names:
            st.session_state["uploaded_selected_files"] = uploaded_names

        st.sidebar.multiselect(
            "Files to display",
            options=uploaded_names,
            key="uploaded_selected_files",
            help="Choose which uploaded CSV files to merge into the current view.",
        )

        selected_uploaded_files = [
            uploaded_file
            for uploaded_file in uploaded_files
            if uploaded_file.name in st.session_state["uploaded_selected_files"]
        ]
        if not selected_uploaded_files:
            st.warning("Select at least one file to display.")
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return

        refresh_paths = refreshable_current_day_paths(
            [uploaded_file.name for uploaded_file in selected_uploaded_files],
            DEFAULT_LOG_DIRECTORY,
        )
        refresh_names = {path.name for path in refresh_paths}
        static_upload_blobs = tuple(
            (uploaded_file.name, uploaded_file.getvalue())
            for uploaded_file in selected_uploaded_files
            if uploaded_file.name not in refresh_names
        )

        frames: list[pd.DataFrame] = []
        filtered_rows_total = 0
        time_col: str | None = None
        file_sources: list[Path | object] = []

        if static_upload_blobs:
            uploaded_df, uploaded_time_col, uploaded_filtered = read_uploaded_logs(
                static_upload_blobs,
                st.session_state["reload_counter"],
            )
            if not uploaded_df.empty:
                frames.append(uploaded_df)
                time_col = uploaded_time_col
            filtered_rows_total += uploaded_filtered
            file_sources.extend(
                uploaded_file
                for uploaded_file in selected_uploaded_files
                if uploaded_file.name not in refresh_names
            )

        if refresh_paths:
            refresh_df, refresh_time_col, refresh_filtered = read_directory_logs(
                tuple(str(path) for path in refresh_paths),
                st.session_state["reload_counter"],
            )
            if not refresh_df.empty:
                frames.append(refresh_df)
                time_col = time_col or refresh_time_col
            filtered_rows_total += refresh_filtered
            file_sources.extend(refresh_paths)

        if not frames:
            df = pd.DataFrame()
        elif len(frames) == 1:
            df = frames[0]
        else:
            df = pd.concat(frames, ignore_index=True, sort=False)
            if time_col and time_col in df.columns:
                df = df.sort_values(by=[time_col, "__source_file__"], kind="stable").reset_index(drop=True)
    except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
        st.error(str(exc))
        st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
        return
    except Exception as exc:
        st.error(f"Failed to read selected file(s): {exc}")
        st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
        return

    st.caption(f"Selected files: {len(selected_uploaded_files)} of {len(uploaded_files)}")

    if refresh_paths:
        current_names = ", ".join(path.name for path in refresh_paths)
        st.caption(
            f"Auto refresh active every {auto_refresh_seconds} s for current-day log on disk: `{current_names}`"
        )
        render_auto_refresh(True, auto_refresh_seconds)
    else:
        st.caption("Auto refresh is idle until the selected upload includes a current-day log also present in `data/`.")

    if filtered_rows_total > 0:
        st.warning(f"Ignored {filtered_rows_total} rows where `backend == 'mock'`.")

    if df.empty:
        st.warning("No data left to display after filtering. The selected file(s) may contain only mock data.")
        st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
        return

    if not time_col:
        st.error("No `timestamp` or `timestamp_iso` column found in the CSV.")
        st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
        return

    summary = summarize_log(df, time_col, file_sources)
    render_metrics(summary)

    with st.sidebar:
        temperature_series = available_series(df, TEMPERATURE_SERIES)
        magnetics_series = available_series(df, MAGNETICS_SERIES_LEFT)
        pressure_series = available_series(df, MAGNETICS_SERIES_RIGHT)
        if not st.session_state["selected_temperature_keys"]:
            st.session_state["selected_temperature_keys"] = list(temperature_series.keys())
        else:
            st.session_state["selected_temperature_keys"] = [
                key for key in st.session_state["selected_temperature_keys"] if key in temperature_series
            ]
        if not st.session_state["selected_magnetics_keys"]:
            st.session_state["selected_magnetics_keys"] = list(magnetics_series.keys())
        else:
            st.session_state["selected_magnetics_keys"] = [
                key for key in st.session_state["selected_magnetics_keys"] if key in magnetics_series
            ]
        if not st.session_state["selected_pressure_keys"]:
            st.session_state["selected_pressure_keys"] = list(pressure_series.keys())
        else:
            st.session_state["selected_pressure_keys"] = [
                key for key in st.session_state["selected_pressure_keys"] if key in pressure_series
            ]
        st.session_state["selected_event_categories"] = [
            key for key in st.session_state["selected_event_categories"] if key in EVENT_CATEGORY_ORDER
        ] or list(EVENT_CATEGORY_ORDER)

        show_event_markers = st.checkbox("Show event markers", key="show_event_markers")
        event_category_options = list(EVENT_CATEGORY_ORDER)
        selected_event_categories = st.multiselect(
            "Event categories",
            options=event_category_options,
            key="selected_event_categories",
        )

        selected_temperature_keys = st.multiselect(
            "Temperatures",
            options=list(temperature_series.keys()),
            key="selected_temperature_keys",
            format_func=lambda key: temperature_series[key]["label"],
        )
        selected_magnetics_keys = st.multiselect(
            "Magnetics",
            options=list(magnetics_series.keys()),
            key="selected_magnetics_keys",
            format_func=lambda key: magnetics_series[key]["label"],
        )
        selected_pressure_keys = st.multiselect(
            "Pressure / Needle",
            options=list(pressure_series.keys()),
            key="selected_pressure_keys",
            format_func=lambda key: pressure_series[key]["label"],
        )

    render_shutdown_controls()

    selected_temperature = {key: temperature_series[key] for key in selected_temperature_keys}
    selected_magnetics = {key: magnetics_series[key] for key in selected_magnetics_keys}
    selected_pressure = {key: pressure_series[key] for key in selected_pressure_keys}

    events = build_event_list(df, time_col)
    events_df = to_event_frame(events)
    if not events_df.empty:
        events_df = events_df[events_df["category"].isin(selected_event_categories)].reset_index(drop=True)

    overview_tab, events_tab, raw_data_tab = st.tabs(["Overview", "Events", "Raw Data"])

    with overview_tab:
        panels: list[tuple[dict, str, str]] = []
        if selected_temperature:
            panels.append((selected_temperature, "Temperature (K)", "Temperatures"))
        if selected_magnetics:
            panels.append((selected_magnetics, "Field / Current / Voltage", "Magnetics"))
        if selected_pressure:
            panels.append((selected_pressure, "Pressure / Needle", "Pressure and Needle Valve"))

        if panels:
            if show_event_markers and not events_df.empty:
                st.caption("The lower rail marks reconstructed events by category color. Hover a marker to see what happened.")
            elif show_event_markers and not selected_event_categories:
                st.caption("Select at least one event category in the sidebar to show markers.")
            st.plotly_chart(
                build_overview_subplot(
                    df,
                    time_col,
                    panels,
                    selected_theme,
                    events_df,
                    show_event_markers,
                ),
                use_container_width=True,
                key="overview_chart",
            )
        if not selected_temperature:
            st.info("No temperature traces selected or available.")
        if not selected_magnetics:
            st.info("No magnetic traces selected or available.")
        if not selected_pressure:
            st.info("No pressure or needle-valve traces selected or available.")
        if not panels:
            st.info("No traces selected. Use the sidebar to select at least one series.")

    with events_tab:
        if events_df.empty:
            if selected_event_categories:
                st.info("No reconstructable events found in the selected data for the active category filter.")
            else:
                st.info("Select at least one event category in the sidebar to list events.")
        else:
            st.caption("Use `category` for quick scanning and `event` for the full reconstructed description.")
            st.dataframe(events_df, use_container_width=True, hide_index=True)

    with raw_data_tab:
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")


if __name__ == "__main__":
    main()
