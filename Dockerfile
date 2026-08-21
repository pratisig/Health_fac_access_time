# Dockerfile — Hugging Face Spaces (SDK « docker »).
#
# HF Spaces expose le port 7860 et exécute le conteneur avec l'utilisateur 1000.
# Un volume persistant est monté sur /data lorsqu'il est activé dans les
# réglages du Space : le cache WorldPop et les isochrones y survivent aux
# redémarrages.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HEALTH_ACCESS_CACHE_DIR=/data/cache

# GDAL/PROJ sont fournis par les roues binaires de rasterio et pyogrio ; seules
# les bibliothèques de compression et les certificats restent nécessaires.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/cache && chown -R app:app /data

WORKDIR /home/app
COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD curl -fsS http://localhost:7860/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", "--server.headless=true"]
