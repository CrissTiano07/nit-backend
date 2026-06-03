from fastapi import APIRouter, HTTPException, Request
import os

router = APIRouter(prefix="/api/v1", tags=["config"])


@router.get("/config", include_in_schema=False)
async def get_config(request: Request):
    """
    Retorna a chave NIT_SECRET_KEY para o frontend.
    Protegido por verificação de origem (CORS).
    """
    origin = request.headers.get("origin", "")
    allowed_origins = os.getenv("ALLOWED_ORIGINS", "").split(",")

    # Verifica se a origem é autorizada
    if allowed_origins and allowed_origins != [""]:
        if not any(origin.startswith(a.strip()) for a in allowed_origins if a.strip()):
            raise HTTPException(status_code=403, detail="Origem não autorizada.")

    key = os.getenv("NIT_SECRET_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503, detail="Chave não configurada no servidor."
        )

    return {"key": key}
