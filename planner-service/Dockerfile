FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch installed separately from the CPU wheel index (keeps the
# image lean — no CUDA). best.pt was trained on GPU but loads fine on CPU.
RUN pip install torch==2.5.0 --index-url https://download.pytorch.org/whl/cpu

COPY planner-service/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY planner-service/pathformer ./pathformer
COPY shared ./shared
COPY planner-service/app ./app

# best.pt is mounted as volume at /app/weights/best.pt (see docker-compose.yml)
RUN mkdir -p /app/weights

ENV PYTHONPATH=/app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
