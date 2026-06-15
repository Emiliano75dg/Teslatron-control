from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STREAMLIT_APP = REPO_ROOT / "tools" / "inspect_environment_log_streamlit.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the Teslatron environment log reader in Streamlit."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8501, help="Port to bind. Default: 8501")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not STREAMLIT_APP.exists():
        parser.exit(2, f"Log viewer script not found: {STREAMLIT_APP}\n")

    print("Starting Teslatron log reader")
    print(f"Script : {STREAMLIT_APP}")
    print(f"Open   : http://{args.host}:{args.port}/")
    print("Press Ctrl+C to stop the application.")

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(STREAMLIT_APP),
        "--server.address",
        args.host,
        "--server.port",
        str(args.port),
    ]
    try:
        raise SystemExit(subprocess.call(command, cwd=str(REPO_ROOT)))
    except KeyboardInterrupt:
        print("\nTeslatron log reader stopped.")
    except FileNotFoundError as exc:
        parser.exit(1, f"Could not start Streamlit: {exc}\n")
    except Exception as exc:
        parser.exit(
            1,
            "Could not start the Teslatron log reader. "
            "Make sure `streamlit` is installed in this Python environment.\n"
            f"Details: {exc}\n",
        )


if __name__ == "__main__":
    main()
