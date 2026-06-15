from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

import uvicorn

from .cryostat.api import create_app
from .cryostat.config import load_config

REPO_ROOT = Path(__file__).resolve().parents[1]

PROFILE_DEFAULTS: dict[str, tuple[str, int]] = {
    "mock": ("config/cryostat_mock.json", 8765),
    "readonly": ("config/cryostat_lab_readonly.json", 8765),
    "lab": ("config/cryostat_lab_readonly.json", 8765),
    "control": ("config/cryostat_lab_control.json", 8766),
    "heliox": ("config/heliox_local_gui.example.json", 8767),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Start the Teslatron cryostat GUI with a simple command. "
            "If you do not pick a profile, a safe mock session is started."
        )
    )
    parser.add_argument(
        "profile",
        nargs="?",
        default="mock",
        choices=sorted(PROFILE_DEFAULTS),
        help="Startup profile: mock, readonly, lab, control, or heliox.",
    )
    parser.add_argument(
        "--config",
        help="Use a specific config file instead of one of the built-in profiles.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, help="Port to bind. Default depends on the profile.")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the GUI automatically in the default browser.",
    )
    return parser


def resolve_launch_settings(args: argparse.Namespace) -> tuple[Path, int]:
    config_name, default_port = PROFILE_DEFAULTS[args.profile]
    config_path = REPO_ROOT / (args.config or config_name)
    return config_path, args.port or default_port


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config_path, port = resolve_launch_settings(args)
    if not config_path.exists():
        parser.exit(
            2,
            f"Config file not found: {config_path}\n"
            "Tip: reinstall the launcher from this repository or use --config with a valid path.\n",
        )

    url = f"http://{args.host}:{port}/"
    print("Starting Teslatron GUI")
    print(f"Profile : {args.profile}")
    print(f"Config  : {config_path}")
    print(f"Open    : {url}")
    print("Press Ctrl+C to stop the service.")

    if args.open_browser:
        webbrowser.open(url)

    try:
        os.chdir(REPO_ROOT)
        config = load_config(str(config_path))
        uvicorn.run(create_app(config), host=args.host, port=port)
    except KeyboardInterrupt:
        print("\nTeslatron stopped.")
    except Exception as exc:
        print(f"Could not start Teslatron: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
