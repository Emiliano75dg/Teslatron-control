import argparse
import os
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyBboxPatch
from matplotlib.widgets import Button, CheckButtons

# Define the series to plot, similar to the web GUI
TEMPERATURE_SERIES = {
    "sample_temperature_K": {"label": "Sample (K)", "color": "#c0392b"},
    "vti_temperature_K": {"label": "VTI (K)", "color": "#1d4e89"},
    "magnet_temperature_K": {"label": "Magnet (K)", "color": "#2a9d8f"},
    "pt1_temperature_K": {"label": "PT1 (K)", "color": "#3a86ff"},
    "pt2_temperature_K": {"label": "PT2 (K)", "color": "#f4a261"},
}

MAGNETICS_SERIES_LEFT = {
    "B_T": {"label": "Field (T)", "color": "#2b9348"},
    "field_output_current_A": {"label": "Current (A)", "color": "#386fa4"},
    "field_output_voltage_V": {"label": "Voltage (V)", "color": "#d0006f"},
}

MAGNETICS_SERIES_RIGHT = {
    "pressure_mbar": {"label": "Pressure (mbar)", "color": "#bc6c25"},
    "needle_valve_percent": {"label": "Needle (%)", "color": "#7b2cbf"},
}

FIGURE_BG = "#f4f7fb"
PANEL_BG = "#ffffff"
PANEL_EDGE = "#c7d3df"
GRID_COLOR = "#cfd8e3"
TEXT_PRIMARY = "#16324f"
TEXT_MUTED = "#5f748c"
BUTTON_BG = "#d9e8f5"
BUTTON_HOVER = "#c5dbef"
ACCENT = "#2a6f97"
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo


def apply_plot_theme():
    """Apply a consistent instrument-style theme."""
    plt.style.use('default')
    plt.rcParams.update({
        'figure.facecolor': FIGURE_BG,
        'axes.facecolor': PANEL_BG,
        'axes.edgecolor': PANEL_EDGE,
        'axes.labelcolor': TEXT_PRIMARY,
        'axes.titlecolor': TEXT_PRIMARY,
        'axes.titleweight': 'bold',
        'axes.linewidth': 1.0,
        'xtick.color': TEXT_MUTED,
        'ytick.color': TEXT_MUTED,
        'text.color': TEXT_PRIMARY,
        'grid.color': GRID_COLOR,
        'grid.alpha': 0.85,
        'grid.linestyle': ':',
        'font.size': 10.5,
        'legend.frameon': False,
        'savefig.facecolor': FIGURE_BG,
    })


def style_axis_panel(ax):
    """Give each subplot a card-like instrument panel look."""
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_color(PANEL_EDGE)
        spine.set_linewidth(1.1)
    ax.tick_params(colors=TEXT_MUTED, labelsize=8.8, pad=4)
    ax.title.set_fontsize(13)
    ax.title.set_weight('bold')
    ax.grid(True, which='major', linewidth=0.8)


def autoscale_visible_lines(*axes):
    """Autoscale each axes based only on currently visible lines."""
    for ax in axes:
        try:
            ax.relim(visible_only=True)
        except TypeError:
            ax.relim()
        if any(line.get_visible() for line in ax.lines):
            ax.autoscale_view()


def add_panel_glow(fig, ax):
    """Draw a rounded panel behind an axes for separation."""
    pos = ax.get_position()
    panel = FancyBboxPatch(
        (pos.x0 - 0.010, pos.y0 - 0.014),
        pos.width + 0.020,
        pos.height + 0.028,
        boxstyle="round,pad=0.01,rounding_size=0.02",
        transform=fig.transFigure,
        linewidth=1.0,
        edgecolor=PANEL_EDGE,
        facecolor="#fbfdff",
        alpha=1.0,
        zorder=-5,
    )
    fig.patches.append(panel)


