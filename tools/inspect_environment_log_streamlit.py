from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

THIS_DIR = Path(__file__).resolve().parent
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


def build_line_chart(
    df: pd.DataFrame,
    time_col: str,
    series_map: dict[str, dict[str, str]],
    title: str,
    y_title: str,
    theme: str,
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
    return fig


def to_event_frame(events: Iterable[tuple[pd.Timestamp, str]]) -> pd.DataFrame:
    """Convert reconstructed events into a table-friendly dataframe."""
    rows = [{"timestamp": timestamp, "event": description} for timestamp, description in events]
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
def read_uploaded_logs(file_blobs: tuple[tuple[str, bytes], ...], reload_token: int) -> tuple[pd.DataFrame, str | None, int]:
    """Read uploaded files through the shared data loader with cache invalidation support."""
    sources = []
    for name, content in file_blobs:
        buffer = io.BytesIO(content)
        buffer.name = name
        sources.append(buffer)
    return load_and_prepare_logs(sources)


@st.cache_data(show_spinner=False)
def read_directory_logs(file_paths: tuple[str, ...], reload_token: int) -> tuple[pd.DataFrame, str | None, int]:
    """Read filesystem log files with cache invalidation support."""
    _ = reload_token
    return load_and_prepare_logs(list(file_paths))


def discover_directory_files(directory: str, pattern: str) -> list[Path]:
    """Resolve matching CSV files from a local directory."""
    base = Path(directory).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"Directory not found: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {base}")
    return sorted(path for path in base.glob(pattern) if path.is_file())


def select_watch_files(paths: list[Path], selection_mode: str) -> list[Path]:
    """Choose either all matched files or only the most recently updated one."""
    if not paths:
        return []
    if selection_mode == "Only latest file":
        latest = max(paths, key=lambda path: (path.stat().st_mtime, path.name))
        return [latest]
    return paths


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


