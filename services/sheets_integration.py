"""
sheets_integration.py
NIT – Integração Google Sheets (Cliente B e futuros clientes)
"""

import os
import json
import logging
import time
from datetime import datetime, date
from typing import Optional

import firebase_admin
from firebase_admin import credentials, db as rtdb
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------
# Logging estruturado
# ---------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("nit.sheets")

# ---------------------------------------------
# Google Sheets – autenticação
# ---------------------------------------------
_sheets_service = None

def get_sheets_service():
    """Retorna o serviço autenticado do Google Sheets (singleton)."""
    global _sheets_service
    if _sheets_service:
        return _sheets_service

    creds_raw = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_raw:
        raise EnvironmentError("GOOGLE_SHEETS_CREDENTIALS não configurado.")

    creds_dict = json.loads(creds_raw)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    _sheets_service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _sheets_service


# ---------------------------------------------
# Helpers de data/hora
# ---------------------------------------------

def _fmt_date(val: Optional[str]) -> str:
    """Normaliza datas para DD/MM/AAAA (aceita AAAA-MM-DD ou DD/MM/AAAA)."""
    if not val:
        return ""
    val = str(val).strip()
    if "-" in val:
        try:
            d = datetime.strptime(val[:10], "%Y-%m-%d")
            return d.strftime("%d/%m/%Y")
        except ValueError:
            pass
    return val  # já está no formato correto ou livre


def _fmt_time(val: Optional[str]) -> str:
    """Retorna hora no formato HH:MM."""
    if not val:
        return ""
    return str(val).strip()[:5]  # trunca segundos se existirem


def calcular_tempo_atendimento(
    data_inicio: str,
    hora_inicio: str,
    data_fim: str,
    hora_fim: str,
) -> str:
    """
    Calcula duração entre início e fim.
    Aceita datas em DD/MM/YYYY ou YYYY-MM-DD.
    Retorna string HH:MM:SS ou '' em caso de dados incompletos.
    """
    def _normalizar_data(data: str) -> str:
        """Converte DD/MM/YYYY → YYYY-MM-DD para compatibilidade com strptime."""
        data = str(data).strip()[:10]
        if "/" in data:
            partes = data.split("/")
            if len(partes) == 3:
                return f"{partes[2]}-{partes[1]}-{partes[0]}"
        return data  # já está em YYYY-MM-DD ou formato livre

    try:
        d_ini = _normalizar_data(data_inicio)
        d_fim = _normalizar_data(data_fim)
        h_ini = str(hora_inicio).strip()[:5]
        h_fim = str(hora_fim).strip()[:5]

        if not d_ini or not d_fim or not h_ini or not h_fim:
            return ""

        dt_in  = datetime.strptime(f"{d_ini} {h_ini}", "%Y-%m-%d %H:%M")
        dt_fim = datetime.strptime(f"{d_fim} {h_fim}", "%Y-%m-%d %H:%M")
        delta  = dt_fim - dt_in
        if delta.total_seconds() < 0:
            return ""
        total_sec = int(delta.total_seconds())
        hh = total_sec // 3600
        mm = (total_sec % 3600) // 60
        ss = total_sec % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except Exception as exc:
        log.warning("calcular_tempo_atendimento falhou: %s", exc)
        return ""


def _concat_data_hora(data: str, hora: str) -> str:
    """Monta concatenação DATA_HORA no padrão DD/MM/AAAA HH:MM."""
    d = _fmt_date(data)
    h = _fmt_time(hora)
    if d and h:
        return f"{d} {h}"
    return d or h


# ---------------------------------------------
# Mapeamento Firebase → linha da planilha
# ---------------------------------------------

