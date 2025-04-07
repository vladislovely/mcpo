FROM python:3.12-slim-bookworm

# Install uv (from official binary), nodejs, npm, and git
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js and npm via NodeSource 
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Confirm npm and node versions (optional debugging info)
RUN node -v && npm -v

# Copy your mcpo source code (assuming in src/mcpo)
COPY src/mcpo /app/mcpo
COPY pyproject.toml /app/
WORKDIR /app

# Install mcpo via uv
RUN uv venv \
    && uv pip install . \
    && rm -rf ~/.cache

# Expose port (optional but common default)
EXPOSE 8000

# Entrypoint set for easy container invocation
ENTRYPOINT ["mcpo"]

# Default help CMD (can override at runtime)
CMD ["--help"]