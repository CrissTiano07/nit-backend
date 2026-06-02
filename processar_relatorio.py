"""
NIT Backend — Módulo: processar_relatorio.py
Cole este arquivo no projeto FastAPI existente e registre o router em main.py:

    from processar_relatorio import router as relatorio_router
    app.include_router(relatorio_router)

Dependências adicionais (adicione ao requirements.txt):
    fastapi          # já deve existir
    pydantic         # já deve existir
    python-dotenv    # já deve existir
    httpx            # consulta batch ao Firebase (reincidencia real)
    # NAO precisa de slowapi — rate limiter implementado em memoria pura

Variavel de ambiente adicional (Railway -> Variables):
    FIREBASE_URL = https://nit-operacional-default-rtdb.firebaseio.com
    (sem barra final; usada para consulta shallow de reincidencia)
"""

from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from typing import List, Optional
import os, re, time, hashlib
from datetime import datetime, timezone
from collections import defaultdict

router = APIRouter(prefix="/api/v1", tags=["relatorio"])

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTES DE LIMITE — ajustadas para manter planos gratuitos
# ═══════════════════════════════════════════════════════════════════════

# Railway free: ~500h CPU/mês → limitar rajadas de processamento
_RATE_LIMIT_JANELA_S = 60  # janela de 60 segundos
_RATE_LIMIT_MAX_REQS = 10  # máx 10 chamadas/min por IP (operador único)
_RATE_LIMIT_BURST_S = 2.0  # mínimo 2s entre chamadas do mesmo IP (anti-spam)

# Firebase Realtime DB free: 1 GB storage, 10 GB download/mês
_MAX_EVENTOS_POR_LOTE = 200  # protege contra relatórios enormes que inflariam o DB
_MAX_TEXTO_BYTES = 64_000  # 64 KB — relatório CEMOB típico < 5 KB
_MAX_OBS_CHARS = 300  # trunca observações longas antes de gravar no DB
_HISTORICO_MAX_DIAS = 7  # historico/ no Firebase: TTL recomendado 7 dias
# (implemente uma Cloud Function ou cron para purgar)

# Railway: 512 MB RAM — evitar acúmulo de estado em memória
_RATE_STORE_MAX_IPS = 500  # máx 500 IPs rastreados em memória simultaneamente
_CACHE_TTL_S = 10.0  # cache de payload idêntico: 10s (anti-duplo-clique)
_CACHE_MAX_ENTRIES = 50  # máx 50 entradas no cache de deduplicação

# ═══════════════════════════════════════════════════════════════════════
# RATE LIMITER EM MEMÓRIA (sem dependência externa)
# ═══════════════════════════════════════════════════════════════════════


class _RateLimiter:
    """
    Rate limiter por IP com duas camadas:
      1. Burst: mínimo de segundos entre chamadas consecutivas do mesmo IP
      2. Janela deslizante: máx N chamadas em M segundos
    Sem dependência de Redis ou slowapi — adequado para Railway free.
    """

    def __init__(self):
        self._ultima_chamada: dict[str, float] = {}
        self._historico: dict[str, list[float]] = defaultdict(list)

    def _limpar_ips_antigos(self):
        """Evita vazamento de memória: descarta IPs inativos."""
        if len(self._historico) > _RATE_STORE_MAX_IPS:
            agora = time.monotonic()
            # Remove IPs sem chamada nos últimos 2× a janela
            corte = agora - _RATE_LIMIT_JANELA_S * 2
            inativos = [ip for ip, ts in self._ultima_chamada.items() if ts < corte]
            for ip in inativos:
                self._ultima_chamada.pop(ip, None)
                self._historico.pop(ip, None)

    def verificar(self, ip: str) -> None:
        """Lança HTTPException 429 se o IP ultrapassou algum limite."""
        agora = time.monotonic()
        self._limpar_ips_antigos()

        # ── Camada 1: burst (anti-duplo-clique / loop acidental) ──
        ultima = self._ultima_chamada.get(ip, 0.0)
        if agora - ultima < _RATE_LIMIT_BURST_S:
            restante = round(_RATE_LIMIT_BURST_S - (agora - ultima), 1)
            raise HTTPException(
                status_code=429,
                detail=f"Aguarde {restante}s antes de reprocessar.",
                headers={"Retry-After": str(restante)},
            )

        # ── Camada 2: janela deslizante ──
        janela_inicio = agora - _RATE_LIMIT_JANELA_S
        hist = [t for t in self._historico[ip] if t > janela_inicio]
        if len(hist) >= _RATE_LIMIT_MAX_REQS:
            raise HTTPException(
                status_code=429,
                detail=f"Limite de {_RATE_LIMIT_MAX_REQS} requisições/minuto atingido. Aguarde.",
                headers={"Retry-After": str(_RATE_LIMIT_JANELA_S)},
            )

        # Registra esta chamada
        hist.append(agora)
        self._historico[ip] = hist
        self._ultima_chamada[ip] = agora


