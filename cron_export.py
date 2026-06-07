"""
app/cron_export.py
Script independente de exportação periódica NIT → Google Sheets.
Rodar no Railway como Cron Job: python -m app.cron_export
Frequência sugerida: */1 * * * *  (a cada 1 minuto)
"""

import os
import sys
import json
import logging
import time

# Garante que o diretório raiz do projeto está no path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import firebase_admin
from firebase_admin import credentials

from routes.export import executar_exportacao, _load_clientes_config

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | CRON | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("nit.cron")


# ─────────────────────────────────────────────
# Inicialização Firebase (idempotente)
# ─────────────────────────────────────────────

def _init_firebase():
    if firebase_admin._apps:
        return  # já inicializado

    fb_creds_raw = os.environ.get("FIREBASE_CREDENTIALS")
    fb_url       = os.environ.get("FIREBASE_DATABASE_URL")

    if not fb_creds_raw or not fb_url:
        raise EnvironmentError(
            "FIREBASE_CREDENTIALS e FIREBASE_DATABASE_URL são obrigatórios."
        )

    cred = credentials.Certificate(json.loads(fb_creds_raw))
    firebase_admin.initialize_app(cred, {"databaseURL": fb_url})
    log.info("Firebase inicializado OK")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    log.info("═══ Cron exportação iniciado ═══")
    start = time.time()

    try:
        _init_firebase()
    except EnvironmentError as exc:
        log.critical("Firebase init falhou: %s", exc)
        sys.exit(1)

    try:
        clientes = _load_clientes_config()
    except Exception as exc:
        log.critical("Não foi possível carregar clientes.json: %s", exc)
        sys.exit(1)

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        log.warning("⚠  DRY_RUN ativo – nenhuma escrita será feita na planilha")

    erros = 0
    for cliente_id, cfg in clientes.items():
        try:
            res = executar_exportacao(cliente_id, cfg, dry_run=dry_run)
            log.info("cliente=%s | inseridos=%d | atualizados=%d",
                     cliente_id, res.get("inseridos", 0), res.get("atualizados", 0))
        except Exception as exc:
            log.error("cliente=%s | ERRO: %s", cliente_id, exc)
            erros += 1

    elapsed = time.time() - start
    log.info("═══ Cron finalizado em %.2fs | erros=%d ═══", elapsed, erros)
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()
