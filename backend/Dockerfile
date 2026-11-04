FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SHIP_DATABASE_URL=sqlite:////data/ship.db \
    SHIP_ANSIBLE_PLAYBOOK_DIR=/ansible/playbooks \
    SHIP_ANSIBLE_INVENTORY=/ansible/inventory/hosts.ini

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl openssh-client tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-recovery.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt -r requirements-recovery.txt

COPY app ./app

RUN useradd --create-home --uid 10001 ship \
    && mkdir -p /data \
    && chown -R ship:ship /app /data

USER ship
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
