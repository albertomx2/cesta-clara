FROM golang:1.26-alpine AS grocery-builder
RUN apk add --no-cache git ca-certificates
RUN git clone --depth 1 https://github.com/jgalea/grocery-cli.git /src/grocery-cli \
    && cd /src/grocery-cli \
    && git fetch --depth 1 origin a5c96f8e1f0f5e33d1c6cc4c89bc498d1c56dd1b \
    && git checkout a5c96f8e1f0f5e33d1c6cc4c89bc498d1c56dd1b \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /grocery ./cmd/grocery

FROM python:3.13-alpine
WORKDIR /app
RUN apk add --no-cache ca-certificates
COPY --from=grocery-builder /grocery /usr/local/bin/grocery
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
