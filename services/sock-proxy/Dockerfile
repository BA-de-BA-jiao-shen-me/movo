FROM python:3.12-slim-bookworm
# Stdlib only; no third-party dependencies.
WORKDIR /app
COPY app ./app
# Runs as root only because it must open docker.sock. read_only, cap_drop ALL
# and the backend-only network are enforced in docker-compose.yml.
ENV PYTHONUNBUFFERED=1
EXPOSE 2375
CMD ["python", "-m", "app.main"]
