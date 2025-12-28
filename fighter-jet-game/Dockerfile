FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server.py .
COPY fighter-jet-game.html .
COPY redis_client.py .
COPY database.py .
COPY backup.py .
COPY websocket_handler.py .

# Create data directory for persistent storage
RUN mkdir -p /app/data

EXPOSE 8080

# Run with gunicorn + eventlet for WebSocket support
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:8080", "server:app"]
