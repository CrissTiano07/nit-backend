"""
NIT Central - FastAPI Backend
Versão: 12.0.0 (Sincronizada com Ecossistema)
Data: 23 de maio de 2026

Integração: Firebase Realtime Database + Railway
"""

from fastapi import FastAPI, Header, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import os
from datetime import datetime

# ── INICIALIZAÇÃO FASTAPI ──
app = FastAPI(
    title="NIT Central API",
    description="Backend para gestão e despacho automatizado de ocorrências de semáforos",
    version="12.0.0",
)

# ── CONFIGURAÇÃO CORS (LIBERAÇÃO MULTI-TELA E LOCALHOST) ──
# Permite que o frontend acesse a API vindo de qualquer porta (8080, 5500) ou do GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── VARIÁVEIS DE AMBIENTE ──
NIT_SECRET_KEY = os.getenv("NIT_SECRET_KEY", "default-dev-key")


# ── MODELOS DE DADOS (SCHEMAS PYDANTIC) ──
class PayloadNormalizar(BaseModel):
    cod: str


class PayloadDespacho(BaseModel):
    cod: str
    eq: str  # Equipe (ex: VL-001)
    vt: str  # Viatura (ex: TSR-001)
    sub: str  # Subsistema / Tipo (vl ou amc)


# ── AUTENTICAÇÃO VIA HEADER CUSTOMIZADO ──
async def verify_key(x_nit_key: str = Header(None, alias="X-NIT-Key")):
    """Verificar chave de autenticação global do ecossistema NIT"""
    if not x_nit_key or x_nit_key != NIT_SECRET_KEY:
        print(f"[ALERTA SEGURANÇA] Bloqueado! Chave inválida ou ausente: {x_nit_key}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de autenticação X-NIT-Key inválida ou ausente.",
        )
    return x_nit_key


# ── ENDPOINTS ──


@app.get("/")
async def root():
    """Endpoint raiz para validação de Up-time"""
    return {"message": "NIT Central API", "version": "12.0.0", "status": "running"}


@app.get("/health")
async def health():
    """Health check - sem autenticação"""
    return {
        "status": "online",
        "version": "12.0.0",
        "service": "NIT Central API",
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/v1/normalizar")
async def normalizar(payload: PayloadNormalizar, auth: str = Depends(verify_key)):
    """
    Marca uma ocorrência como normalizada recebendo o payload via JSON BODY.
    Requer header: X-NIT-Key
    """
    try:
        print(f"[NIT-API] Processando encerramento da ocorrência: {payload.cod}")

        # O Firebase cuida do estado da tela em tempo real.
        # Este espaço está pronto para receber regras de persistência de relatórios futuros (Fase 2).
        return {
            "success": True,
            "message": f"Ocorrência {payload.cod} processada com sucesso no backend",
            "codigo": payload.cod,
            "status": "NORMALIZADO",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao normalizar: {str(e)}")


@app.post("/api/v1/despacho")
async def despacho(payload: PayloadDespacho, auth: str = Depends(verify_key)):
    """
    Registra despacho de equipe/viatura recebendo o payload via JSON BODY.
    Requer header: X-NIT-Key
    """
    try:
        # Validar tipo de despacho
        if payload.sub not in ["vl", "amc"]:
            raise HTTPException(
                status_code=400,
                detail="Tipo de despacho inválido (deve ser 'vl' ou 'amc')",
            )

        tipo_legivel = "Via Livre" if payload.sub == "vl" else "AMC"
        print(
            f"[NIT-API] Despacho Homologado -> Cód: {payload.cod} | Equipe: {payload.eq} [{tipo_legivel}]"
        )

        # Pronto para acoplar o disparo automático de WhatsApp/Telegram para as equipes de rua aqui.
        return {
            "success": True,
            "message": f"Despacho para {payload.cod} homologado com sucesso.",
            "despacho": {
                "codigo": payload.cod,
                "equipe": payload.eq,
                "viatura": payload.vt,
                "tipo": tipo_legivel,
                "tipo_codigo": payload.sub,
                "timestamp": datetime.now().isoformat(),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Erro ao registrar despacho: {str(e)}"
        )


# ── CUSTOM ERROR HANDLER ──
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return {
        "error": exc.detail,
        "status": exc.status_code,
        "timestamp": datetime.now().isoformat(),
    }


# ── INICIALIZAÇÃO DO SERVIDOR LOCAL ──
if __name__ == "__main__":
    import uvicorn
    import os

    print(f"""
    ╔════════════════════════════════════════════════╗
    ║         NIT Central API - CORE BACKEND         ║
    ║                                                ║
    ║  Versão: 12.0.0                                ║
    ║  Porta: {port}                                    ║
    ║  Secret Key: {'✓ Configurada' if NIT_SECRET_KEY != 'default-dev-key' else '⚠️ Padrão'}
    ║                                                ║
    ╚════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
