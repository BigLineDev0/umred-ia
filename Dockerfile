FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Cache Hugging Face sur un volume : le modèle (~3 Go) n'est téléchargé
    # qu'au premier démarrage, pas à chaque reconstruction de l'image.
    HF_HOME=/cache/huggingface

WORKDIR /app

# PyTorch en version CPU depuis l'index officiel : la version par défaut de
# PyPI embarque CUDA (plusieurs Go) alors que le modèle tourne sur CPU.
# 2.14.0+cpu satisfait ensuite le « torch==2.14.0 » de requirements.txt.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.0

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 umred \
    && mkdir -p /cache/huggingface && chown -R umred:umred /cache
COPY --chown=umred:umred . .
USER umred

EXPOSE 8001

# Le modèle se charge en arrière-plan : /health répond dès le démarrage
# (« mode_degrade » tant que le modèle n'est pas prêt, puis « charge »).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
