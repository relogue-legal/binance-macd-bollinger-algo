FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docker_prod.py .
COPY dashboard.html .

EXPOSE 5000

CMD ["python", "docker_prod.py"]
