# One image: FastAPI serves /api and the built React dashboard.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
# Pipeline outputs (models, ledger DB, recommendations) are produced locally and shipped as data.
COPY artifacts/ artifacts/
ENV PYTHONPATH=/app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "forecaster.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
