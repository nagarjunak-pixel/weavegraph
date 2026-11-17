FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY csrc ./csrc
COPY src ./src

RUN uv sync --no-dev
EXPOSE 8150

CMD ["uv", "run", "weavegraph", "serve", "--host", "0.0.0.0", "--port", "8150"]
