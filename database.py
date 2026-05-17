import firebase_admin
from firebase_admin import credentials, db
import os

# Descobre o caminho exato onde o credentials.json está guardado na sua máquina
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")


def inicializar_firebase():
    """Inicializa o Firebase Admin SDK com acesso mestre."""
    if not firebase_admin._apps:
        if not os.path.exists(CREDENTIALS_PATH):
            raise FileNotFoundError(
                f"Erro Crítico: O arquivo '{CREDENTIALS_PATH}' não foi encontrado. "
                "Coloque o arquivo de credenciais do Firebase dentro da pasta nit-api."
            )

        cred = credentials.Certificate(CREDENTIALS_PATH)
        firebase_admin.initialize_app(
            cred,
            {
                # URL exata do seu Realtime Database do NIT
                "databaseURL": "https://nit-operacional-default-rtdb.firebaseio.com/"
            },
        )

    return db
