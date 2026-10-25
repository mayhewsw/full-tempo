FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Baked-in MusicBrainz data (~1.8GB, static/read-only — see data/README.md).
# Copied as its own layer, before the rest of the app code, so routine code
# changes don't invalidate and re-push this large layer.
COPY data/musicbrainz_classical.sqlite data/musicbrainz_classical.sqlite

COPY . .
EXPOSE 8080
CMD ["uv", "run", "python", "app.py"]