def add_overview_header(fig, file_path, df, time_col):
    """Add a dashboard-like header with log metadata."""
    start_ts = df[time_col].min()
    end_ts = df[time_col].max()
    duration = end_ts - start_ts
    minutes = int(duration.total_seconds() // 60) if pd.notna(duration) else 0
    timezone_label = getattr(LOCAL_TIMEZONE, "tzname", lambda _dt: str(LOCAL_TIMEZONE))(None)

    fig.text(0.055, 0.968, "Cryostat Environment Log",
             fontsize=16.0, fontweight='bold', color=TEXT_PRIMARY,
             ha='left', va='top')
    fig.text(
        0.055, 0.946,
        os.path.basename(file_path),
        fontsize=9.6,
        color=ACCENT,
        ha='left',
        va='top',
    )
    fig.text(
        0.39, 0.968,
        f"Samples: {len(df):,}",
        fontsize=9.0,
        color=TEXT_MUTED,
        ha='left',
        va='top',
    )
    fig.text(
        0.52, 0.968,
        f"Window: {minutes} min",
        fontsize=9.0,
        color=TEXT_MUTED,
        ha='left',
        va='top',
    )
    fig.text(
        0.66, 0.968,
        f"TZ: {timezone_label}",
        fontsize=9.0,
        color=TEXT_MUTED,
        ha='left',
        va='top',
    )
    fig.text(
        0.39, 0.946,
        f"From: {start_ts:%Y-%m-%d %H:%M}",
        fontsize=9.0,
        color=TEXT_MUTED,
        ha='left',
        va='top',
    )
    fig.text(
        0.66, 0.946,
        f"To: {end_ts:%Y-%m-%d %H:%M}",
        fontsize=9.0,
        color=TEXT_MUTED,
        ha='left',
        va='top',
    )


def normalize_time_data(series):
    """
    Normalize timestamps to the local machine timezone for plotting.
    Logs are written in UTC, so we convert explicitly for display.
    """
    if pd.api.types.is_numeric_dtype(series):
        parsed = pd.to_datetime(series, unit='s', utc=True)
    else:
        parsed = pd.to_datetime(series)
        if getattr(parsed.dt, 'tz', None) is None:
            parsed = parsed.dt.tz_localize('UTC')
        else:
            parsed = parsed.dt.tz_convert('UTC')
    return parsed.dt.tz_convert(LOCAL_TIMEZONE)


def add_header_button(fig, on_click, label='Load New File'):
    """Place the main action button in the top-right header area."""
    ax_button = fig.add_axes([0.84, 0.943, 0.12, 0.034])
    button = Button(ax_button, label)
    button.on_clicked(on_click)
    style_button(ax_button, button)
    return button


def style_button(button_ax, button):
    """Style action buttons to match the dashboard palette."""
    button_ax.set_facecolor(BUTTON_BG)
    for spine in button_ax.spines.values():
        spine.set_edgecolor(PANEL_EDGE)
        spine.set_linewidth(1.0)
    button.label.set_color(TEXT_PRIMARY)
    button.label.set_fontweight('bold')
    button.color = BUTTON_BG
    button.hovercolor = BUTTON_HOVER


def style_checkbuttons(check_buttons, all_series):
    """
    Style CheckButtons using the public Matplotlib API so this keeps working
    across versions where internal widget attributes may differ.
    """
    check_buttons.ax.set_facecolor(PANEL_BG)
    for spine in check_buttons.ax.spines.values():
        spine.set_color(PANEL_EDGE)
        spine.set_linewidth(1.0)

    if hasattr(check_buttons, 'set_frame_props'):
        check_buttons.set_frame_props({
            'facecolor': '#eef4fa',
            'edgecolor': PANEL_EDGE,
        })

    if hasattr(check_buttons, 'set_check_props'):
        check_buttons.set_check_props({'color': TEXT_PRIMARY})

    for label in check_buttons.labels:
        label_text = label.get_text()
        label.set_color(TEXT_PRIMARY)
        label.set_fontsize(8.2)
        label.set_horizontalalignment('left')
        label.set_x(0.31)
        for props in all_series.values():
            if props['label'] == label_text:
                label.set_color(props['color'])
                break

def inspect_log(file_path: str):
    """
    Reads a cryostat environment log CSV file and plots the data in two graphs
    for inspection, similar to the Teslatron web interface.
    """
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        print(f"Error: File not found at '{file_path}'")
        return
    except Exception as e:
        print(f"Error reading CSV file: {e}")
        return

    # --- Filter out mock data ---
    if 'backend' in df.columns:
        original_rows = len(df)
        # Keep only rows where the backend is not 'mock'
        df = df[df['backend'] != 'mock'].copy()
        rows_removed = original_rows - len(df)
        if rows_removed > 0:
            print(f"Info: Ignored {rows_removed} rows where backend was 'mock'.")

    # If all data was filtered out, show a message instead of crashing.
    if df.empty:
        print("Warning: No data left to plot after filtering. The file may contain only mock data.")
        apply_plot_theme()
        fig, ax = plt.subplots(figsize=(15, 10))
        fig.patch.set_facecolor(FIGURE_BG)
        style_axis_panel(ax)
        ax.text(0.5, 0.5, 'No data to display.\nThe selected file may contain only mock data.',
                horizontalalignment='center', verticalalignment='center',
                transform=ax.transAxes, color=TEXT_PRIMARY, fontsize=16, fontweight='bold')
        ax.set_xticks([])
        ax.set_yticks([])
        fig.text(0.055, 0.965, "Cryostat Environment Log", fontsize=16.5, fontweight='bold', color=TEXT_PRIMARY)
        fig.text(0.25, 0.965, os.path.basename(file_path), fontsize=10.0, color=ACCENT)
        add_panel_glow(fig, ax)

        # --- Button to load new file ---
        def _load_new_file_callback(event):
            plt.close(fig)

        button = add_header_button(fig, _load_new_file_callback)
        fig._button = button
        plt.show()
        return

    # --- Time Axis ---
    # Find the timestamp column and convert it to datetime
    time_col = None
    if 'timestamp_iso' in df.columns:
        time_col = 'timestamp_iso'
    elif 'timestamp' in df.columns:
        time_col = 'timestamp'
    
    if time_col:
        try:
            df[time_col] = normalize_time_data(df[time_col])
        except Exception as e:
            print(f"Error converting timestamp column '{time_col}': {e}")
            return
    else:
        print("Error: No 'timestamp' or 'timestamp_iso' column found in the CSV.")
        return

    # --- Plotting ---
    apply_plot_theme()
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(15.5, 9.8), sharex=True,
        gridspec_kw={'height_ratios': [1, 1], 'hspace': 0.18},
    )
    fig.patch.set_facecolor(FIGURE_BG)

    # --- Plot 1: Temperatures ---
    ax1.set_title('Temperatures')
    ax1.set_ylabel('Temperature (K)')
    ax1.yaxis.labelpad = 8
    style_axis_panel(ax1)

    lines_by_label_ax1 = {}
    labels_ax1 = []
    for key, props in TEMPERATURE_SERIES.items():
        if key in df.columns:
            line, = ax1.plot(
                df[time_col], df[key],
                label=props['label'],
                color=props['color'],
                linewidth=2.1,
                alpha=0.95,
                solid_capstyle='round',
            )
            lines_by_label_ax1[props['label']] = line
            labels_ax1.append(props['label'])

    # --- Plot 2: Magnetics & Pressure (with dual Y-axis) ---
    ax2.set_title('Magnetics & Pressure')
    ax2.set_ylabel('Field / Current / Voltage')
    ax2.yaxis.labelpad = 8
    style_axis_panel(ax2)
    
    lines_by_label_ax2 = {}
    labels_ax2 = []
    for key, props in MAGNETICS_SERIES_LEFT.items():
        if key in df.columns:
            line, = ax2.plot(
                df[time_col], df[key],
                label=props['label'],
                color=props['color'],
                linewidth=2.0,
                alpha=0.95,
                solid_capstyle='round',
            )
            lines_by_label_ax2[props['label']] = line
            labels_ax2.append(props['label'])

    ax2.tick_params(axis='y', colors=TEXT_MUTED, labelsize=8.6, pad=3)

    # Create a second Y-axis for pressure and needle valve
    ax3 = ax2.twinx()
    ax3.set_ylabel('Pressure / Needle')
    ax3.yaxis.labelpad = 8
    ax3.set_facecolor('none')
    for spine in ax3.spines.values():
        spine.set_color(PANEL_EDGE)
        spine.set_linewidth(1.1)

    for key, props in MAGNETICS_SERIES_RIGHT.items():
        if key in df.columns:
            line, = ax3.plot(
                df[time_col], df[key],
                label=props['label'],
                color=props['color'],
                linewidth=2.0,
                linestyle=(0, (5, 3)),
                alpha=0.95,
                solid_capstyle='round',
            )
            lines_by_label_ax2[props['label']] = line
            labels_ax2.append(props['label'])
    
    ax3.tick_params(axis='y', colors=TEXT_MUTED, labelsize=8.6, pad=3)

    # --- Final Touches ---
    add_overview_header(fig, file_path, df, time_col)

    # Format the x-axis to show dates and times nicely
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    formatter = mdates.ConciseDateFormatter(locator, tz=LOCAL_TIMEZONE)
    formatter.formats = ['%Y', '%b', '%d %b', '%H:%M', '%H:%M', '%H:%M:%S']
    formatter.zero_formats = ['%Y', '%b', '%d %b', '%H:%M', '%H:%M', '%H:%M:%S']
    ax2.xaxis.set_major_locator(locator)
    ax2.xaxis.set_major_formatter(formatter)
    ax2.set_xlabel('Timestamp')
    ax2.xaxis.labelpad = 8
    # Adjust layout to make room for suptitle, button, and legends
    fig.subplots_adjust(left=0.09, right=0.79, bottom=0.11, top=0.88)
    add_panel_glow(fig, ax1)
    add_panel_glow(fig, ax2)

    # --- Checkbox Legends ---
    # Combine all series for color lookup
    ALL_SERIES = {**TEMPERATURE_SERIES, **MAGNETICS_SERIES_LEFT, **MAGNETICS_SERIES_RIGHT}

    # Checkboxes for Temperature plot
    fig.text(0.85, 0.845, "Temperatures", fontsize=8.5, color=TEXT_MUTED, fontweight='bold', ha='left')
    ax_check1 = fig.add_axes([0.848, 0.60, 0.12, 0.16])
    check1 = CheckButtons(
        ax=ax_check1,
        labels=labels_ax1,
        actives=[True] * len(labels_ax1),
    )
    # Style checkboxes for dark theme
    style_checkbuttons(check1, ALL_SERIES)

    def toggle_vis_ax1(label):
        line = lines_by_label_ax1[label]
        line.set_visible(not line.get_visible())
        autoscale_visible_lines(ax1)
        fig.canvas.draw_idle()

    check1.on_clicked(toggle_vis_ax1)
    fig._check1 = check1

    # Checkboxes for Magnetics plot
    fig.text(0.85, 0.41, "Magnetics", fontsize=8.5, color=TEXT_MUTED, fontweight='bold', ha='left')
    ax_check2 = fig.add_axes([0.848, 0.165, 0.12, 0.16])
    check2 = CheckButtons(
        ax=ax_check2,
        labels=labels_ax2,
        actives=[True] * len(labels_ax2),
    )
    # Style checkboxes for dark theme
    style_checkbuttons(check2, ALL_SERIES)

    def toggle_vis_ax2(label):
        line = lines_by_label_ax2[label]
        line.set_visible(not line.get_visible())
        autoscale_visible_lines(ax2, ax3)
        fig.canvas.draw_idle()

    check2.on_clicked(toggle_vis_ax2)
    fig._check2 = check2

    # --- Button to load new file ---
    # The button click simply closes the window. The main script loop will then
    # prompt for a new file to be opened.
    def _load_new_file_callback(event):
        plt.close(fig)

    button = add_header_button(fig, _load_new_file_callback)
    # We need to keep a reference to the button, otherwise it gets garbage collected
    fig._button = button

    plt.show()

