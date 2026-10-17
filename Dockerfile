FROM python:3.12-slim

# git est une dépendance d'exécution : les espaces sont des dépôts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssh-client \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LOREMD_SPACES__ROOT=/data/spaces

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[embeddings]"

RUN useradd --uid 10001 --create-home loremd && mkdir -p /data/spaces && chown -R loremd /data
USER loremd

# Les commits d'agents passent par -c user.name/-c user.email ; ceci n'est qu'un filet.
RUN git config --global user.name "LoreMd" \
 && git config --global user.email "loremd@localhost" \
 && git config --global --add safe.directory '*'

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["loremd", "serve"]
