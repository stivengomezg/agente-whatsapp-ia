"""Panel web para el negocio: solicitudes, conversaciones escaladas, métricas y pausa.

Vive en el servidor del agente, no en el sistema del negocio. Acceso con
usuario y clave (PANEL_USUARIO / PANEL_CLAVE). El negocio puede PAUSAR el
agente; solo el proveedor puede SUSPENDERLO (ver /admin en whatsapp.py).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timezone
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app import config

router = APIRouter(prefix="/panel")
_basic = HTTPBasic(realm="Panel del agente")
ZONA = ZoneInfo(config.ZONA_HORARIA)


def _usuario(cred: HTTPBasicCredentials = Depends(_basic)) -> str:
    ok_usuario = secrets.compare_digest(cred.username.encode(), config.PANEL_USUARIO.encode())
    ok_clave = bool(config.PANEL_CLAVE) and secrets.compare_digest(cred.password.encode(), config.PANEL_CLAVE.encode())
    if not (ok_usuario and ok_clave):
        raise HTTPException(401, "Usuario o clave incorrectos", headers={"WWW-Authenticate": "Basic"})
    return cred.username


def _token_formulario() -> str:
    """Protege los botones contra formularios enviados desde otros sitios (CSRF)."""
    return hmac.new(config.PANEL_CLAVE.encode(), b"panel-formularios", hashlib.sha256).hexdigest()


def _validar(token: str) -> None:
    if not hmac.compare_digest(token, _token_formulario()):
        raise HTTPException(403, "Formulario inválido, recarga la página")


def _agente():
    from app.whatsapp import agente  # import diferido: evita ciclo
    return agente()


def _fecha(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONA).strftime("%d/%m %I:%M %p")


def _boton(accion: str, texto: str, clase: str = "", **campos: str) -> str:
    ocultos = "".join(f'<input type="hidden" name="{k}" value="{escape(v)}">' for k, v in campos.items())
    return (f'<form method="post" action="{accion}"><input type="hidden" name="token" value="{_token_formulario()}">'
            f'{ocultos}<button class="{clase}">{escape(texto)}</button></form>')


ESTILOS = """
:root{--fondo:#f6f7f9;--tarjeta:#fff;--texto:#1b1f24;--suave:#5b6470;--borde:#e3e6ea;--acento:#0f766e;--sobre-acento:#ffffff;--alerta:#b42318;--aviso:#b54708}
@media (prefers-color-scheme:dark){:root{--fondo:#0f1215;--tarjeta:#171b20;--texto:#e8ebee;--suave:#9aa4af;--borde:#2a3038;--acento:#2dd4bf;--sobre-acento:#042f2e;--alerta:#f97066;--aviso:#fdb022}}
*{box-sizing:border-box}body{margin:0;background:var(--fondo);color:var(--texto);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--suave);margin:0 0 20px}.estado{padding:14px 16px;border-radius:10px;border:1px solid var(--borde);background:var(--tarjeta);display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.punto{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px}.activo{background:var(--acento)}.inactivo{background:var(--alerta)}.pausa{background:var(--aviso)}
.cifras{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}@media (min-width:640px){.cifras{grid-template-columns:repeat(3,minmax(0,1fr))}}.cifra{background:var(--tarjeta);border:1px solid var(--borde);border-radius:10px;padding:14px}
.cifra b{display:block;font-size:24px}.cifra span{color:var(--suave);font-size:13px}
.lista{display:flex;flex-direction:column;gap:10px}.item{background:var(--tarjeta);border:1px solid var(--borde);border-radius:10px;padding:14px;display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}
.item p{margin:4px 0 0}.meta{color:var(--suave);font-size:13px}.acciones{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
form{margin:0}button{font:inherit;font-size:14px;padding:8px 12px;border-radius:8px;border:1px solid var(--borde);background:var(--tarjeta);color:var(--texto);cursor:pointer;min-height:40px}
button.principal{background:var(--acento);border-color:var(--acento);color:var(--sobre-acento);font-weight:600}button:focus-visible,a:focus-visible{outline:2px solid var(--acento);outline-offset:2px}
a{color:var(--acento)}.vacio{color:var(--suave);padding:8px 0}
"""


@router.get("", response_class=HTMLResponse)
def panel(_: str = Depends(_usuario)) -> str:
    ag = _agente()
    almacen, negocio = ag.almacen, ag.negocio
    estado = almacen.estado_servicio()
    m = almacen.metricas(30)

    textos_estado = {
        "activo": ("activo", "El agente está respondiendo.", _boton("/panel/pausa", "Pausar agente", valor="si")),
        "pausado": ("pausa", "El agente está en pausa: no responde a nadie.",
                    _boton("/panel/pausa", "Reanudar agente", "principal", valor="no")),
        "suspendido": ("inactivo", "Servicio suspendido. Comunícate con tu proveedor para reactivarlo.", ""),
        "prueba_vencida": ("inactivo", "Terminó el periodo de prueba. Comunícate con tu proveedor.", ""),
    }
    clase, texto, accion = textos_estado[estado]

    cifras = [
        (m["clientes_atendidos"], "clientes atendidos"),
        (m["resueltos_sin_persona"], "resueltos sin una persona"),
        (m["escalamientos"], "pasados a una persona"),
        (sum(m["solicitudes"].values()), "solicitudes"),
        (m["solicitudes"].get("convertida_en_pedido", 0), "convertidas en pedido"),
        (m["fallas_tecnicas"], "fallas técnicas"),
    ]

    escalados = almacen.escalamientos_abiertos()
    html_escalados = "".join(
        f'<div class="item"><div><b>{escape(e["telefono"])}</b> · {escape(e["motivo"])}'
        f'<p>{escape(e["resumen"])}</p><span class="meta">{_fecha(e["creado_en"])}</span></div>'
        f'<div class="acciones"><a href="https://wa.me/57{quote(e["telefono"])}" target="_blank" rel="noopener">Abrir chat</a>'
        f'{_boton("/panel/escalamientos/" + quote(e["telefono"]) + "/devolver", "Devolver al agente", "principal")}</div></div>'
        for e in escalados) or '<p class="vacio">No hay conversaciones esperando a una persona.</p>'

    solicitudes = almacen.listar_solicitudes("pendiente_revision")
    html_solicitudes = "".join(
        f'<div class="item"><div><b>{escape(s["codigo"] or "")}</b> · {escape(s["telefono"])}'
        f'<p>{escape(s["descripcion"])}</p><span class="meta">{_fecha(s["creada_en"])}</span></div>'
        f'<div class="acciones">{_boton(f"/panel/solicitudes/{s["id"]}", "Ya es pedido", "principal", estado="convertida_en_pedido")}'
        f'{_boton(f"/panel/solicitudes/{s["id"]}", "Descartar", estado="descartada")}</div></div>'
        for s in solicitudes) or '<p class="vacio">No hay solicitudes pendientes.</p>'

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Agente · {escape(negocio.nombre)}</title>
<style>{ESTILOS}</style></head><body><main>
<h1>Agente de WhatsApp</h1><p class="sub">{escape(negocio.nombre)} · <a href="/panel/probar">Probar el agente</a></p>
<div class="estado"><div><span class="punto {clase}"></span>{escape(texto)}</div>{accion}</div>
<h2>Esperando a una persona ({len(escalados)})</h2><div class="lista">{html_escalados}</div>
<h2>Solicitudes por revisar ({len(solicitudes)})</h2><div class="lista">{html_solicitudes}</div>
<h2>Últimos 30 días</h2><div class="cifras">{"".join(f'<div class="cifra"><b>{v}</b><span>{t}</span></div>' for v, t in cifras)}</div>
</main></body></html>"""


@router.post("/pausa")
def pausa(valor: str = Form(...), token: str = Form(...), _: str = Depends(_usuario)) -> RedirectResponse:
    _validar(token)
    ag = _agente()
    ag.almacen.guardar_ajuste("pausado", "si" if valor == "si" else "no")
    ag.almacen.registrar_evento(None, "pausa_panel", {"pausado": valor == "si"})
    return RedirectResponse("/panel", status_code=303)


@router.post("/solicitudes/{solicitud_id}")
def solicitud(solicitud_id: int, estado: str = Form(...), token: str = Form(...),
              _: str = Depends(_usuario)) -> RedirectResponse:
    _validar(token)
    try:
        _agente().almacen.cambiar_estado_solicitud(solicitud_id, estado)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/panel", status_code=303)


@router.post("/escalamientos/{telefono}/devolver")
def devolver(telefono: str, token: str = Form(...), _: str = Depends(_usuario)) -> RedirectResponse:
    _validar(token)
    _agente().devolver_al_agente(telefono)
    return RedirectResponse("/panel", status_code=303)
