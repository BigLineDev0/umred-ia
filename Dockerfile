FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# torch en version CPU uniquement (beaucoup plus léger que la version CUDA
# par défaut) : on l'installe d'abord avec l'index PyTorch dédié, puis le
# reste des dépendances normalement.
# La version DOIT être la même que dans requirements.txt : sinon pip
# remplace torch CPU par la version CUDA de PyPI (plusieurs Go en plus).
# Timeout/retries élevés : les gros wheels échouent sur une connexion lente.
ENV PIP_DEFAULT_TIMEOUT=300 PIP_RETRIES=10
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]

