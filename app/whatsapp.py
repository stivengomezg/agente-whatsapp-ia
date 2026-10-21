"""Servidor web: recibe mensajes de WhatsApp Cloud API y responde con el agente.

Rutas:
  GET  /webhook                         verificación inicial de Meta
  POST /webhook                         mensajes entrantes (firma verificada)
  /panel                                panel del negocio (ver app/panel.py)
  GET  /admin/estado, POST /admin/servicio   solo el proveedor: suspender, reactivar, fecha de prueba
  POST /admin/conversaciones/{tel}/devolver  solo el proveedor
  GET  /salud                           para el monitoreo del hosting
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import OrderedDict, defaultdict, deque

import httpx
from datetime import date

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import PlainTextResponse

from app import config
from app.agente import Agente
from app.avisos import alertar_desarrollador
from app.panel import router as router_panel
from app.probar import router as router_probar

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agente.whatsapp")

app = FastAPI(title="Agente de WhatsApp")
app.include_router(router_panel)
app.include_router(router_probar)
_agente: Agente | None = None

# Meta reintenta si no respondemos rápido: se ignoran ids ya procesados.
_procesados: OrderedDict[str, None] = OrderedDict()
# Límite de mensajes por número (evita abusos y cuentas de IA infladas).
_recientes: dict[str, deque] = defaultdict(deque)

NO_TEXTO = "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribes, por favor? 🙏"
DEMASIADOS = "Recibí muchos mensajes seguidos. Dame unos minutos y te respondo."


def agente() -> Agente:
    global _agente
    if _agente is None:
        _agente = Agente()
    return _agente


def firma_valida(cuerpo: bytes, firma: str | None) -> bool:
    if not config.WHATSAPP_APP_SECRET:
        log.warning("WHATSAPP_APP_SECRET vacío: no se verifica la firma (solo para desarrollo)")
        return True
    if not firma or not firma.startswith("sha256="):
        return False
    esperada = hmac.new(config.WHATSAPP_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.removeprefix("sha256="))


def ya_procesado(mensaje_id: str) -> bool:
    if mensaje_id in _procesados:
        return True
    _procesados[mensaje_id] = None
    if len(_procesados) > 5000:
        _procesados.popitem(last=False)
    return False


def supera_limite(telefono: str) -> bool:
    ahora, cola = time.monotonic(), _recientes[telefono]
    while cola and ahora - cola[0] > 600:
        cola.popleft()
    cola.append(ahora)
    return len(cola) > config.LIMITE_MENSAJES_10_MIN


def enviar_whatsapp(telefono: str, texto: str) -> None:
    url = f"https://graph.facebook.com/v21.0/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    r = httpx.post(url, timeout=15,
                   headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"},
                   json={"messaging_product": "whatsapp", "to": telefono,
                         "type": "text", "text": {"body": texto[:4000]}})
    if r.status_code >= 400:
        alertar_desarrollador("envio_whatsapp", f"WhatsApp respondió {r.status_code}: {r.text[:300]}")


def atender(telefono: str, texto: str | None) -> None:
    try:
        if agente().almacen.estado_servicio() != "activo":
            agente().almacen.registrar_evento(telefono, "mensaje_sin_servicio", {})
            return  # apagado: no se responde nada, ni siquiera avisos automáticos
        if texto is None:
            enviar_whatsapp(telefono, NO_TEXTO)
            return
        if supera_limite(telefono):
            if len(_recientes[telefono]) == config.LIMITE_MENSAJES_10_MIN + 1:  # avisar una sola vez
                enviar_whatsapp(telefono, DEMASIADOS)
                alertar_desarrollador(f"limite:{telefono}", f"Límite de mensajes superado por {telefono}")
            return
        resultado = agente().responder(telefono, texto)
        if resultado.respuesta:
            enviar_whatsapp(telefono, resultado.respuesta)
    except Exception as e:  # noqa: BLE001
        log.exception("Error atendiendo a %s", telefono)
        alertar_desarrollador("atender", f"Error no controlado con {telefono}: {e}")


@app.get("/webhook", response_class=PlainTextResponse)
def verificar(modo: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
              reto: str = Query(alias="hub.challenge")) -> str:
    if modo == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and hmac.compare_digest(
            token, config.WHATSAPP_VERIFY_TOKEN):
        return reto
    raise HTTPException(403, "Token inválido")


@app.post("/webhook")
async def recibir(request: Request, tareas: BackgroundTasks,
                  x_hub_signature_256: str | None = Header(default=None)) -> dict:
    cuerpo = await request.body()
    if not firma_valida(cuerpo, x_hub_signature_256):
        raise HTTPException(401, "Firma inválida")
    datos = await request.json()

    for entrada in datos.get("entry", []):
        for cambio in entrada.get("changes", []):
            for msg in cambio.get("value", {}).get("messages", []):
                if ya_procesado(msg.get("id", "")):
                    continue
                texto = msg["text"]["body"] if msg.get("type") == "text" else None
                # Se responde a Meta de inmediato; el agente trabaja en segundo plano.
                tareas.add_task(atender, msg["from"], texto)
    return {"ok": True}


def _proveedor(authorization: str | None = Header(default=None)) -> None:
    if not config.ADMIN_TOKEN or not authorization or not hmac.compare_digest(
            authorization, f"Bearer {config.ADMIN_TOKEN}"):
        raise HTTPException(401, "No autorizado")


class CambioServicio(BaseModel):
    suspendido: bool | None = None     # True: el agente deja de responder (p. ej. falta de pago)
    prueba_hasta: date | None = None   # último día de prueba
    quitar_prueba: bool = False


@app.get("/admin/estado", dependencies=[Depends(_proveedor)])
def estado_admin() -> dict:
    a = agente().almacen
    return {"estado": a.estado_servicio(), "suspendido": a.leer_ajuste("suspendido", "no"),
            "pausado": a.leer_ajuste("pausado", "no"), "prueba_hasta": a.leer_ajuste("prueba_hasta") or None,
            "metricas_30_dias": a.metricas(30)}


@app.post("/admin/servicio", dependencies=[Depends(_proveedor)])
def cambiar_servicio(cambio: CambioServicio) -> dict:
    a = agente().almacen
    if cambio.suspendido is not None:
        a.guardar_ajuste("suspendido", "si" if cambio.suspendido else "no")
    if cambio.prueba_hasta:
        a.guardar_ajuste("prueba_hasta", cambio.prueba_hasta.isoformat())
    if cambio.quitar_prueba:
        a.guardar_ajuste("prueba_hasta", "")
    a.registrar_evento(None, "cambio_servicio", cambio.model_dump(mode="json"))
    return estado_admin()


@app.post("/admin/conversaciones/{telefono}/devolver", dependencies=[Depends(_proveedor)])
def devolver(telefono: str) -> dict:
    agente().devolver_al_agente(telefono)
    return {"ok": True, "telefono": telefono}


@app.get("/licencia/asistente")
def licencia_asistente() -> dict:
    """La consulta el asistente que vive dentro de la app del negocio (ASISTENTE_LICENCIA_URL).

    Pausar el bot de WhatsApp desde el panel no apaga el asistente de la
    app; suspender el servicio o que venza la prueba sí.
    """
    return {"activo": agente().almacen.estado_servicio() not in ("suspendido", "prueba_vencida")}


@app.get("/salud")
def salud() -> dict:
    return {"ok": True, "negocio": config.NEGOCIO}
