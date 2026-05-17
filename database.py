import firebase_admin
from firebase_admin import credentials, db
import os
import json

# Caminho local (para a sua máquina)
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")


def inicializar_firebase():
    """Inicializa o Firebase Admin SDK usando Variável de Ambiente (Nuvem) ou Arquivo Físico (Local)."""
    if not firebase_admin._apps:
        # 1. Tenta carregar pela variável de ambiente do Railway
        env_credentials = os.environ.get("FIREBASE_CREDENTIALS_JSON")

        if env_credentials:
            try:
                # Converte o texto JSON da variável de volta para um dicionário Python
                cred_dict = json.loads(env_credentials)
                cred = credentials.Certificate(cred_dict)
            except Exception as e:
                raise RuntimeError(
                    f"Erro ao decodificar a variável FIREBASE_CREDENTIALS_JSON: {e}"
                )

        # 2. Se não achar a variável, procura o arquivo físico (Modo Local na sua máquina)
        elif os.path.exists(CREDENTIALS_PATH):
            cred = credentials.Certificate(CREDENTIALS_PATH)

        else:
            raise FileNotFoundError(
                "Erro Crítico: Nenhuma credencial do Firebase encontrada! "
                "Configure a variável FIREBASE_CREDENTIALS_JSON no Railway ou adicione o arquivo credentials.json localmente."
            )

        # Inicializa o app com a credencial encontrada (seja da nuvem ou local)
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": "https://nit-operacional-default-rtdb.firebaseio.com/"},
        )

    return db