_rate_limiter = _RateLimiter()

# ═══════════════════════════════════════════════════════════════════════
# CACHE DE DEDUPLICAÇÃO (evita reprocessar payload idêntico em < 10s)
# Protege contra duplo-clique acidental e economiza CPU no Railway
# ═══════════════════════════════════════════════════════════════════════


class _PayloadCache:
    def __init__(self):
        self._store: dict[str, tuple[float, dict]] = {}  # hash → (ts, resposta)

    def _hash(self, texto: str) -> str:
        return hashlib.sha256(texto.encode()).hexdigest()[:16]

    def _limpar(self):
        if len(self._store) > _CACHE_MAX_ENTRIES:
            agora = time.monotonic()
            expirados = [
                k for k, (ts, _) in self._store.items() if agora - ts > _CACHE_TTL_S
            ]
            for k in expirados:
                del self._store[k]

    def get(self, texto: str) -> Optional[dict]:
        self._limpar()
        h = self._hash(texto)
        entrada = self._store.get(h)
        if entrada and time.monotonic() - entrada[0] < _CACHE_TTL_S:
            return entrada[1]
        return None

    def set(self, texto: str, resposta: dict):
        self._limpar()
        self._store[self._hash(texto)] = (time.monotonic(), resposta)


_cache = _PayloadCache()

# ═══════════════════════════════════════════════════════════════════════
# MODELOS PYDANTIC
# ═══════════════════════════════════════════════════════════════════════


class ProcessarRequest(BaseModel):
    texto: str

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, v: str) -> str:
        # Proteção Firebase: evita relatórios gigantes que inflariam o DB
        if len(v.encode()) > _MAX_TEXTO_BYTES:
            raise ValueError(
                f"Texto muito grande ({len(v.encode())/1024:.0f} KB). "
                f"Máximo: {_MAX_TEXTO_BYTES//1024} KB."
            )
        if not v.strip():
            raise ValueError("Texto do relatório não pode ser vazio.")
        return v


class EventoProcessado(BaseModel):
    eventoId: str
    codigo: str
    endereco: str
    problema: str
    tipo: str
    inicio: Optional[str] = None
    fim: Optional[str] = None
    observacoes: Optional[str] = None
    status: str
    coluna: str
    dataReferencia: str
    reincidente: bool


class ProcessarResponse(BaseModel):
    dataReferencia: str
    total: int  # total de eventos no lote
    truncado: bool  # True se o lote foi cortado em _MAX_EVENTOS_POR_LOTE
    eventos: List[EventoProcessado]
    # Metadados de economia para logging no frontend
    _meta: Optional[dict] = None


# ═══════════════════════════════════════════════════════════════════════
# FUNÇÕES AUXILIARES
# ═══════════════════════════════════════════════════════════════════════

