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
    append_heranca_diaria,           
    get_cursor,
    update_cursor,
)

log = logging.getLogger("nit.export")
router = APIRouter()

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
            raise ValueError(f"Cliente '{cliente_id}' incompleto. Ausentes: {faltando}")
        if cfg["spreadsheet_id"] == "SUBSTITUA_PELO_ID_DA_PLANILHA":
            raise ValueError(f"Cliente '{cliente_id}': spreadsheet_id é placeholder.")
    return clientes


def executar_exportacao(cliente_id: str, cliente_config: dict, dry_run: bool = False) -> dict:
    cursor_node      = cliente_config["cursor_node"]
    ocorrencias_node = cliente_config.get("ocorrencias_node", "/ocorrencias")

    cursor                  = get_cursor(cursor_node)
    def _int(val, default=0):
        try:
            return int(val or default)
        except (TypeError, ValueError):
            return default

    ultimo_ts               = _int(cursor.get("ultimo_ts",             0))
    ultima_atualizacao      = _int(cursor.get("ultima_atualizacao",    0))
    ultimo_dataReferencia   = _int(cursor.get("ultimo_dataReferencia", 0))

    log.info("[%s] iniciando | ultimo_ts=%s | ultima_atualizacao=%s", cliente_id, ultimo_ts, ultima_atualizacao)

    ref_oc = rtdb.reference(ocorrencias_node)

    # ── 1. Novas ocorrências ────────────────────────────────────────────
    novas_query = (ref_oc.order_by_child("ts").start_at(ultimo_ts + 1).get()) or {}
    inseridos = 0
    novo_ts   = ultimo_ts

    for id_oc, dados in novas_query.items():
        if not isinstance(dados, dict):
            continue
        if not dry_run:
            from services.sheets_integration import get_sheets_service, _encontrar_linha
            try:
                svc = get_sheets_service()
                if _encontrar_linha(svc, cliente_config["spreadsheet_id"], cliente_config["sheet_name"], id_oc):
                    log.info("SKIP | id=%s já existe – ignorando append", id_oc)
                    novo_ts = max(novo_ts, dados.get("ts", 0))
                    continue
            except Exception as exc:
                log.warning("dedup check falhou id=%s: %s – prosseguindo", id_oc, exc)

        ok = append_nova_ocorrencia(cliente_config, dados, id_oc, dry_run=dry_run)
        if ok:
            inseridos += 1
            novo_ts = max(novo_ts, dados.get("ts", 0))
        else:
            log.error("[%s] APPEND falhou id=%s – cursor NÃO avançado", cliente_id, id_oc)
            break
    else:
        if novo_ts > ultimo_ts and not dry_run:
            update_cursor(cursor_node, ultimo_ts=novo_ts)

    # ── 2. Ocorrências normalizadas ─────────────────────────────────────
    norm_query = (ref_oc.order_by_child("ts_norm").start_at(ultima_atualizacao + 1).get()) or {}

    if not norm_query:
        log.warning("[%s] ts_norm não encontrado – fallback por status==NORMALIZADO", cliente_id)
        all_oc = ref_oc.get() or {}
        norm_query = {
            k: v for k, v in all_oc.items()
            if isinstance(v, dict)
            and v.get("status") == "NORMALIZADO"
            and v.get("ts_norm", v.get("ts", 0)) > ultima_atualizacao
        }

    atualizados      = 0
    nova_atualizacao = ultima_atualizacao

    for id_oc, dados in norm_query.items():
        if not isinstance(dados, dict):
            continue
        if dados.get("status") != "NORMALIZADO":
            continue

        ok = update_ocorrencia_normalizada(cliente_config, id_oc, dados, dry_run=dry_run)
        if ok:
            atualizados += 1
            nova_atualizacao = max(nova_atualizacao, dados.get("ts_norm") or dados.get("ts", 0))
        else:
            log.error(
                "[%s] UPDATE falhou id=%s | linha não encontrada na planilha. "
                "Verifique se o id_ocorrencia existe na coluna R. "
                "Cursor NÃO avançado — será reprocessado.",
                cliente_id, id_oc,
            )

    if nova_atualizacao > ultima_atualizacao and not dry_run:
        update_cursor(cursor_node, ultima_atualizacao=nova_atualizacao)

    # ── 3. Herança de dataReferencia ────────────────────────────────────
    dataRef_query = (
        ref_oc.order_by_child("ts_dataReferencia").start_at(ultimo_dataReferencia + 1).get()
    ) or {}

    herdados = 0
    novo_dataReferencia = ultimo_dataReferencia

    for id_oc, dados in dataRef_query.items():
        if not isinstance(dados, dict):
            continue
        if dados.get("status") == "NORMALIZADO":
            log.debug("HERANÇA SKIP normalizado | id=%s", id_oc)
            novo_dataReferencia = max(novo_dataReferencia, dados.get("ts_dataReferencia", 0))
            continue
        nova_data = dados.get("dataReferencia", "")
        if not nova_data:
            continue
        ok = append_heranca_diaria(cliente_config, dados, id_oc, data_plantao=nova_data, dry_run=dry_run)
        if ok:
            herdados += 1
            novo_dataReferencia = max(novo_dataReferencia, dados.get("ts_dataReferencia", 0))
        else:
            log.error("[%s] HERANÇA APPEND falhou id=%s – cursor NÃO avançado", cliente_id, id_oc)
            break
    else:
        if novo_dataReferencia > ultimo_dataReferencia and not dry_run:
            update_cursor(cursor_node, ultimo_dataReferencia=novo_dataReferencia)

    # ── 4. Despachos ativos — coluna N ──────────────────────────────────
    # Atualiza a coluna N de ocorrências ativas com o valor operacional atual.
    # Prioridade: colunaN do Firebase → derivado do historico → sub_map simples.
    # colunaN é gravado pelo frontend a partir desta versão; cards despachados
    # antes do deploy não têm o campo — nesses casos derivamos do historico.
    all_oc_despacho = ref_oc.get() or {}
    despacho_query = {
        k: v for k, v in all_oc_despacho.items()
        if isinstance(v, dict)
        and str(v.get("sub", "")).strip().lower() in ("vl", "amc")
        and v.get("status") != "NORMALIZADO"
    }

    despachados = 0
    from services.sheets_integration import get_sheets_service, _encontrar_linha

    sub_map = {"vl": "VIA LIVRE", "amc": "AMC"}

    def _derivar_coluna_n(historico: dict, sub_fallback: str) -> str:
        """Replica _derivarColunaN do frontend. despacho→segmento, apoio→+, rendição→→."""
        if not historico:
            return sub_map.get(sub_fallback, sub_fallback.upper())
        eventos = sorted(historico.values(), key=lambda e: e.get("ts", 0))
        segmentos, seg = [], []
        label_map = {"vl": "VIA LIVRE", "amc": "AMC"}
        for ev in eventos:
            label = label_map.get(str(ev.get("sub", "")).strip().lower())
            if not label:
                continue
            tipo = str(ev.get("tipo", "")).strip().lower()
            if tipo == "despacho":
                seg = [label]
            elif tipo == "apoio":
                if label not in seg:
                    seg.append(label)
            elif tipo in ("rendição", "rendicao", "rendição"):
                if seg:
                    segmentos.append(" + ".join(seg))
                seg = [label]
        if seg:
            segmentos.append(" + ".join(seg))
        # Para a planilha, relevante é a transição mais recente, não a cadeia histórica completa.
        # Ex: card com 5 dias de operação → "VIA LIVRE → AMC" (último par), não a cadeia toda.
        if not segmentos:
            return sub_map.get(sub_fallback, sub_fallback.upper())
        if len(segmentos) == 1:
            return segmentos[0]
        return f"{segmentos[-2]} → {segmentos[-1]}"

    for id_oc, dados in despacho_query.items():
        sub = str(dados.get("sub", "")).strip().lower()
        if not sub:
            continue

        # Prioridade 1: colunaN gravado pelo frontend (cards pós-deploy)
        valor_n = dados.get("colunaN", "")

        # Prioridade 2: derivar do historico (cards pré-deploy ou sem colunaN)
        if not valor_n:
            try:
                hist_ref = rtdb.reference(f"{ocorrencias_node}/{id_oc}/historico")
                hist     = hist_ref.get() or {}
                valor_n  = _derivar_coluna_n(hist, sub)
            except Exception as exc:
                log.warning("DESPACHO histórico FAIL | id=%s | %s", id_oc, exc)
                valor_n = sub_map.get(sub, sub.upper())

        if not dry_run:
            try:
                svc            = get_sheets_service()
                row_num        = _encontrar_linha(svc, cliente_config["spreadsheet_id"], cliente_config["sheet_name"], id_oc)
                if not row_num:
                    continue
                sheet_name     = cliente_config["sheet_name"]
                spreadsheet_id = cliente_config["spreadsheet_id"]
                atual          = svc.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=f"\'{sheet_name}\'!N{row_num}",
                ).execute()
                valor_atual = (atual.get("values") or [[""]])[0][0] if atual.get("values") else ""
                if valor_atual == valor_n:
                    log.debug("DESPACHO SKIP | id=%s | N já=%s", id_oc, valor_n)
                    continue
                svc.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"valueInputOption": "RAW", "data": [
                        {"range": f"\'{sheet_name}\'!N{row_num}", "values": [[valor_n]]}
                    ]},
                ).execute()
                log.info("DESPACHO OK | id=%s | N=%s | row=%d", id_oc, valor_n, row_num)
                despachados += 1
            except Exception as exc:
                log.error("DESPACHO FAIL | id=%s | erro=%s", id_oc, exc)
        else:
            log.info("DRY_RUN DESPACHO | id=%s | N=%s", id_oc, valor_n)
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


