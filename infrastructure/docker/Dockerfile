FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY swarm ./swarm

RUN pip install --no-cache-dir .

CMD ["python", "-m", "swarm.main"]