_RE_DATA = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_RE_SEMAF = re.compile(
    r"🚦\s*([A-Z0-9]{2,8})\s*🚦\s*(.*?)\s*●\s*([A-ZÀ-Ú\s/]+)(.*)", re.I
)
_RE_TIPO = re.compile(r"^([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ\s]+?)\s*🚦", re.I)
_RE_INICIO = re.compile(r"in[íi]cio\s*:\s*(.+)", re.I)
_RE_FIM_LABEL = re.compile(r"fim\s*:\s*(.+)", re.I)
# Extrai datas no formato dd/mm/aaaa hh:mm em qualquer posicao da linha
_RE_DATETIME = re.compile(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2})")
# Mapa de palavras-chave para tipo normalizado
_TIPO_MAP = [
    ("FALHA DE EQUIPAMENTO", "FALHA DE EQUIPAMENTO"),
    ("ENEL", "ENEL"),
    ("INVESTIGANDO", "INVESTIGANDO"),
    ("FURTO", "FURTO"),
    ("IMPROCEDENTE", "IMPROCEDENTE"),
    ("ACIDENTE", "ACIDENTE"),
    ("VANDALISMO", "VANDALISMO"),
]
_RE_DIGITS = re.compile(r"\D")
_RE_VL = re.compile(r"\bvl\b|via\s+livre", re.I)
_RE_AMC = re.compile(r"\bamc\b", re.I)
_RE_SN_CRZ = re.compile(r"cruzamento\s+(funcionando|normal|ok)", re.I)


def extrair_tipo_normalizado(linha: str) -> str:
    """Extrai tipo buscando palavras-chave na linha inteira (mais robusto que regex de prefixo)."""
    linha_up = linha.upper()
    for keyword, label in _TIPO_MAP:
        if keyword in linha_up:
            return label
    m = _RE_TIPO.search(linha)
    if m:
        candidato = m.group(1).strip()
        if candidato and candidato not in ("N/I", ""):
            return candidato
    return "N/I"


def extrair_inicio_fim_inline(linha: str) -> tuple:
    """Extrai inicio e fim de qualquer posicao na linha (dd/mm/aaaa hh:mm)."""
    matches = _RE_DATETIME.findall(linha)
    inicio = matches[0] if len(matches) >= 1 else None
    fim = matches[1] if len(matches) >= 2 else None
    return inicio, fim


def extrair_data_referencia(linhas: List[str]) -> str:
    for linha in linhas:
        m = _RE_DATA.search(linha)
        if m:
            return m.group(1)
    return datetime.now(timezone.utc).strftime("%d/%m/%Y")


def gerar_evento_id(codigo: str, inicio: str) -> str:
    if inicio:
        nums = _RE_DIGITS.sub("", inicio)[:12]
        if nums:
            return f"{codigo}_{nums}"
    # Fallback: timestamp em ms (único por chamada)
    return f"{codigo}_{int(time.time() * 1000)}"


def determinar_status(ev: dict) -> tuple[str, str]:
    """Retorna (status, coluna). Ordem de prioridade é relevante."""
    if ev["secao"] == "NORMALIZADO" or ev["fim"]:
        return "NORMALIZADO", "coluna-normalizados"

    end = ev["endereco"].lower()
    obs = ev["observacoes"].lower()
    prob = ev["problema"].lower()
    tipo = ev["tipo"].lower()
    tudo = f"{end} {obs} {prob} {tipo}"

    # SEM_NECESSIDADE: cruzamento fora de área ou já OK
    if "pgv" in end or end.startswith("entre") or _RE_SN_CRZ.search(obs):
        return "SEM_NECESSIDADE", "coluna-sem-necessidade"

    # VL / AMC: despachados diretamente no relatório
    if _RE_VL.search(tudo):
        return "vl", "coluna-vl"
    if _RE_AMC.search(tudo):
        return "amc", "coluna-amc"

    return "PENDENTE", "coluna-espera"