def _montar_linha_nova(dados: dict, cfg: dict, id_ocorrencia: str) -> list:
    """
    Converte um dict do Firebase em lista de valores ordenada por coluna.
    Ordem: A→R (incluindo coluna oculta R = ID_OCORRENCIA).
    """
    mp = cfg["mapeamento_firebase"]

    def fb(campo_planilha: str) -> str:
        chave = mp.get(campo_planilha, "")
        return str(dados.get(chave, "")).strip() if chave else ""

    # Campo "inicio" pode conter "DD/MM/AAAA HH:MM" — separar data e hora
    inicio_raw = fb("data_inicio")  # mapeado para "inicio" no Firebase
    if " " in inicio_raw:
        data_inicio_raw, hora_inicio_raw = inicio_raw.split(" ", 1)
    else:
        data_inicio_raw = inicio_raw
        hora_inicio_raw = fb("hora_inicio")

    # Tradução do campo pl → STATUS_ATUAL legível
    pl_map = {"norm": "NORMALIZADO", "atend": "EM ATENDIMENTO", "aguard": "AGUARDANDO"}
    sub_map = {"vl": "VIA LIVRE", "amc": "AMC"}
    status_atual_raw = fb("status_atual")
    status_atual = pl_map.get(status_atual_raw, status_atual_raw.upper() if status_atual_raw else "")

    row = [
        fb("scn"),                                              # A – SCN
        fb("localizacao"),                                      # B – LOCALIZAÇÃO
        _fmt_date(data_inicio_raw),                             # C – DATA_PLANTAO (usa data_inicio)
        fb("plantonista"),                                      # D – PLANTONISTA
        fb("status_falha").upper() if fb("status_falha") else "",  # E – STATUS_FALHA
        fb("causa").upper() if fb("causa") else "",             # F – CAUSA
        _fmt_date(data_inicio_raw),                             # G – DATA_INICIO
        _fmt_time(hora_inicio_raw),                             # H – HORA_INICIO
        _concat_data_hora(data_inicio_raw, hora_inicio_raw),    # I – DATA_HORA_IN
        "",                                                     # J – DATA_FIM (vazio na criação)
        "",                                                     # K – HORA_FIM
        "",                                                     # L – DATA_HORA_FIM
        status_atual or "AGUARDANDO",                           # M – STATUS_ATUAL
        sub_map.get(str(dados.get("sub", "")).strip().lower(), fb("operando_cruzamento")),  # N – OPERANDO_CRUZAMENTO
        "",                                                     # O – TEMPO DE ATENDIMENTO
        fb("bairro"),                                           # P – BAIRRO
        fb("observacoes"),                                      # Q – OBSERVAÇÕES
        id_ocorrencia,                                          # R – ID_OCORRENCIA (oculta)
    ]
    return row


# ---------------------------------------------
# Retry exponencial
# ---------------------------------------------

def _retry(fn, max_tries=3, base_delay=2):
    """Executa fn com retry exponencial. Lança a última exceção se esgotar."""
    for attempt in range(1, max_tries + 1):
        try:
            return fn()
        except HttpError as exc:
            if exc.resp.status in (429, 500, 503) and attempt < max_tries:
                delay = base_delay ** attempt
                log.warning("HTTP %s – tentativa %d/%d, aguardando %ds",
                            exc.resp.status, attempt, max_tries, delay)
                time.sleep(delay)
            else:
                raise


# ---------------------------------------------
# Cache em memória: coluna R por planilha
# ---------------------------------------------
# Chave: (spreadsheet_id, sheet_name)
# Valor: dict {id_ocorrencia: row_number_1based}
# Poupa chamadas à API quando várias ocorrências são normalizadas no mesmo ciclo.
# Invalidar com _invalidar_cache_linha() sempre que o append inserir uma nova linha.
_cache_coluna_r: dict = {}

def _invalidar_cache_linha(spreadsheet_id: str, sheet_name: str):
    """Descarta o cache da coluna R para forçar releitura no próximo ciclo."""
    _cache_coluna_r.pop((spreadsheet_id, sheet_name), None)


# ---------------------------------------------
# Localizar linha pelo ID_OCORRENCIA (coluna R)
# ---------------------------------------------

