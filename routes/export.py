"""
app/routes/export.py
Endpoint manual de exportação NIT → Google Sheets
"""

import os
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
import firebase_admin
from firebase_admin import db as rtdb

from services.sheets_integration import (
    append_nova_ocorrencia,
    update_ocorrencia_normalizada,
    update_data_plantao,           # ← NOVA FUNÇÃO
    get_cursor,
    update_cursor,
)

log = logging.getLogger("nit.export")
router = APIRouter()


# ─────────────────────────────────────────────
# Carrega configuração de clientes
# ─────────────────────────────────────────────

_CAMPOS_OBRIGATORIOS = [
    "spreadsheet_id",
    "sheet_name",
    "cursor_node",
    "mapeamento_firebase",
    "colunas",
]

def _load_clientes_config() -> dict:
    config_path = os.environ.get("CLIENTES_CONFIG", "config/clientes.json")
    with open(config_path, "r", encoding="utf-8") as f:
        clientes = json.load(f)

    for cliente_id, cfg in clientes.items():
        faltando = [c for c in _CAMPOS_OBRIGATORIOS if not cfg.get(c)]
        if faltando:
            raise ValueError(
                f"Cliente '{cliente_id}' no clientes.json está incompleto. "
                f"Campos obrigatórios ausentes ou vazios: {faltando}"
            )
        if cfg["spreadsheet_id"] == "SUBSTITUA_PELO_ID_DA_PLANILHA":
            raise ValueError(
                f"Cliente '{cliente_id}': spreadsheet_id ainda é o valor placeholder. "
                "Substitua pelo ID real antes do deploy."
            )

    return clientes


# ─────────────────────────────────────────────
# Lógica central de exportação (reutilizada pelo cron)
# ─────────────────────────────────────────────