def _truncar_obs(obs: str) -> str:
    """
    Trunca observações para economizar storage no Firebase.
    Tenta cortar no ultimo ponto final (preserva frases completas).
    Firebase Spark: 1 GB total — campos longos sao o principal risco.
    """
    obs = obs.strip()
    if not obs:
        return None
    if len(obs) <= _MAX_OBS_CHARS:
        return obs
    ultimo_ponto = obs.rfind(".", 0, _MAX_OBS_CHARS)
    if ultimo_ponto > int(_MAX_OBS_CHARS * 0.8):
        return obs[: ultimo_ponto + 1]
    return obs[: _MAX_OBS_CHARS - 1] + "…"


def extrair_eventos_do_texto(texto: str) -> List[dict]:
    """
    Parser single-pass fiel ao formato CEMOB.
    Uma única iteração sobre as linhas — O(n).
    """
    linhas = texto.splitlines()
    eventos: List[dict] = []
    atual = None
    secao = "PENDENTE"

    for linha in linhas:
        linha_s = linha.strip()
        if not linha_s:
            continue

        # ── IGNORA LINHAS DE CABEÇALHO (NORMALIZADOS/PENDENTES) ──
        if re.search(r"(NORMALIZADOS|PENDENTES)\s*[✅❌]?", linha_s, re.IGNORECASE):
            continue

        # ── Detecta mudança de seção (mantido para compatibilidade) ──
        if "NORMALIZADOS✅" in linha_s:
            secao = "NORMALIZADO"
            continue
        if "PENDENTES❌" in linha_s:
            secao = "PENDENTE"
            continue

        # ── Novo evento: linha com 🚦 código 🚦 ──
        if "🚦" in linha_s:
            m = _RE_SEMAF.search(linha_s)
            if m:
                if atual:
                    eventos.append(atual)

                tipo = extrair_tipo_normalizado(linha_s)
                ini_inline, fim_inline = extrair_inicio_fim_inline(linha_s)

                atual = {
                    "codigo": m.group(1).strip(),
                    "endereco": m.group(2).strip().replace("  ", " "),
                    "problema": m.group(3).strip().upper(),
                    "tipo": tipo,
                    "inicio": ini_inline or "",
                    "fim": fim_inline or "",
                    "observacoes": (m.group(4) or "").strip().replace('"', ""),
                    "secao": secao,
                }
                continue

        # ── Linhas de detalhe do evento atual ──
        if atual is None:
            continue

        m_ini = _RE_INICIO.search(linha_s)
        if m_ini:
            atual["inicio"] = m_ini.group(1).strip()
            continue

        m_fim = _RE_FIM_LABEL.search(linha_s)
        if m_fim:
            if not atual["fim"]:
                atual["fim"] = m_fim.group(1).strip()
            continue

        # Acumula como observação
        obs_linha = linha_s.replace('"', "").strip()
        if obs_linha:
            atual["observacoes"] = (atual["observacoes"] + " " + obs_linha).strip()

    if atual:
        eventos.append(atual)

    return eventos


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════


