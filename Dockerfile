FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Run migrations
RUN python manage.py migrate

EXPOSE 8000

CMD ["gunicorn", "spotter_backend.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]

