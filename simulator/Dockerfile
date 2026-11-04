FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SIM_PORT=9101

WORKDIR /app
COPY app.py /app/app.py

RUN useradd --create-home --uid 10001 simulator
USER simulator

EXPOSE 9101

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9101/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "/app/app.py"]
