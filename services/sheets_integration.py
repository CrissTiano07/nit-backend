"""
sheets_integration.py
NIT – Integração Google Sheets (Cliente B e futuros clientes)
"""

import os
import json
import logging
import time
from datetime import datetime
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
    return val


def _fmt_time(val: Optional[str]) -> str:
    """Retorna hora no formato HH:MM."""
    if not val:
        return ""
    return str(val).strip()[:5]


def calcular_tempo_atendimento(
    data_inicio: str,
    hora_inicio: str,
    data_fim: str,
    hora_fim: str,
) -> str:
    """
    Calcula duração entre início e fim.
    Retorna string HH:MM:SS ou '' em caso de dados incompletos.
    """
    # Verifica se os dados de fim existem
    if not data_fim or not hora_fim or data_fim.strip() == "" or hora_fim.strip() == "":
        return ""
    
    try:
        # Tenta converter DD/MM/AAAA ou AAAA-MM-DD
        data_inicio_clean = data_inicio.strip()
        data_fim_clean = data_fim.strip()
        
        if "/" in data_inicio_clean:
            dia, mes, ano = data_inicio_clean.split("/")
            data_inicio_clean = f"{ano}-{mes}-{dia}"
        if "/" in data_fim_clean:
            dia, mes, ano = data_fim_clean.split("/")
            data_fim_clean = f"{ano}-{mes}-{dia}"
        
        dt_in = datetime.strptime(f"{data_inicio_clean} {hora_inicio[:5]}", "%Y-%m-%d %H:%M")
        dt_fim = datetime.strptime(f"{data_fim_clean} {hora_fim[:5]}", "%Y-%m-%d %H:%M")
        delta = dt_fim - dt_in
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
    inicio_raw = fb("data_inicio")
    if " " in inicio_raw:
        data_inicio_raw, hora_inicio_raw = inicio_raw.split(" ", 1)
    else:
        data_inicio_raw = inicio_raw
        hora_inicio_raw = fb("hora_inicio")

    # Tradução do campo status → STATUS_ATUAL legível
    status_map = {"NORMALIZADO": "NORMALIZADO", "PENDENTE": "AGUARDANDO"}
    status_atual_raw = fb("status_atual")
    status_atual = status_map.get(status_atual_raw, status_atual_raw.upper() if status_atual_raw else "AGUARDANDO")

    sub_map = {"vl": "VIA LIVRE", "amc": "AMC"}
    sub_val = str(dados.get("sub", "")).strip().lower()

    row = [
        fb("scn"),                                                                  # A
        fb("localizacao"),                                                          # B
        _fmt_date(data_inicio_raw),                                                 # C – DATA_PLANTAO
        fb("plantonista"),                                                          # D
        fb("status_falha").upper() if fb("status_falha") else "",                    # E
        fb("causa").upper() if fb("causa") else "",                                  # F
        _fmt_date(data_inicio_raw),                                                 # G – DATA_INICIO
        _fmt_time(hora_inicio_raw),                                                 # H – HORA_INICIO
        _concat_data_hora(data_inicio_raw, hora_inicio_raw),                        # I – DATA_HORA_IN
        "",                                                                         # J – DATA_FIM
        "",                                                                         # K – HORA_FIM
        "",                                                                         # L – DATA_HORA_FIM
        status_atual,                                                               # M – STATUS_ATUAL
        sub_map.get(sub_val, ""),                                                   # N – OPERANDO_CRUZAMENTO
        "",                                                                         # O – TEMPO ATENDIMENTO
        fb("bairro"),                                                               # P
        fb("observacoes"),                                                          # Q
        id_ocorrencia,                                                              # R – ID_OCORRENCIA
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
_cache_coluna_r: dict = {}

def _invalidar_cache_linha(spreadsheet_id: str, sheet_name: str):
    """Descarta o cache da coluna R para forçar releitura no próximo ciclo."""
    _cache_coluna_r.pop((spreadsheet_id, sheet_name), None)


def _encontrar_linha(service, spreadsheet_id: str, sheet_name: str, id_ocorrencia: str) -> Optional[int]:
    """Localiza linha pelo ID_OCORRENCIA na coluna R (usando cache)."""
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
# API pública – APPEND
# ---------------------------------------------

def append_nova_ocorrencia(
    cliente_config: dict,
    dados_ocorrencia: dict,
    id_ocorrencia: str,
    dry_run: bool = False,
) -> bool:
    """Adiciona uma nova linha ao final da planilha."""
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name = cliente_config["sheet_name"]
    row = _montar_linha_nova(dados_ocorrencia, cliente_config, id_ocorrencia)

    log.info("APPEND | id=%s | scn=%s | dry_run=%s", id_ocorrencia, row[0], dry_run)

    if dry_run:
        log.info("DRY_RUN – linha: %s", row)
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
        _invalidar_cache_linha(spreadsheet_id, sheet_name)
        return True
    except Exception as exc:
        log.error("APPEND FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False


# ---------------------------------------------
# API pública – UPDATE (normalização)
# ---------------------------------------------

def update_ocorrencia_normalizada(
    cliente_config: dict,
    id_ocorrencia: str,
    dados_fim: dict,
    dry_run: bool = False,
) -> bool:
    """Atualiza colunas de finalização (J, K, L, M, O)."""
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name = cliente_config["sheet_name"]

    data_inicio = str(dados_fim.get("data_inicio", "")).strip()
    hora_inicio = str(dados_fim.get("hora_inicio", "")).strip()
    data_fim = str(dados_fim.get("data_fim", "")).strip()
    hora_fim = str(dados_fim.get("hora_fim", "")).strip()

    status_raw = dados_fim.get("status", "")
    status_atual = "NORMALIZADO" if status_raw == "NORMALIZADO" else "AGUARDANDO"

    tempo = calcular_tempo_atendimento(data_inicio, hora_inicio, data_fim, hora_fim)

    log.info("UPDATE | id=%s | data_fim=%s | hora_fim=%s | tempo=%s | dry_run=%s",
             id_ocorrencia, data_fim, hora_fim, tempo, dry_run)

    if dry_run:
        return True

    service = get_sheets_service()
    row_num = _encontrar_linha(service, spreadsheet_id, sheet_name, id_ocorrencia)
    if row_num is None:
        log.warning("UPDATE | linha não encontrada para id=%s", id_ocorrencia)
        return False

    updates = [
        (f"'{sheet_name}'!J{row_num}", _fmt_date(data_fim)),
        (f"'{sheet_name}'!K{row_num}", _fmt_time(hora_fim)),
        (f"'{sheet_name}'!L{row_num}", _concat_data_hora(data_fim, hora_fim)),
        (f"'{sheet_name}'!M{row_num}", status_atual),
        (f"'{sheet_name}'!O{row_num}", tempo),
    ]

    data_body = {
        "valueInputOption": "USER_ENTERED",
        "data": [{"range": rng, "values": [[val]]} for rng, val in updates],
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
        log.info("UPDATE OK | id=%s | row=%d", id_ocorrencia, row_num)
        return True
    except Exception as exc:
        log.error("UPDATE FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False


# ---------------------------------------------
# API pública – UPDATE DATA_PLANTAO (herança)
# ---------------------------------------------

def update_data_plantao(
    cliente_config: dict,
    id_ocorrencia: str,
    nova_data: str,
    dry_run: bool = False,
) -> bool:
    """
    Atualiza apenas a coluna C (DATA_PLANTAO) na planilha.
    Usado para herança de pendências entre dias.
    """
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name = cliente_config["sheet_name"]

    log.info("DATA_REF | id=%s | nova_data=%s | dry_run=%s", id_ocorrencia, nova_data, dry_run)

    if dry_run:
        return True

    service = get_sheets_service()
    row_num = _encontrar_linha(service, spreadsheet_id, sheet_name, id_ocorrencia)
    if row_num is None:
        log.warning("DATA_REF | linha não encontrada para id=%s", id_ocorrencia)
        return False

    range_c = f"'{sheet_name}'!C{row_num}"

    def _write():
        return (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_c,
                valueInputOption="RAW",
                body={"values": [[nova_data]]},
            )
            .execute()
        )

    try:
        result = _retry(_write)
        log.info("DATA_REF OK | id=%s | row=%d | nova_data=%s", id_ocorrencia, row_num, nova_data)
        return True
    except Exception as exc:
        log.error("DATA_REF FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Substitui a lógica de "sobrescrever coluna C" por "inserir nova linha"
# ─────────────────────────────────────────────────────────────────────────────

def append_heranca_diaria(
    cliente_config: dict,
    dados_ocorrencia: dict,
    id_ocorrencia: str,
    data_plantao: str,
    dry_run: bool = False,
) -> bool:
    """
    Insere uma nova linha para ocorrência pendente herdada entre dias.

    Regras:
      - Coluna R (ID_OCORRENCIA) → mesmo id_ocorrencia original
      - Coluna G (DATA_INICIO)   → data do primeiro dia (vem do Firebase via dados_ocorrencia)
      - Coluna C (DATA_PLANTAO)  → data_plantao (data do plantão atual, passada explicitamente)
      - Colunas J/K/L/M/O        → vazias (ainda pendente)

    Deduplicação: se já existe uma linha com esse id_ocorrencia E essa data_plantao
    na coluna C, a inserção é ignorada (evita duplicatas em re-execuções).
    """
    spreadsheet_id = cliente_config["spreadsheet_id"]
    sheet_name     = cliente_config["sheet_name"]

    # Monta a linha com os dados originais do Firebase
    row = _montar_linha_nova(dados_ocorrencia, cliente_config, id_ocorrencia)

    # Sobrescreve coluna C com a data do plantão atual (índice 2)
    row[2] = _fmt_date(data_plantao)

    log.info(
        "HERANÇA APPEND | id=%s | data_plantao=%s | data_inicio=%s | dry_run=%s",
        id_ocorrencia, row[2], row[6], dry_run,
    )

    if dry_run:
        log.info("DRY_RUN HERANÇA – linha: %s", row)
        return True

    service = get_sheets_service()

    # ── Deduplicação: evita inserir a mesma data_plantao duas vezes ──────────
    # Lê colunas C e R para checar par (id_ocorrencia, data_plantao)
    try:
        result_cr = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!C:R",
        ).execute()
        valores_cr = result_cr.get("values", [])
        col_c_idx = 0          # C é a 1ª coluna do range C:R
        col_r_idx = 15         # R é a 16ª coluna do range C:R (R - C = 15)
        data_plantao_fmt = _fmt_date(data_plantao)
        for linha_vals in valores_cr:
            c_val = linha_vals[col_c_idx] if len(linha_vals) > col_c_idx else ""
            r_val = linha_vals[col_r_idx] if len(linha_vals) > col_r_idx else ""
            if r_val == id_ocorrencia and c_val == data_plantao_fmt:
                log.info(
                    "HERANÇA SKIP (já existe) | id=%s | data_plantao=%s",
                    id_ocorrencia, data_plantao_fmt,
                )
                return True   # Considera sucesso — não é falha, só duplicata
    except Exception as exc:
        log.warning("HERANÇA dedup check falhou | id=%s | erro=%s – prosseguindo", id_ocorrencia, exc)

    # ── Append ───────────────────────────────────────────────────────────────
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
        log.info(
            "HERANÇA APPEND OK | id=%s | data_plantao=%s | updatedRows=%s",
            id_ocorrencia, row[2], result.get("updates", {}).get("updatedRows"),
        )
        _invalidar_cache_linha(spreadsheet_id, sheet_name)
        return True
    except Exception as exc:
        log.error("HERANÇA APPEND FAIL | id=%s | erro=%s", id_ocorrencia, exc)
        return False



# ---------------------------------------------
# Cursor Firebase
# ---------------------------------------------

def get_cursor(cursor_node: str) -> dict:
    """Lê o cursor de exportação do Firebase."""
    try:
        ref = rtdb.reference(cursor_node)
        val = ref.get() or {}
        return {
            "ultimo_ts": val.get("ultimo_ts", 0),
            "ultima_atualizacao": val.get("ultima_atualizacao", 0),
            "ultimo_dataReferencia": val.get("ultimo_dataReferencia", 0),
        }
    except Exception as exc:
        log.error("get_cursor FAIL | node=%s | erro=%s", cursor_node, exc)
        return {"ultimo_ts": 0, "ultima_atualizacao": 0, "ultimo_dataReferencia": 0}


def update_cursor(
    cursor_node: str,
    ultimo_ts: Optional[int] = None,
    ultima_atualizacao: Optional[int] = None,
    ultimo_dataReferencia: Optional[int] = None,
):
    """Atualiza o cursor de exportação no Firebase."""
    try:
        ref = rtdb.reference(cursor_node)
        patch = {}
        if ultimo_ts is not None:
            patch["ultimo_ts"] = ultimo_ts
        if ultima_atualizacao is not None:
            patch["ultima_atualizacao"] = ultima_atualizacao
        if ultimo_dataReferencia is not None:
            patch["ultimo_dataReferencia"] = ultimo_dataReferencia
        if patch:
            ref.update(patch)
            log.info("cursor atualizado | node=%s | patch=%s", cursor_node, patch)
    except Exception as exc:
        log.error("update_cursor FAIL | node=%s | erro=%s", cursor_node, exc)
