FROM python:3.11-slim

# GDAL et ses dependances systeme sont indispensables a geopandas.
# python3-dev + build-essential sont necessaires pour compiler certains
# paquets Python qui n'ont pas de wheel precompile pour toutes les plateformes.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Variables necessaires pour que pip trouve la bonne version de GDAL
# a la compilation du binding Python (evite les erreurs de version mismatch).
ENV GDAL_VERSION=3.6.2
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render fournit le port via la variable d'environnement PORT.
# On l'utilise directement dans la commande de demarrage plutot que
# de coder un port fixe (8000 ne sera pas forcement celui attribue).
EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}