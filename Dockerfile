FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0

WORKDIR /app

# no third-party packages: the runner is standard library only
COPY . /app

# hosted platforms inject PORT; 8080 is the default here
ENV PORT=8080
EXPOSE 8080

CMD ["python3", "server.py"]
