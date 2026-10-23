# Records the README media (docs/media) from made-up logs, the same way on every machine.
#   docker build -f demo/Dockerfile -t tke-demo . && docker run --rm -v "$PWD/docs/media:/out" tke-demo
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --break-system-packages playwright==1.48.0
WORKDIR /app
COPY tke.py pyproject.toml README.md ./
COPY token_economy ./token_economy
COPY demo ./demo
ENV PYTHONDONTWRITEBYTECODE=1 TZ=UTC
CMD ["python3", "demo/record.py", "/out"]
