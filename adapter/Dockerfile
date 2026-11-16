# The reader's voice-server API (API.md) in front of ZONOS3. Small, no GPU.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV ZONOS_URL=http://zonos3:1919 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python", "app.py"]