def _encontrar_linha(service, spreadsheet_id: str, sheet_name: str, id_ocorrencia: str) -> Optional[int]:
    """
    Percorre a coluna R para encontrar o índice (1-based) da linha com o ID.
    Usa cache em memória dentro do mesmo ciclo de exportação: a coluna R é lida
    apenas uma vez por planilha, independente de quantas ocorrências precisem
    ser localizadas (eficiente para planilhas com milhares de linhas).
    Retorna None se não encontrar.
    """
    cache_key = (spreadsheet_id, sheet_name)

    if cache_key not in _cache_coluna_r:
        range_r = f"'{sheet_name}'!R:R"

        def _read():
            return (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_r)
                .execute()
            )

        result = _retry(_read)
        values = result.get("values", [])
        _cache_coluna_r[cache_key] = {
            cell[0]: i + 1
            for i, cell in enumerate(values)
            if cell and cell[0]
        }
        log.debug("cache coluna R carregado | planilha=%s | entradas=%d",
                  sheet_name, len(_cache_coluna_r[cache_key]))

    return _cache_coluna_r[cache_key].get(str(id_ocorrencia))


# ---------------------------------------------
# API pública
# ---------------------------------------------

def append_nova_ocorrencia(
    cliente_config: dict,
    dados_ocorrencia: dict,
    id_ocorrencia: str,
    dry_run: bool = False,
) -> bool:
    """
    Adiciona uma nova linha ao final da planilha.

    Args:
        cliente_config: bloco do clientes.json para o cliente
        dados_ocorrencia: dict vindo do Firebase
        id_ocorrencia: chave do nó no Firebase (usada como ID único na coluna R)
        dry_run: se True, apenas loga sem escrever

    Returns:
        True em sucesso, False em falha
    """
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name     = cliente_config["sheet_name"]
    row            = _montar_linha_nova(dados_ocorrencia, cliente_config, id_ocorrencia)

    log.info("APPEND | id=%s | scn=%s | dry_run=%s", id_ocorrencia, row[0], dry_run)

    if dry_run:
        log.info("DRY_RUN – linha que seria inserida: %s", row)
        return True

    service = get_sheets_service()
    range_a1 = f"'{sheet_name}'!A:R"

    def _write():
        return (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )

    try:
        result = _retry(_write)
        log.info("APPEND OK | updates=%s", result.get("updates", {}).get("updatedRows"))
        # Invalida cache da coluna R: a nova linha mudou o índice potencial de buscas futuras
        _invalidar_cache_linha(spreadsheet_id, sheet_name)
        return True
    except Exception as exc:
        log.error("APPEND FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False


def update_ocorrencia_normalizada(
    cliente_config: dict,
    id_ocorrencia: str,
    dados_fim: dict,
    dry_run: bool = False,
) -> bool:
    """
    Atualiza as colunas de finalização de uma ocorrência já existente na planilha.

    Args:
        cliente_config: bloco do clientes.json
        id_ocorrencia: ID Firebase para localizar a linha
        dados_fim: dict com data_fim, hora_fim, data_inicio, hora_inicio, equipe, pl, etc.
        dry_run: se True, não escreve

    Returns:
        True em sucesso, False em falha
    """
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name     = cliente_config["sheet_name"]

    # Firebase guarda início como campo combinado "DD/MM/YYYY HH:MM" (campo 'inicio').
    # Separar data e hora para calcular o tempo de atendimento.
    inicio_raw  = str(dados_fim.get("inicio", "")).strip()
    if " " in inicio_raw:
        data_inicio, hora_inicio = inicio_raw.split(" ", 1)
    else:
        data_inicio = inicio_raw
        hora_inicio = ""

    data_fim    = str(dados_fim.get("data_fim",    "")).strip()
    hora_fim    = str(dados_fim.get("hora_fim",    "")).strip()

    pl_map = {"norm": "NORMALIZADO", "atend": "EM ATENDIMENTO", "aguard": "AGUARDANDO"}
    sub_map = {"vl": "VIA LIVRE", "amc": "AMC"}
    pl_raw = str(dados_fim.get("pl", "")).strip()
    status_atual = pl_map.get(pl_raw, pl_raw.upper() if pl_raw else "NORMALIZADO")

    tempo = calcular_tempo_atendimento(data_inicio, hora_inicio, data_fim, hora_fim)

    log.info("UPDATE | id=%s | data_fim=%s | hora_fim=%s | tempo=%s | dry_run=%s",
             id_ocorrencia, data_fim, hora_fim, tempo, dry_run)

    if dry_run:
        log.info("DRY_RUN – dados que seriam atualizados: data_fim=%s, hora_fim=%s, tempo=%s, status=%s",
                 data_fim, hora_fim, tempo, status_atual)
        return True

    service = get_sheets_service()

    row_num = _encontrar_linha(service, spreadsheet_id, sheet_name, id_ocorrencia)
    if row_num is None:
        log.error("UPDATE FAIL | id=%s | linha não encontrada na planilha", id_ocorrencia)
        return False

    # ── Colunas J, K, L, M — sempre atualizadas na normalização ──────────
    updates = [
        (f"'{sheet_name}'!J{row_num}", _fmt_date(data_fim)),
        (f"'{sheet_name}'!K{row_num}", _fmt_time(hora_fim)),
        (f"'{sheet_name}'!L{row_num}", _concat_data_hora(data_fim, hora_fim)),
        (f"'{sheet_name}'!M{row_num}", status_atual),
    ]

    # ── Coluna O (Tempo de Atendimento) — write-once ──────────────────────
    # Só grava se: (1) o cálculo retornou valor e (2) a célula está vazia.
    # Evita sobrescrever um tempo correto caso o cron rode duas vezes
    # ou o cálculo falhe por dados incompletos.
    if tempo:
        try:
            result_o = _retry(lambda: service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"'{sheet_name}'!O{row_num}",
            ).execute())
            valor_o_atual = (result_o.get("values") or [[""]])[0][0] if result_o.get("values") else ""
        except Exception as exc:
            log.warning("Leitura coluna O falhou | id=%s | erro=%s — assumindo vazia", id_ocorrencia, exc)
            valor_o_atual = ""

        if not valor_o_atual:
            updates.append((f"'{sheet_name}'!O{row_num}", tempo))
            log.info("UPDATE | id=%s | coluna O → %s", id_ocorrencia, tempo)
        else:
            log.debug("UPDATE | id=%s | coluna O já preenchida (%s) — mantendo", id_ocorrencia, valor_o_atual)
    else:
        log.warning("UPDATE | id=%s | tempo de atendimento não calculado — coluna O não alterada", id_ocorrencia)

    data_body = {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": rng, "values": [[val]]}
            for rng, val in updates
        ],
    }

    def _write():
        return (
            service.spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=data_body)
            .execute()
        )

    try:
        result = _retry(_write)
        log.info("UPDATE OK | id=%s | row=%d | totalUpdated=%s",
                 id_ocorrencia, row_num,
                 result.get("totalUpdatedCells"))
        return True
    except Exception as exc:
        log.error("UPDATE FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False


# ---------------------------------------------
# Cursor Firebase
# ---------------------------------------------

def get_cursor(cursor_node: str) -> dict:
    """
    Lê o cursor de exportação do Firebase.
    Retorna {'ultimo_ts': ..., 'ultima_atualizacao': ...}
    """
    try:
        ref = rtdb.reference(cursor_node)
        val = ref.get() or {}
        return {
            "ultimo_ts": val.get("ultimo_ts", 0),
            "ultima_atualizacao": val.get("ultima_atualizacao", 0),
        }
    except Exception as exc:
        log.error("get_cursor FAIL | node=%s | erro=%s", cursor_node, exc)
        return {"ultimo_ts": 0, "ultima_atualizacao": 0}


def update_cursor(cursor_node: str, ultimo_ts: Optional[int] = None,
                  ultima_atualizacao: Optional[int] = None):
    """Atualiza o cursor de exportação no Firebase."""
    try:
        ref = rtdb.reference(cursor_node)
        patch = {}
        if ultimo_ts is not None:
            patch["ultimo_ts"] = ultimo_ts
        if ultima_atualizacao is not None:
            patch["ultima_atualizacao"] = ultima_atualizacao
        if patch:
            ref.update(patch)
            log.info("cursor atualizado | node=%s | patch=%s", cursor_node, patch)
    except Exception as exc:
        log.error("update_cursor FAIL | node=%s | erro=%s", cursor_node, exc)
