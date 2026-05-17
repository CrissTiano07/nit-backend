from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from database import inicializar_firebase
from schemas import DespachoOcorrencia
import time

app = FastAPI(title="NIT - Núcleo Inteligente de Tráfego API")

# Configuração de CORS (Essencial para o GitHub Pages conversar com o seu Python local)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite que qualquer origem acesse durante os testes locais
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializa o Firebase com o acesso mestre que configuramos
db = inicializar_firebase()


@app.post("/despacho")
async def registrar_despacho(dados: DespachoOcorrencia):
    try:
        # Acessa diretamente a ocorrência correspondente no banco usando o Admin SDK
        ref = db.reference(f"/ocorrencias/{dados.cod}")

        payload = {
            "eq": dados.eq,
            "vt": dados.vt,
            "sub": dados.sub,
            "pl": "atend",  # Altera o status para mover o card de coluna automaticamente
            "ts": int(
                time.time() * 1000
            ),  # Gera o timestamp correto exigido pelo Supervisor
        }

        # O Admin SDK tem acesso livre, ignorando erros de permissão do cliente
        ref.update(payload)
        return {"status": "success", "message": f"Ocorrência {dados.cod} despachada."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no Firebase Admin: {str(e)}")
