FROM python:3.13-slim-bookworm

RUN python3 -m pip install httpx loguru psycopg2-binary

WORKDIR /app
COPY setup_rctf.py .

CMD ["python3", "setup_rctf.py"]
