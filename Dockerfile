FROM python:3.10.21-slim-trixie

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY app.py .
COPY shortener.py .
COPY db.py .

EXPOSE 8080


CMD ["python3", "app.py"]