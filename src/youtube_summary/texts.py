from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Texts:
    untitled: str
    watch_video: str
    auth_alert: str


ENGLISH = Texts(
    untitled="Untitled",
    watch_video="Watch video",
    auth_alert=(
        "⚠️ The NotebookLM session has expired. Renew storage_state.json (see README). "
        "Pending videos will be sent once it is renewed."
    ),
)
SPANISH = Texts(
    untitled="Sin título",
    watch_video="Ver vídeo",
    auth_alert=(
        "⚠️ La sesión de NotebookLM ha caducado. Renueva storage_state.json (ver README). "
        "Los vídeos pendientes se enviarán al renovarla."
    ),
)
TEXTS: Mapping[str, Texts] = {"en": ENGLISH, "es": SPANISH}
DEFAULT_LANGUAGE = "en"
