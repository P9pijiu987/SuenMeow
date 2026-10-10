FROM registry-1.docker.io/library/python:3.12-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN chmod -R a+rX /app && pip install --no-cache-dir . && useradd -u 10001 -m suenmeow
USER 10001
EXPOSE 8000
CMD ["suenmeow", "api"]

FROM backend AS test
USER root
RUN pip install --no-cache-dir '.[dev]'
USER 10001
CMD ["python", "-m", "pytest", "-q"]