def get_file_path_interactively():
    """Opens a file dialog to select a CSV file."""
    try:
        # Import tkinter here to keep it optional
        from tkinter import Tk, filedialog

        root = Tk()
        root.withdraw()  # Hide the main window
        file_path = filedialog.askopenfilename(
            title="Select a cryostat environment log file",
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        return file_path
    except ImportError:
        print("Could not import tkinter for file dialog.")
        print("Please provide the file path as a command-line argument.")
        return None
    except Exception as e:
        # This can happen on systems without a display (e.g., SSH without -X)
        print(f"Could not open file dialog: {e}")
        print("Please provide the file path as a command-line argument.")
        return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Inspect a Teslatron environment log CSV file by plotting its data.'
    )
    parser.add_argument(
        'file_path',
        nargs='?',
        default=None,
        type=str,
        help='Optional path to the environment CSV log file. If not provided, a file dialog will open.'
    )
    args = parser.parse_args()

    # If a file path is provided via command line, process it once and exit.
    if args.file_path:
        inspect_log(args.file_path)
    else:
        # Interactive mode: loop until the user cancels the file dialog.
        while True:
            file_to_inspect = get_file_path_interactively()
            if file_to_inspect:
                inspect_log(file_to_inspect)
            else:
                # User cancelled the dialog or an error occurred
                print("No file selected or dialog was cancelled. Exiting.")
                break
