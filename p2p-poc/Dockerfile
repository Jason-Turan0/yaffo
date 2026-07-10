FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY p2p_poc ./p2p_poc

# Cloud Run injects $PORT; must bind 0.0.0.0.
ENV PORT=8080
CMD exec uvicorn p2p_poc.cloud_run_rendezvous:app --host 0.0.0.0 --port ${PORT}
