FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# requirements first so a code change does not re-resolve the dependency tree
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# non-root. /data/kb must exist and be owned here: Docker seeds an empty named
# volume from the image path, so creating it after USER would leave it root-owned
# and every upload would fail with EACCES.
RUN useradd -r -u 10001 appuser \
 && mkdir -p /data/kb \
 && chown -R appuser /app /data/kb
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
