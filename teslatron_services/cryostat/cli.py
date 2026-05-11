from __future__ import annotations

import argparse

from .api import create_app
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Teslatron cryostat service for the Q-MAT lab system."
    )
    parser.add_argument("--config", default="config/cryostat_lab_readonly.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    config = load_config(args.config)
    uvicorn.run(create_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
