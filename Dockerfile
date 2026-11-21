FROM python:3.12-slim

# Empêche Python de créer des fichiers .pyc et force l'affichage immédiat des logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Installation de Tesseract OCR et des packs de langues français + anglais
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Installation des dépendances Python (mis en cache tant que requirements.txt ne change pas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source du projet
COPY . .

EXPOSE 8000

# Démarrage du serveur Django accessible depuis l'extérieur du conteneur
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]