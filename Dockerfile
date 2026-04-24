FROM python:3.11-slim AS backend

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY dashboard/backend/requirements.txt /tmp/requirements-backend.txt
COPY requirements-agents.txt /tmp/requirements-agents.txt

RUN pip install --no-cache-dir -r /tmp/requirements-backend.txt \
    && pip install --no-cache-dir -r /tmp/requirements-agents.txt

COPY . /app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


FROM node:20-alpine AS frontend

WORKDIR /app/dashboard/frontend

COPY dashboard/frontend/package*.json ./
RUN npm install

COPY dashboard/frontend /app/dashboard/frontend

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
