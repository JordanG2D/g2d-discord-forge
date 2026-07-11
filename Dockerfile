FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY forge.py ./
COPY src ./src

RUN mkdir -p /app/state
VOLUME ["/app/state"]

ENTRYPOINT ["python", "/app/forge.py"]
CMD ["--help"]