@router.post(
    "/processar-relatorio",
    response_model=ProcessarResponse,
    summary="Processa relatório CEMOB bruto",
    description=(
        "Parseia o texto bruto do relatório CEMOB e retorna os eventos estruturados. "
        "O frontend envia o batch resultante ao Firebase. "
        "Limitado a 10 req/min por IP e payloads ≤ 64 KB para preservar créditos Railway/Firebase."
    ),
)
async def processar_relatorio(
    request: Request,
    req: ProcessarRequest,
    x_nit_key: str = Header(..., alias="X-NIT-Key"),
):
    # ── 1. Autenticação ──
    expected = os.getenv("NIT_SECRET_KEY", "")
    if not expected or x_nit_key != expected:
        raise HTTPException(status_code=401, detail="Chave inválida.")

    # ── 2. Rate limiting (protege CPU do Railway free) ──
    client_ip = request.headers.get(
        "X-Forwarded-For", request.client.host if request.client else "unknown"
    )
    client_ip = client_ip.split(",")[
        0
    ].strip()  # proxy reverso do Railway pode empilhar IPs
    _rate_limiter.verificar(client_ip)

    # ── 3. Cache de deduplicação (anti-duplo-clique, economiza CPU) ──
    cached = _cache.get(req.texto)
    if cached:
        return JSONResponse(content=cached, headers={"X-NIT-Cache": "HIT"})

    # ── 4. Validacao de formato antes do parsing ──
    if "🚦" not in req.texto:
        raise HTTPException(
            status_code=400,
            detail="Relatorio invalido: nao contém o marcador 🚦. Verifique se o texto foi copiado corretamente.",
        )

    # ── 5. Parsing ──
    linhas = req.texto.splitlines()
    data_ref = extrair_data_referencia(linhas)
    eventos_raw = extrair_eventos_do_texto(req.texto)

    if not eventos_raw:
        raise HTTPException(
            status_code=422,
            detail="Nenhuma ocorrencia encontrada. Verifique o formato do relatorio CEMOB.",
        )

    # ── 6. Protecao Firebase: limita tamanho do batch ──
    truncado = len(eventos_raw) > _MAX_EVENTOS_POR_LOTE
    eventos_raw = eventos_raw[:_MAX_EVENTOS_POR_LOTE]

    # ── 7. Reincidencia real: consulta batch ao Firebase (uma unica requisicao) ──
    # Usa shallow=true para trazer apenas as chaves do /kanban, sem os valores.
    # Economico em download; os codigos sao extraidos do prefixo do eventoId.
    # Se FIREBASE_URL nao estiver configurada, cai para deteccao local no lote.
    codigos_firebase: set = set()
    firebase_url = os.getenv("FIREBASE_URL", "")
    if firebase_url:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp_fb = await client.get(
                    f"{firebase_url}/kanban.json",
                    params={"shallow": "true"},
                )
            if resp_fb.status_code == 200:
                for chave in resp_fb.json() or {}:
                    codigos_firebase.add(chave.split("_")[0])
        except Exception:
            pass  # falha silenciosa — reincidencia cai para deteccao local

    # ── 8. Montagem dos eventos processados ──
    codigos_lote: set = set()
    eventos_processados: List[EventoProcessado] = []

    for ev in eventos_raw:
        ev_id = gerar_evento_id(ev["codigo"], ev["inicio"])
        status, coluna = determinar_status(ev)
        reincidente = ev["codigo"] in codigos_lote or ev["codigo"] in codigos_firebase
        codigos_lote.add(ev["codigo"])

        eventos_processados.append(
            EventoProcessado(
                eventoId=ev_id,
                codigo=ev["codigo"],
                endereco=ev["endereco"],
                problema=ev["problema"],
                tipo=ev["tipo"],
                inicio=ev["inicio"] or None,
                fim=ev["fim"] or None,
                observacoes=_truncar_obs(ev["observacoes"]),
                status=status,
                coluna=coluna,
                dataReferencia=data_ref,
                reincidente=reincidente,
            )
        )

    resposta = ProcessarResponse(
        dataReferencia=data_ref,
        total=len(eventos_processados),
        truncado=truncado,
        eventos=eventos_processados,
    )

    # ── 7. Serializa para cache e retorno ──
    resposta_dict = resposta.model_dump()
    _cache.set(req.texto, resposta_dict)

    # Avisa o frontend se o lote foi cortado
    if truncado:
        resposta_dict["_aviso"] = (
            f"Relatório truncado: apenas {_MAX_EVENTOS_POR_LOTE} de "
            f"{len(eventos_raw) + (len(eventos_raw) - _MAX_EVENTOS_POR_LOTE)} eventos processados."
        )

    return JSONResponse(content=resposta_dict)


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT DE HEALTH CHECK — evita cold start lento no Railway
# Railway dorme o serviço após 30 min de inatividade (plano free).
# Configure um cron externo (ex: cron-job.org) para pingar /health
# a cada 25 minutos e manter o serviço ativo durante o turno.
# ═══════════════════════════════════════════════════════════════════════


