"""
NIT — Núcleo Inteligente de Tráfego
Backend FastAPI — main.py
Deploy: Railway (HTTPS nativo)
Firebase: Admin SDK (acesso mestre via credentials.json)
"""

from fastapi import FastAPI, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import Optional, Literal
import time, logging, os, re
from datetime import datetime
import pytz

from database import get_db

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [NIT] %(levelname)s %(message)s"
)
log = logging.getLogger("nit")

# ──────────────────────────────────────────────
#  APP
# ──────────────────────────────────────────────
app = FastAPI(
    title="NIT API",
    description="Núcleo Inteligente de Tráfego — AMC Fortaleza",
    version="1.1.0",
)

# ──────────────────────────────────────────────
#  CORS — restritivo (não mais "*")
# ──────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "https://crisstiano07.github.io",  # produção + homologação
    "http://127.0.0.1:5500",  # Live Server VS Code
    "http://localhost:5500",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "X-NIT-Key"],
)

# ──────────────────────────────────────────────
#  API KEY — lida da variável de ambiente
# ──────────────────────────────────────────────
NIT_API_KEY = os.environ.get("NIT_API_KEY", "")


def verificar_api_key(x_nit_key: Optional[str] = Header(default=None)):
    """
    Dependência de autenticação injetada em todas as rotas operacionais.
    Retorna 403 se o header estiver ausente ou a chave for inválida.
    NIT_API_KEY vazia desativa a verificação apenas em dev local sem variável.
    """
    if not NIT_API_KEY:
        # Variável não configurada — modo dev sem proteção
        log.warning("NIT_API_KEY não configurada — autenticação desativada")
        return
    if x_nit_key != NIT_API_KEY:
        log.warning("Tentativa com chave inválida: %s", x_nit_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave de API inválida ou ausente.",
        )


# ──────────────────────────────────────────────
#  SCHEMAS — base compartilhada
# ──────────────────────────────────────────────
def _validar_cod(v: str) -> str:
    v = v.strip().upper()
    if not re.match(r"^[A-Z0-9]{1,20}$", v):
        raise ValueError("cod deve ser alfanumérico sem espaços (ex: A14, B03)")
    return v


class CodBase(BaseModel):
    cod: str = Field(
        ..., min_length=1, max_length=20, description="Código da ocorrência"
    )

    @validator("cod")
    def cod_alfanumerico(cls, v):
        return _validar_cod(v)


class DespachoOcorrencia(CodBase):
    eq: str = Field(..., min_length=1, max_length=60, description="Equipe / agente")
    vt: str = Field("N/I", max_length=20, description="Viatura")
    sub: Literal["vl", "amc", "sn"] = Field(
        ..., description="vl=Via Livre, amc=AMC, sn=Sem Necessidade"
    )

    @validator("eq")
    def eq_sem_html(cls, v):
        if "<" in v or ">" in v:
            raise ValueError("eq não pode conter HTML")
        return v.strip()

    @validator("vt")
    def vt_normalizar(cls, v):
        return v.strip() or "N/I"


class NormalizarOcorrencia(CodBase):
    fim: Optional[str] = Field(
        default=None,
        description="Horário de normalização HH:MM — se omitido, gerado server-side (Fortaleza BRT-3)",
    )

    @validator("fim")
    def fim_formato(cls, v):
        if v is not None:
            v = v.strip()
            if not re.match(r"^\d{2}:\d{2}$", v):
                raise ValueError("fim deve estar no formato HH:MM")
        return v


class ReativarOcorrencia(CodBase):
    pass  # cod herdado — reativar não tem parâmetros variáveis


class DespachoResponse(BaseModel):
    ok: bool
    cod: str
    ts: int
    msg: str


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────
TZ_FORTALEZA = pytz.timezone("America/Fortaleza")


def _agora_fortaleza() -> str:
    """Retorna HH:MM no fuso de Fortaleza (BRT-3, sem horário de verão)."""
    return datetime.now(TZ_FORTALEZA).strftime("%H:%M")


def _ts_ms() -> int:
    return int(time.time() * 1000)


def _get_card(db, cod: str) -> dict:
    """
    Lê o nó /ocorrencias/{cod} do Firebase.
    Lança 404 se não existir — evita criação de nó fantasma.
    """
    snapshot = db.reference(f"ocorrencias/{cod}").get()
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ocorrência '{cod}' não encontrada no Firebase.",
        )
    return snapshot


# ──────────────────────────────────────────────
#  ROTAS
# ──────────────────────────────────────────────
@app.get("/", tags=["health"])
def health():
    return {"status": "online", "sistema": "NIT API v1.1"}


