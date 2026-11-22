# Scanner app for the Jetson Nano (arm64). Also builds on x86 PCs for testing.
# All Python packages have prebuilt arm64 wheels, so nothing compiles here.
FROM python:3.12-slim-bookworm

# v4l-utils: v4l2-ctl for camera focus/sharpness   libglib2.0-0: needed by OpenCV
RUN apt-get update \
 && apt-get install -y --no-install-recommends v4l-utils libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY scanner/ scanner/
COPY tests/ tests/
COPY references/ references/

ENV DATA_DIR=/data \
    HOST=127.0.0.1 \
    PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "scanner"]
