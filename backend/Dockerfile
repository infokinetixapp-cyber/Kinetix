FROM python:3.11-slim

WORKDIR /app

# Install system dependencies that some Python packages need
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python deps first for better Docker layer caching
COPY requirements.txt .
# emergentintegrations is hosted on Emergent's public CDN (not on pypi.org), so we
# explicitly add their index as an extra source. This lets the package resolver
# find it during build on Render / any non-Emergent host.
RUN pip install --no-cache-dir \
    --extra-index-url https://d33sy5i8bnduze.cloudfront.net/simple/ \
    -r requirements.txt

# Copy app source
COPY . .

# Render injects $PORT — bind to it; never to localhost
EXPOSE 8001
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001} --proxy-headers --forwarded-allow-ips=*"]
