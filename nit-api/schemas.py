from pydantic import BaseModel, Field
from typing import Optional


class DespachoOcorrencia(BaseModel):
    cod: str = Field(..., description="Código identificador da ocorrência")
    eq: str = Field(
        ..., max_length=100, description="Nome ou ID da equipe de manutenção"
    )
    vt: str = Field(..., max_length=50, description="Identificação da viatura")
    sub: Optional[str] = Field(
        "vl", description="Sub-status (ex: vl para Via Livre, amc para AMC)"
    )

    # Nossa regra de ouro de proteção contra abusos e controle de custos:
    obs: Optional[str] = Field(
        None, max_length=500, description="Observações limitadas a 500 caracteres"
    )