@router.post("/api/v1/exportar")
async def exportar(
    x_nit_key: Optional[str] = Header(None, alias="X-NIT-KEY"),
    cliente: Optional[str] = Query(None, description="ID do cliente. Omitir = todos."),
    dry_run: bool = Query(False, description="Se true, não escreve na planilha"),
):
    expected_key = os.environ.get("EXPORT_SECRET", "")
    if not expected_key:
        raise HTTPException(500, "EXPORT_SECRET não configurado.")
    if x_nit_key != expected_key:
        raise HTTPException(401, "X-NIT-KEY inválida ou ausente.")

    try:
        clientes = _load_clientes_config()
    except FileNotFoundError:
        raise HTTPException(500, "Arquivo de configuração de clientes não encontrado.")

    targets = {}
    if cliente:
        if cliente not in clientes:
            raise HTTPException(404, f"Cliente '{cliente}' não encontrado.")
        targets[cliente] = clientes[cliente]
    else:
        targets = clientes

    resultados = []
    for cid, cfg in targets.items():
        try:
            res = executar_exportacao(cid, cfg, dry_run=dry_run)
            resultados.append(res)
        except Exception as exc:
            log.error("exportação falhou cliente=%s: %s", cid, exc)
            resultados.append({"cliente": cid, "erro": str(exc)})

    return {"status": "ok", "resultados": resultados}
