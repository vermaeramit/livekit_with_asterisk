FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# requirements first so a code change does not re-resolve the dependency tree
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# non-root: the container only needs to read its own code and talk to postgres
RUN useradd -r -u 10001 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
