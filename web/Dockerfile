# --- Stage 1: Build Frontend ---
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
# Copy package files and install dependencies
COPY frontend/package*.json ./
RUN npm install
# Copy frontend source code and build
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Build Backend & Serve App ---
FROM python:3.11-slim

# Install system dependencies needed for compiling some packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy python project configuration
COPY pyproject.toml uv.lock ./

# Install python dependencies using uv (forces system environment installation)
RUN uv pip install --system -r pyproject.toml

# Copy backend files
COPY . .

# Copy built frontend assets from Stage 1 into backend's static directory
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose port 8000
EXPOSE 8000

# Run FastAPI app using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