@router.get("/health", include_in_schema=False)
async def health():
    """
    Ping leve para manter o Railway awake durante turnos ativos.
    Custo: ~1 ms CPU, ~200 bytes de resposta.
    Configure em cron-job.org: GET /api/v1/health a cada 25 min.
    """
    return {
        "ok": True,
        "ts": int(time.time()),
        "cache_entries": len(_cache._store),
        "ips_rastreados": len(_rate_limiter._historico),
    }


# ═══════════════════════════════════════════════════════════════════════
# ENDPOINT DE CONFIG (chave para o frontend, sem expor no JS)
# ─────────────────────────────────────────────────────────────────────
# O frontend chama GET /api/config (sem autenticação) para buscar a
# chave e guardá-la em _NIT_KEY_CACHE (memória de sessão, nunca no JS
# global nem no localStorage). A chave é protegida por CORS no Railway.
# ═══════════════════════════════════════════════════════════════════════


@router.get("/config", include_in_schema=False)
async def config(request: Request):
    """
    Retorna a chave NIT para o frontend sem expô-la no código JS.
    Protegido por CORS: configure ALLOWED_ORIGINS no Railway para
    incluir apenas o domínio do painel NIT.
    """
    origin = request.headers.get("origin", "")
    allowed = os.getenv("ALLOWED_ORIGINS", "").split(",")

    # Só entrega a chave para origens explicitamente autorizadas
    if (
        allowed
        and allowed != [""]
        and not any(origin.startswith(a.strip()) for a in allowed if a.strip())
    ):
        raise HTTPException(status_code=403, detail="Origem não autorizada.")

    key = os.getenv("NIT_SECRET_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503, detail="Chave não configurada no servidor."
        )

    return {"key": key}


# ═══════════════════════════════════════════════════════════════════════
# NOTAS PARA O FIREBASE (não executadas aqui — orientações para o admin)
# ═══════════════════════════════════════════════════════════════════════
#
# 1. REGRAS DE SEGURANÇA (database.rules.json):
#    {
#      "rules": {
#        "kanban": {
#          ".read":  "auth != null || root.child('meta/modo').val() === 'operacional'",
#          ".write": "auth != null || root.child('meta/modo').val() === 'operacional'"
#        },
#        "historico": {
#          ".read":  "auth != null",
#          ".write": true,
#          "$cod": {
#            ".indexOn": ["ts"]
#          }
#        }
#      }
#    }
#
# 2. PURGA DO HISTÓRICO (recomendado — Cloud Function ou cron externo):
#    Execute esta query periódica para manter historico/ < 7 dias:
#
#    import firebase_admin
#    from firebase_admin import db
#    corte = int(time.time() * 1000) - 7 * 86400 * 1000  # 7 dias em ms
#    ref = db.reference('historico')
#    for cod_node in ref.get(shallow=True) or {}:
#        entradas = ref.child(cod_node).order_by_child('ts').end_at(corte).get()
#        for k in (entradas or {}):
#            ref.child(f"{cod_node}/{k}").delete()
#
# 3. MONITORAMENTO DE STORAGE (alerta antes de atingir 1 GB):
#    Use o Firebase Console → Realtime Database → Uso.
#    Adicione um alerta em 800 MB para ter margem de reação.
#
# 4. BATCH WRITES — boas práticas:
#    O frontend deve agrupar todas as escritas em um único update()
#    no nó raiz (/kanban) em vez de múltiplos set() individuais.
#    Ex (JS): firebase.database().ref('kanban').update(batchFirebase)
#    Isso conta como 1 operação de escrita, não N.
