FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY bot ./bot
COPY db ./db
COPY web ./web
COPY config ./config
COPY prompts ./prompts
COPY main.py ./

RUN mkdir -p /app/personas /app/prompts_public /app/personas_public /app/prompts_backup /app/data /app/logs

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "main.py", "worker", "--root", "/app"]
