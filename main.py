"""
NIT Central - FastAPI Backend
Versao: 12.0.0 (Sincronizada com Ecossistema)
Data: 28 de maio de 2026

Integracao: Firebase Realtime Database + Railway
"""

from fastapi import FastAPI, Header, HTTPException, Depends, status
from routes.config import router as config_router
from processar_relatorio import router as relatorio_router
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
from datetime import datetime
import logging

# ── CONFIGURACAO DE LOGS ──
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ── INICIALIZACAO FASTAPI ──
app = FastAPI(
    title="NIT Central API",
    description="Backend para gestao e despacho automatizado de ocorrencias de semaforos",
    version="12.0.0",
)
app.include_router(relatorio_router)

# ── CONFIGURACAO CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── VARIAVEIS DE AMBIENTE ──
NIT_SECRET_KEY = os.getenv("NIT_SECRET_KEY", "default-dev-key")
RAILWAY_ENVIRONMENT = os.getenv("RAILWAY_ENVIRONMENT", False)


# ── MODELOS DE DADOS ──
class PayloadNormalizar(BaseModel):
    cod: str


class PayloadDespacho(BaseModel):
    cod: str
    eq: str  # Equipe (ex: VL-001)
    vt: str  # Viatura (ex: TSR-001)
    sub: str  # Subsistema / Tipo (vl ou amc)


# ── AUTENTICACAO ──
async def verify_key(x_nit_key: str = Header(None, alias="X-NIT-Key")):
    """Verificar chave de autenticacao global do ecossistema NIT"""
    if not x_nit_key or x_nit_key != NIT_SECRET_KEY:
        logger.warning(f"[SEGURANCA] Bloqueado! Chave invalida ou ausente")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Chave de autenticacao X-NIT-Key invalida ou ausente.",
        )
    return x_nit_key


# ── EVENTO DE STARTUP ──
@app.on_event("startup")
async def startup_event():
    """Executado quando o app inicia"""
    logger.info("🚀 NIT Central API iniciando...")
    logger.info(f"Documentacao disponivel em /docs")
    logger.info(f"Ambiente: {'Railway' if RAILWAY_ENVIRONMENT else 'Desenvolvimento'}")
    logger.info("✅ NIT Central API iniciada com sucesso!")


@app.on_event("shutdown")
async def shutdown_event():
    """Executado quando o app e encerrado"""
    logger.info("🛑 NIT Central API encerrando...")
    logger.info("✅ NIT Central API encerrada com sucesso!")


# ── ENDPOINTS ──


@app.get("/")
async def root():
    """Endpoint raiz para validacao de Up-time"""
    return {
        "message": "NIT Central API",
        "version": "12.0.0",
        "status": "running",
        "environment": "railway" if RAILWAY_ENVIRONMENT else "development",
    }


@app.get("/health")
async def health():
    """Health check - sem autenticacao para o load balancer"""
    return {
        "status": "healthy",
        "version": "12.0.0",
        "service": "NIT Central API",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/ready")
async def ready():
    """Readiness probe - verifica se o app esta pronto para trafego"""
    return {
        "ready": True,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/v1/normalizar")
async def normalizar(payload: PayloadNormalizar, auth: str = Depends(verify_key)):
    """Marca uma ocorrencia como normalizada"""
    try:
        logger.info(f"Processando encerramento da ocorrencia: {payload.cod}")

        return {
            "success": True,
            "message": f"Ocorrencia {payload.cod} processada com sucesso no backend",
            "codigo": payload.cod,
            "status": "NORMALIZADO",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Erro ao normalizar {payload.cod}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao normalizar: {str(e)}")


@app.post("/api/v1/despacho")
async def despacho(payload: PayloadDespacho, auth: str = Depends(verify_key)):
    """Registra despacho de equipe/viatura"""
    try:
        # Validar tipo de despacho
        if payload.sub not in ["vl", "amc"]:
            raise HTTPException(
                status_code=400,
                detail="Tipo de despacho invalido (deve ser 'vl' ou 'amc')",
            )

        tipo_legivel = "Via Livre" if payload.sub == "vl" else "AMC"
        logger.info(
            f"Despacho Homologado -> Cod: {payload.cod} | Equipe: {payload.eq} [{tipo_legivel}]"
        )

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
        logger.error(f"Erro ao registrar despacho: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Erro ao registrar despacho: {str(e)}"
        )


# ── CUSTOM ERROR HANDLER (CORRIGIDO) ──
from fastapi.responses import JSONResponse


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handler personalizado para erros HTTP"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status": exc.status_code,
            "timestamp": datetime.now().isoformat(),
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handler para erros não tratados"""
    logger.error(f"Erro nao tratado: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Erro interno do servidor",
            "status": 500,
            "timestamp": datetime.now().isoformat(),
        },
    )


# ── INICIALIZACAO LOCAL (APENAS PARA TESTES) ──
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))

    print(f"""
    ╔════════════════════════════════════════════════╗
    ║         NIT Central API - MODO LOCAL           ║
    ║                                                ║
    ║  Versao: 12.0.0                                ║
    ║  Porta: {port}                                 ║
    ║  Secret Key: {'✓ Configurada' if NIT_SECRET_KEY != 'default-dev-key' else '⚠️ Padrao'}
    ║                                                ║
    ╚════════════════════════════════════════════════╝
    """)

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True, log_level="info")
