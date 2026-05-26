# Use uma versão específica e estável do Python
FROM python:3.12-slim

# Define a pasta de trabalho
WORKDIR /app

# Copia e instala as dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código fonte
COPY . .

# Comando para rodar a aplicação
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"