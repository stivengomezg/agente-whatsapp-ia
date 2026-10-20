"""Avisos a personas: errores técnicos al desarrollador, casos de negocio al equipo.

Si un mismo error se repite, se manda un solo aviso cada 10 minutos.
"""
from __future__ import annotations

import logging
import time

import httpx

from app import config

log = logging.getLogger("agente.avisos")
_ultimo_aviso: dict[str, float] = {}
VENTANA_SEGUNDOS = 600


def _enviar(url: str, texto: str) -> None:
    if not url:
        log.warning("Aviso sin webhook configurado: %s", texto)
        return
    try:
        # "text" sirve para Slack y Google Chat; "content" para Discord.
        httpx.post(url, json={"text": texto, "content": texto}, timeout=5)
    except httpx.HTTPError as e:
        log.error("No se pudo enviar el aviso: %s", e)


def alertar_desarrollador(clave: str, texto: str) -> bool:
    """Devuelve False si se omitió por estar dentro de la ventana anti-spam."""
    ahora = time.monotonic()
    if ahora - _ultimo_aviso.get(clave, -VENTANA_SEGUNDOS) < VENTANA_SEGUNDOS:
        return False
    _ultimo_aviso[clave] = ahora
    _enviar(config.ALERTA_WEBHOOK_URL, f"⚠️ [agente] {texto}")
    return True


def avisar_equipo(texto: str) -> None:
    _enviar(config.EQUIPO_WEBHOOK_URL, texto)
