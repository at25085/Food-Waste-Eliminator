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
# xgboost-cpu: same package without the CUDA/NCCL libraries (the server does not train)
RUN sed -i "s/^xgboost/xgboost-cpu/" backend/requirements.txt && pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
# Models, plans and training data are NOT baked in: they live on persistent volumes
# (docker-compose.prod.yml) so uploads and live retrains survive restarts.
RUN mkdir -p artifacts data/processed data/cache
ENV PYTHONPATH=/app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "forecaster.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
