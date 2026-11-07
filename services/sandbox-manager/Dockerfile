FROM python:3.10-slim-bookworm

ARG MOVO_PIP_INDEX_URL=
WORKDIR /app
COPY requirements.txt .
RUN if [ -n "${MOVO_PIP_INDEX_URL}" ]; then \
      python -m pip install --no-cache-dir --index-url "${MOVO_PIP_INDEX_URL}" -r requirements.txt; \
    else \
      python -m pip install --no-cache-dir -r requirements.txt; \
    fi
COPY app ./app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin movo
# P0: docker.sock now lives only in sock-proxy, so the manager runs non-root.
USER 10001:10001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
