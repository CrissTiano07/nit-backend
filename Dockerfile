# Usa uma imagem oficial estável do Python 3.12
FROM python:3.12-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Instala dependências antes de copiar o código (otimização de cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o resto do código para dentro do container
COPY . .

# Expõe a porta que o FastAPI usa
EXPOSE 8000

# Comando para rodar a aplicação
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]