def executar_exportacao(
    cliente_id: str,
    cliente_config: dict,
    dry_run: bool = False,
) -> dict:
    """
    Executa o ciclo completo de exportação para um cliente.
    Retorna dict com métricas da execução.
    """
    cursor_node  = cliente_config["cursor_node"]
    ocorrencias_node = cliente_config.get("ocorrencias_node", "/ocorrencias")

    cursor = get_cursor(cursor_node)
    ultimo_ts               = cursor.get("ultimo_ts", 0)
    ultima_atualizacao      = cursor.get("ultima_atualizacao", 0)
    ultimo_dataReferencia   = cursor.get("ultimo_dataReferencia", 0)   # ← NOVO

    log.info("[%s] iniciando exportação | ultimo_ts=%s | ultima_atualizacao=%s | ultimo_dataReferencia=%s",
             cliente_id, ultimo_ts, ultima_atualizacao, ultimo_dataReferencia)

    ref_oc = rtdb.reference(ocorrencias_node)

    # ── 1. Novas ocorrências (ts > ultimo_ts) ──────────────────────────
    novas_query = (
        ref_oc.order_by_child("ts")
              .start_at(ultimo_ts + 1)
              .get()
    ) or {}

    inseridos = 0
    novo_ts   = ultimo_ts

    for id_oc, dados in novas_query.items():
        if not isinstance(dados, dict):
            continue

        if not dry_run:
            from services.sheets_integration import get_sheets_service, _encontrar_linha
            try:
                svc = get_sheets_service()
                linha_existente = _encontrar_linha(
                    svc,
                    cliente_config["spreadsheet_id"],
                    cliente_config["sheet_name"],
                    id_oc,
                )
                if linha_existente:
                    log.info("SKIP | id=%s já existe na linha %d – ignorando append", id_oc, linha_existente)
                    ts_oc = dados.get("ts", 0)
                    if ts_oc > novo_ts:
                        novo_ts = ts_oc
                    continue
            except Exception as exc:
                log.warning("dedup check falhou para id=%s: %s – prosseguindo com append", id_oc, exc)

        ok = append_nova_ocorrencia(cliente_config, dados, id_oc, dry_run=dry_run)
        if ok:
            inseridos += 1
            ts_oc = dados.get("ts", 0)
            if ts_oc > novo_ts:
                novo_ts = ts_oc
        else:
            log.error("[%s] APPEND falhou para id=%s – cursor NÃO avançado", cliente_id, id_oc)
            break
    else:
        if novo_ts > ultimo_ts and not dry_run:
            update_cursor(cursor_node, ultimo_ts=novo_ts)

    # ── 2. Ocorrências normalizadas (ts_norm > ultima_atualizacao) ──────
    norm_query = (
        ref_oc.order_by_child("ts_norm")
              .start_at(ultima_atualizacao + 1)
              .get()
    ) or {}

    if not norm_query:
        log.warning(
            "[%s] ts_norm não encontrado – usando fallback por status==NORMALIZADO + ts. "
            "Considere gravar ts_norm no Firebase.",
            cliente_id,
        )
        all_oc = ref_oc.get() or {}
        norm_query = {
            k: v for k, v in all_oc.items()
            if isinstance(v, dict) and v.get("status") == "NORMALIZADO"
            and v.get("ts_norm", v.get("ts", 0)) > ultima_atualizacao
        }

    atualizados     = 0
    nova_atualizacao = ultima_atualizacao

    for id_oc, dados in norm_query.items():
        if not isinstance(dados, dict):
            continue
        if dados.get("status") != "NORMALIZADO":
            continue

        ok = update_ocorrencia_normalizada(cliente_config, id_oc, dados, dry_run=dry_run)
        if ok:
            atualizados += 1
            ts_oc = dados.get("ts_norm") or dados.get("ts", 0)
            if ts_oc > nova_atualizacao:
                nova_atualizacao = ts_oc
        else:
            log.warning("[%s] UPDATE falhou id=%s – linha inexistente, tentando APPEND", cliente_id, id_oc)
            ok_append = append_nova_ocorrencia(cliente_config, dados, id_oc, dry_run=dry_run)
            if ok_append:
                atualizados += 1
                ts_oc = dados.get("ts_norm") or dados.get("ts", 0)
                if ts_oc > nova_atualizacao:
                    nova_atualizacao = ts_oc
            else:
                log.error("[%s] APPEND fallback falhou id=%s – cursor NÃO avançado", cliente_id, id_oc)
                break

    if nova_atualizacao > ultima_atualizacao and not dry_run:
        update_cursor(cursor_node, ultima_atualizacao=nova_atualizacao)

    # ── 3. Herança de dataReferencia (ts_dataReferencia > ultimo_dataReferencia) ──
    dataRef_query = (
        ref_oc.order_by_child("ts_dataReferencia")
              .start_at(ultimo_dataReferencia + 1)
              .get()
    ) or {}

    herdados = 0
    novo_dataReferencia = ultimo_dataReferencia

    for id_oc, dados in dataRef_query.items():
        if not isinstance(dados, dict):
            continue
        nova_data = dados.get("dataReferencia", "")
        if not nova_data:
            continue

        ok = update_data_plantao(cliente_config, id_oc, nova_data, dry_run=dry_run)
        if ok:
            herdados += 1
            ts_oc = dados.get("ts_dataReferencia", 0)
            if ts_oc > novo_dataReferencia:
                novo_dataReferencia = ts_oc
        else:
            log.error("[%s] DATA_REFERENCIA falhou para id=%s – cursor NÃO avançado", cliente_id, id_oc)
            break
    else:
        if novo_dataReferencia > ultimo_dataReferencia and not dry_run:
            update_cursor(cursor_node, ultimo_dataReferencia=novo_dataReferencia)

    # ── 4. Despachos (sub != "") ────────────────────────────────────────
    all_oc_despacho = ref_oc.get() or {}
    despacho_query = {
        k: v for k, v in all_oc_despacho.items()
        if isinstance(v, dict) and str(v.get("sub", "")).strip().lower() in ("vl", "amc")
    }

    despachados = 0
    from services.sheets_integration import get_sheets_service, _encontrar_linha

    for id_oc, dados in despacho_query.items():
        if not isinstance(dados, dict):
            continue
        sub = str(dados.get("sub", "")).strip().lower()
        if not sub:
            continue

        sub_map = {"vl": "VIA LIVRE", "amc": "AMC"}
        valor_n = sub_map.get(sub, sub.upper())

        if not dry_run:
            try:
                svc = get_sheets_service()
                row_num = _encontrar_linha(
                    svc,
                    cliente_config["spreadsheet_id"],
                    cliente_config["sheet_name"],
                    id_oc,
                )
                if row_num:
                    sheet_name = cliente_config["sheet_name"]
                    atual = svc.spreadsheets().values().get(
                        spreadsheetId=cliente_config["spreadsheet_id"],
                        range=f"'{sheet_name}'!N{row_num}",
                    ).execute()
                    valor_atual = (atual.get("values") or [[""]])[0][0] if atual.get("values") else ""
                    if valor_atual == valor_n:
                        log.debug("DESPACHO SKIP | id=%s | coluna_n já=%s", id_oc, valor_n)
                        continue
                    svc.spreadsheets().values().batchUpdate(
                        spreadsheetId=cliente_config["spreadsheet_id"],
                        body={
                            "valueInputOption": "RAW",
                            "data": [{"range": f"'{sheet_name}'!N{row_num}", "values": [[valor_n]]}],
                        },
                    ).execute()
                    log.info("DESPACHO OK | id=%s | coluna_n=%s | row=%d", id_oc, valor_n, row_num)
                    despachados += 1
            except Exception as exc:
                log.error("DESPACHO FAIL | id=%s | erro=%s", id_oc, exc)
        else:
            log.info("DRY_RUN DESPACHO | id=%s | coluna_n=%s", id_oc, valor_n)
            despachados += 1

    resultado = {
        "cliente": cliente_id,
        "inseridos": inseridos,
        "atualizados": atualizados,
        "herdados": herdados,
        "despachados": despachados,
        "dry_run": dry_run,
    }
    log.info("[%s] exportação concluída | %s", cliente_id, resultado)
    return resultado


# ─────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────

@router.post("/api/v1/exportar")
async def exportar(
    x_nit_key: Optional[str] = Header(None, alias="X-NIT-KEY"),
    cliente: Optional[str] = Query(None, description="ID do cliente (ex: cliente_b). Omitir = todos."),
    dry_run: bool = Query(False, description="Se true, não escreve na planilha"),
):
    expected_key = os.environ.get("EXPORT_SECRET", "")
    if not expected_key:
        raise HTTPException(500, "EXPORT_SECRET não configurado no servidor.")
    if x_nit_key != expected_key:
        raise HTTPException(401, "X-NIT-KEY inválida ou ausente.")

    try:
        clientes = _load_clientes_config()
    except FileNotFoundError:
        raise HTTPException(500, "Arquivo de configuração de clientes não encontrado.")

    targets = {}
    if cliente:
        if cliente not in clientes:
            raise HTTPException(404, f"Cliente '{cliente}' não encontrado na configuração.")
        targets[cliente] = clientes[cliente]
    else:
        targets = clientes

    resultados = []
    for cid, cfg in targets.items():
        try:
            res = executar_exportacao(cid, cfg, dry_run=dry_run)
            resultados.append(res)
        except Exception as exc:
            log.error("exportação falhou para cliente=%s: %s", cid, exc)
            resultados.append({"cliente": cid, "erro": str(exc)})

    return {"status": "ok", "resultados": resultados}
