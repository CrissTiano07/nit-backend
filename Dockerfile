FROM python:3.12-slim

WORKDIR /app

# Instalar curl para healthcheck (opcional)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o resto da aplicação
COPY . .

# Variável para logs em tempo real
ENV PYTHONUNBUFFERED=1

# Healthcheck (opcional, mas recomendado)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Expoe a porta (documentação)
EXPOSE ${PORT:-8000}

# ✅ CRÍTICO: Usar $PORT do Railway
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"