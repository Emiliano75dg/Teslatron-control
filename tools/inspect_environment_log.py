from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy entry point for the cryostat environment log inspector. "
            "The Matplotlib viewer has been replaced by a local Streamlit app."
        )
    )
    parser.add_argument(
        "file_paths",
        nargs="*",
        help=(
            "Optional CSV paths. Upload them from the Streamlit UI after launch; "
            "they are not consumed directly by this wrapper."
        ),
    )
    parser.parse_args()

    streamlit_path = Path(__file__).with_name("inspect_environment_log_streamlit.py")
    print("The Matplotlib log inspector has been retired.")
    print("Use the Streamlit app instead:")
    print()
    print(f"  streamlit run {streamlit_path}")


if __name__ == "__main__":
    main()
