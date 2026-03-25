FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY eve_client/ eve_client/

RUN pip install --no-cache-dir '.[server]'

ENTRYPOINT ["eve-mcp-server"]
