FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cacheable layer)
COPY controller/pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "asyncpg>=0.30" \
    "redis>=5.0" \
    "kubernetes>=31.0" \
    "pydantic-settings>=2.0" \
    "httpx>=0.27" \
    "aiosqlite>=0.20" \
    "pyyaml>=6.0" \
    "jsonschema>=4.0"

# Copy source
COPY controller/src/ ./src/
ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD ["uvicorn", "controller.main:app", "--host", "0.0.0.0", "--port", "8000"]