def main() -> None:
    with st.sidebar:
        st.header("Display")
        selected_theme = st.radio(
            "Theme",
            options=["Light", "Dark"],
            horizontal=True,
        )
        source_mode = st.radio(
            "Source",
            options=["Directory watch", "Manual upload"],
            index=0,
        )
        if "reload_counter" not in st.session_state:
            st.session_state["reload_counter"] = 0
        auto_refresh_enabled = False
        auto_refresh_seconds = 5
        watch_directory = ""
        watch_pattern = "cryostat_environment_*.csv"
        watch_selection_mode = "Merge all matched files"

        if source_mode == "Directory watch":
            watch_directory = st.text_input("Log directory", value=str((THIS_DIR.parent / "data").resolve()))
            watch_pattern = st.text_input("File pattern", value="cryostat_environment_*.csv")
            watch_selection_mode = st.radio(
                "Matched files",
                options=["Merge all matched files", "Only latest file"],
                index=0,
            )
            auto_refresh_enabled = st.checkbox("Auto refresh", value=True)
            auto_refresh_seconds = int(
                st.number_input("Refresh every (s)", min_value=1, max_value=300, value=5, step=1)
            )

        reload_label = "Reload watched files" if source_mode == "Directory watch" else "Reload uploaded files"
        if st.button(reload_label, use_container_width=True):
            read_uploaded_logs.clear()
            read_directory_logs.clear()
            st.session_state["reload_counter"] += 1
            st.rerun()

        st.header("Trace Selection")
        st.caption("Use Plotly pan, zoom, and hover directly on the charts.")

    apply_app_theme(selected_theme)

    st.title("Cryostat Environment Log")
    st.caption("Local Streamlit + Plotly reader for cryostat environment CSV logs.")

    file_sources: list[Path] | list[object]
    if source_mode == "Directory watch":
        try:
            matched_files = discover_directory_files(watch_directory, watch_pattern)
            selected_watch_files = select_watch_files(matched_files, watch_selection_mode)
            file_sources = selected_watch_files
            file_paths = tuple(str(path) for path in selected_watch_files)
            df, time_col, filtered_rows_total = read_directory_logs(file_paths, st.session_state["reload_counter"])
        except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
            st.error(str(exc))
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return
        except Exception as exc:
            st.error(f"Failed to read watched file(s): {exc}")
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return

        st.caption(f"Watching `{watch_directory}` with pattern `{watch_pattern}`")
        st.caption(
            f"Mode: {watch_selection_mode}"
            + (
                f" ({selected_watch_files[0].name})"
                if watch_selection_mode == "Only latest file" and selected_watch_files
                else ""
            )
        )
        if auto_refresh_enabled:
            st.caption(f"Auto refresh enabled: every {auto_refresh_seconds} s")
            render_auto_refresh(True, auto_refresh_seconds)

        if not matched_files:
            st.warning("No files matched the current directory/pattern selection.")
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return
    else:
        uploaded_files = st.file_uploader(
            "Upload one or more cryostat environment CSV files",
            type=["csv"],
            accept_multiple_files=True,
        )

        if not uploaded_files:
            st.info("Upload one or more CSV files to inspect merged cryostat environment logs.")
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return

        try:
            file_blobs = tuple((uploaded_file.name, uploaded_file.getvalue()) for uploaded_file in uploaded_files)
            df, time_col, filtered_rows_total = read_uploaded_logs(file_blobs, st.session_state["reload_counter"])
            file_sources = list(uploaded_files)
        except ValueError as exc:
            st.error(str(exc))
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return
        except Exception as exc:
            st.error(f"Failed to read CSV file(s): {exc}")
            st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")
            return

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

        selected_temperature_keys = st.multiselect(
            "Temperatures",
            options=list(temperature_series.keys()),
            default=list(temperature_series.keys()),
            format_func=lambda key: temperature_series[key]["label"],
        )
        selected_magnetics_keys = st.multiselect(
            "Magnetics",
            options=list(magnetics_series.keys()),
            default=list(magnetics_series.keys()),
            format_func=lambda key: magnetics_series[key]["label"],
        )
        selected_pressure_keys = st.multiselect(
            "Pressure / Needle",
            options=list(pressure_series.keys()),
            default=list(pressure_series.keys()),
            format_func=lambda key: pressure_series[key]["label"],
        )

    render_shutdown_controls()

    selected_temperature = {key: temperature_series[key] for key in selected_temperature_keys}
    selected_magnetics = {key: magnetics_series[key] for key in selected_magnetics_keys}
    selected_pressure = {key: pressure_series[key] for key in selected_pressure_keys}

    events = build_event_list(df, time_col)
    events_df = to_event_frame(events)

    overview_tab, events_tab, raw_data_tab = st.tabs(["Overview", "Events", "Raw Data"])

    with overview_tab:
        if selected_temperature:
            st.plotly_chart(
                build_line_chart(df, time_col, selected_temperature, "Temperatures", "Temperature (K)", selected_theme),
                use_container_width=True,
            )
        else:
            st.info("No temperature traces selected or available.")

        if selected_magnetics:
            st.plotly_chart(
                build_line_chart(
                    df,
                    time_col,
                    selected_magnetics,
                    "Magnetics",
                    "Field / Current / Voltage",
                    selected_theme,
                ),
                use_container_width=True,
            )
        else:
            st.info("No magnetic traces selected or available.")

        if selected_pressure:
            st.plotly_chart(
                build_line_chart(
                    df,
                    time_col,
                    selected_pressure,
                    "Pressure and Needle Valve",
                    "Pressure / Needle",
                    selected_theme,
                ),
                use_container_width=True,
            )
        else:
            st.info("No pressure or needle-valve traces selected or available.")

    with events_tab:
        if events_df.empty:
            st.info("No reconstructable events found in the selected data.")
        else:
            st.dataframe(events_df, use_container_width=True, hide_index=True)

    with raw_data_tab:
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.caption("CLI: `streamlit run inspect_environment_log_streamlit.py`")


if __name__ == "__main__":
    main()
