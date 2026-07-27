"""
Entry point: FastAPI + static web UI.
"""

import os

import uvicorn

from ducktrack.server import create_app


def main():
    app = create_app()
    port = int(os.environ.get("DUCKTRACK_BACKEND_PORT", "18765"))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
