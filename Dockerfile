# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The package (and its bundled ZIP reference) is self-contained, so no build step.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Usage:
#   docker run --rm -v "$PWD:/data" assay check /data/batch.ndjson --schema /data/schema.json
ENTRYPOINT ["assay"]
CMD ["--help"]
