FROM python:3.13-slim-bookworm

RUN python3 -m pip install httpx loguru PyMySQL

WORKDIR /app
COPY setup_ctfd.py .

CMD ["python3", "setup_ctfd.py"]
