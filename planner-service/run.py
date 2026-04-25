"""Local entry point for planner-service.

Run with: python run.py

For production/Docker, uvicorn is invoked directly via the Dockerfile CMD
(`uvicorn src.main:app ...`). This script is a convenience wrapper for
local development — it makes the start command discoverable and keeps
host/port in one obvious place.

Environment variables (MinIO, RabbitMQ, WEIGHTS_PATH, etc.) are read from
a local `.env` file via src/core/config.py. See .env.example for the
full list.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
