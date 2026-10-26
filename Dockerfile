# Veto runner, packaged for a hosted deploy.
#
# The demo DB and secrets are NOT baked in: the platform injects environment
# variables, and the runner seeds the demo workspace on an empty /data volume
# on first boot. `PAYPAL_VAULT_ID` re-attaches the approved sandbox wallet at
# startup (see Settlement.prepare), so a fresh host captures like the local one.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8787 \
    VETO_DB=/data/veto.db \
    VETO_SEED_DEMO=1

WORKDIR /app

COPY server.py ./
COPY veto ./veto
COPY app ./app

RUN mkdir -p /data && chmod 777 /data

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3)" || exit 1

CMD ["python3", "server.py"]
