import re
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# Imports internos do seu projeto
from database import inicializar_firebase
from schemas import DespachoOcorrencia

app = FastAPI(title="NIT - Núcleo Inteligente de Tráfego API")

# Configuração de CORS (Essencial para o GitHub Pages conversar com o seu Python local/nuvem)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite que qualquer origem acesse durante os testes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inicializa o Firebase com o acesso mestre
db = inicializar_firebase()

# =====================================================================
# 1. SCHEMAS DE VALIDAÇÃO (PYDANTIC)
# =====================================================================


class NITResponse(BaseModel):
    """Modelo unificado de resposta da API para o Frontend"""

    ok: bool
    cod: str
    ts: int
    msg: str


def validar_cod(v: str) -> str:
    """Sanitiza e valida o padrão alfanumérico do código da ocorrência"""
    if not v:
        raise ValueError("O código da ocorrência não pode ser vazio.")
    # Remove espaços extras e força caixa alta para manter o padrão do NIT
    return re.sub(r"\s+", "", v).upper()


class CodBase(BaseModel):
    cod: str = Field(..., min_length=1, max_length=30)

    # Aplica o validador compartilhado para garantir consistência DRY
    _validate_cod = validator("cod", allow_reuse=True)(validar_cod)


class NormalizarOcorrencia(CodBase):
    fim: str | None = Field(None, description="Horário de término opcional (HH:MM)")

    @validator("fim")
    def validar_formato_hora(cls, v):
        if v is not None:
            if not re.match(r"^\d{2}:\d{2}$", v):
                raise ValueError("O horário de término deve seguir o formato HH:MM")
        return v


class ReativarOcorrencia(CodBase):
    pass  # Reativar usa apenas o 'cod' herdado de CodBase


# =====================================================================
# 2. ROTAS OPERACIONAIS DO SISTEMA
# =====================================================================


@app.post("/despacho", response_model=NITResponse)
async def registrar_despacho(dados: DespachoOcorrencia):
    try:
        # Força a sanitização do código vindo do schema externo
        cod_sanitizado = validar_cod(dados.cod)
        ref = db.reference(f"/ocorrencias/{cod_sanitizado}")

        ts_atual = int(time.time() * 1000)

        payload = {
            "eq": dados.eq,
            "vt": dados.vt,
            "sub": dados.sub,
            "pl": "atend",  # Move o card para a coluna de atendimento
            "ts": ts_atual,
        }

        ref.update(payload)
        db.reference("meta").update({"lastUpdate": ts_atual})

        return NITResponse(
            ok=True,
            cod=cod_sanitizado,
            ts=ts_atual,
            msg=f"Ocorrência {cod_sanitizado} despachada com sucesso.",
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro no Firebase Admin: {str(e)}",
        )


@app.post("/normalizar", response_model=NITResponse)
async def normalizar_despacho(payload: NormalizarOcorrencia):
    ref_ocorrencia = db.reference(f"ocorrencias/{payload.cod}")

    # [FILTRO 1] Verificação de Existência (Evita nós fantasmas)
    dados_atuais = ref_ocorrencia.get()
    if dados_atuais is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ocorrência {payload.cod} não foi encontrada no sistema.",
        )

    # [FILTRO 2] State Guard (Proteção contra double-click)
    if dados_atuais.get("pl") == "norm":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A ocorrência {payload.cod} já se encontra normalizada.",
        )

    ts_atual = int(time.time() * 1000)
    hora_servidor = datetime.now().strftime("%H:%M")

    # Define o horário final (usa o enviado pelo front ou gera no servidor)
    horario_fim = payload.fim if payload.fim else hora_servidor

    # [PERSISTÊNCIA INCREMENTAL] Altera APENAS o necessário. 'sub' fica intacto.
    payload_atualizacao = {"pl": "norm", "fim": horario_fim, "ts": ts_atual}

    # TODO: Futuramente, calcular a métrica de duração aqui (fim - ini)

    ref_ocorrencia.update(payload_atualizacao)
    db.reference("meta").update({"lastUpdate": ts_atual})

    return NITResponse(
        ok=True,
        cod=payload.cod,
        ts=ts_atual,
        msg=f"Ocorrência {payload.cod} normalizada com sucesso às {horario_fim}.",
    )


@app.post("/reativar", response_model=NITResponse)
async def reativar_despacho(payload: ReativarOcorrencia):
    ref_ocorrencia = db.reference(f"ocorrencias/{payload.cod}")

    # [FILTRO 1] Verificação de Existência
    dados_atuais = ref_ocorrencia.get()
    if dados_atuais is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ocorrência {payload.cod} não existe para ser reativada.",
        )

    # [FILTRO 2] State Guard (Evita reativar o que já está ativo)
    if dados_atuais.get("pl") == "atend":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A ocorrência {payload.cod} já está ativa em atendimento.",
        )

    ts_atual = int(time.time() * 1000)

    # [PERSISTÊNCIA INCREMENTAL] Joga para 'atend' e limpa o 'fim' antigo
    payload_atualizacao = {
        "pl": "atend",
        "fim": None,  # Limpa o registro para o card voltar à linha do tempo ativa
        "ts": ts_atual,
    }

    ref_ocorrencia.update(payload_atualizacao)
    db.reference("meta").update({"lastUpdate": ts_atual})

    return NITResponse(
        ok=True,
        cod=payload.cod,
        ts=ts_atual,
        msg=f"Ocorrência {payload.cod} reativada com sucesso. Retornada para a coluna de Atendimento.",
    )