@app.post(
    "/despacho",
    response_model=DespachoResponse,
    status_code=status.HTTP_200_OK,
    tags=["semaforo"],
    summary="Despacha equipe para uma ocorrência semafórica",
)
def despacho(
    payload: DespachoOcorrencia,
    x_nit_key: Optional[str] = Header(default=None),
):
    verificar_api_key(x_nit_key)
    db = get_db()
    ts = _ts_ms()
    cod = payload.cod

    # 404 se card não existe
    _get_card(db, cod)

    # pl depende do sub: 'sn' é um estado próprio, não 'atend'
    pl = "sn" if payload.sub == "sn" else "atend"

    updates = {
        f"ocorrencias/{cod}/eq": payload.eq,
        f"ocorrencias/{cod}/vt": payload.vt,
        f"ocorrencias/{cod}/sub": payload.sub if payload.sub != "sn" else None,
        f"ocorrencias/{cod}/pl": pl,
        f"ocorrencias/{cod}/ts": ts,
        "meta/lastUpdate": ts,
    }
    try:
        db.reference("/").update(updates)
        log.info(
            "DESPACHO OK | cod=%s eq=%s sub=%s pl=%s", cod, payload.eq, payload.sub, pl
        )
    except Exception as e:
        log.error("DESPACHO FAIL | cod=%s erro=%s", cod, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    acao = "Sem necessidade" if pl == "sn" else f"despachado → {payload.sub.upper()}"
    return DespachoResponse(ok=True, cod=cod, ts=ts, msg=f"{cod} {acao}.")


@app.post(
    "/normalizar",
    response_model=DespachoResponse,
    status_code=status.HTTP_200_OK,
    tags=["semaforo"],
    summary="Normaliza uma ocorrência semafórica",
)
def normalizar(
    payload: NormalizarOcorrencia,
    x_nit_key: Optional[str] = Header(default=None),
):
    verificar_api_key(x_nit_key)
    db = get_db()
    ts = _ts_ms()
    cod = payload.cod

    # 404 se não existe
    card = _get_card(db, cod)

    # 409 se já normalizado
    if card.get("pl") == "norm":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ocorrência '{cod}' já está normalizada.",
        )

    # fim: usa valor enviado ou gera server-side (Fortaleza BRT-3)
    fim = payload.fim or _agora_fortaleza()

    # sub NÃO está no payload — não é tocado pelo update()
    updates = {
        f"ocorrencias/{cod}/pl": "norm",
        f"ocorrencias/{cod}/fim": fim,
        f"ocorrencias/{cod}/ts": ts,
        "meta/lastUpdate": ts,
    }
    try:
        db.reference("/").update(updates)
        log.info("NORMALIZAR OK | cod=%s fim=%s", cod, fim)
    except Exception as e:
        log.error("NORMALIZAR FAIL | cod=%s erro=%s", cod, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return DespachoResponse(ok=True, cod=cod, ts=ts, msg=f"{cod} normalizado às {fim}.")


@app.post(
    "/reativar",
    response_model=DespachoResponse,
    status_code=status.HTTP_200_OK,
    tags=["semaforo"],
    summary="Reativa uma ocorrência normalizada para espera",
)
def reativar(
    payload: ReativarOcorrencia,
    x_nit_key: Optional[str] = Header(default=None),
):
    verificar_api_key(x_nit_key)
    db = get_db()
    ts = _ts_ms()
    cod = payload.cod

    # 404 se não existe
    card = _get_card(db, cod)

    # 409 se já está em espera
    if card.get("pl") == "espera":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ocorrência '{cod}' já está em espera.",
        )

    # sub e eq/vt preservados — update() incremental não os toca
    updates = {
        f"ocorrencias/{cod}/pl": "espera",
        f"ocorrencias/{cod}/fim": None,  # limpa horário de normalização
        f"ocorrencias/{cod}/ts": ts,
        "meta/lastUpdate": ts,
    }
    try:
        db.reference("/").update(updates)
        log.info("REATIVAR OK | cod=%s", cod)
    except Exception as e:
        log.error("REATIVAR FAIL | cod=%s erro=%s", cod, e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))

    return DespachoResponse(
        ok=True, cod=cod, ts=ts, msg=f"{cod} reativado para espera."
    )


# ──────────────────────────────────────────────
#  ROTA FUTURA — ingestão de relatórios WhatsApp
# ──────────────────────────────────────────────
# @app.post("/ingestao/whatsapp")
# def ingestao_whatsapp(raw: RawRelatorio):
#     ocorrencias = parser_cemob(raw.texto)
#     for oc in ocorrencias:
#         db.reference(f"ocorrencias/{oc.cod}").set(oc.dict())
#     return {"ingeridos": len(ocorrencias)}
