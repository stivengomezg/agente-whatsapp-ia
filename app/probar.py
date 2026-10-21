"""Chat de prueba dentro del panel: el negocio conversa con el agente desde el navegador.

Lee los datos REALES del negocio (mismo conector, solo lectura), pero todo lo que
el agente escribe en la prueba (memoria, solicitudes, escalamientos) va a una
base temporal en memoria. Probar con el celular de una clienta real no pausa su
conversación de WhatsApp ni crea solicitudes reales. Sirve para demostraciones
de venta sin configurar WhatsApp.
"""
from __future__ import annotations

from threading import Lock

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.agente import Agente
from app.almacen import Almacen
from app.panel import ESTILOS, _token_formulario, _usuario, _validar

router = APIRouter(prefix="/panel/probar")
_prueba: Agente | None = None
_candado = Lock()


def _agente_principal() -> Agente:
    from app.whatsapp import agente  # import diferido: evita ciclo
    return agente()


def agente_prueba(reiniciar: bool = False) -> Agente:
    """Mismo negocio y mismos modelos que el agente real, con una base desechable."""
    global _prueba
    with _candado:
        if _prueba is None or reiniciar:
            real = _agente_principal()
            _prueba = Agente(real.negocio, Almacen("sqlite://"), llm=real.llm, llm_rapido=real.llm_rapido)
        return _prueba


class Mensaje(BaseModel):
    telefono: str = Field(min_length=7, max_length=20)
    texto: str = Field(min_length=1, max_length=1000)


@router.post("/mensaje")
def mensaje(m: Mensaje, x_token: str = Header(default=""), _: str = Depends(_usuario)) -> dict:
    _validar(x_token)
    estado = _agente_principal().almacen.estado_servicio()
    if estado in ("suspendido", "prueba_vencida"):  # sin servicio tampoco hay demostración
        raise HTTPException(403, "El servicio no está activo. Comunícate con tu proveedor.")
    ag = agente_prueba()
    if m.texto.strip() == "/devolver":
        ag.devolver_al_agente(m.telefono)
        return {"respuesta": None, "estado": "devuelto", "herramientas": []}
    try:
        r = ag.responder(m.telefono, m.texto)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"respuesta": r.respuesta, "estado": r.estado, "herramientas": r.herramientas}


@router.post("/reiniciar")
def reiniciar(x_token: str = Header(default=""), _: str = Depends(_usuario)) -> dict:
    _validar(x_token)
    agente_prueba(reiniciar=True)
    return {"ok": True}


CHAT_CSS = """
.chat{background:var(--tarjeta);border:1px solid var(--borde);border-radius:12px;display:flex;flex-direction:column;height:min(70vh,640px)}
.barra{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px;border-bottom:1px solid var(--borde)}
.barra label{font-size:13px;color:var(--suave)}.barra input{font:inherit;padding:8px 10px;border:1px solid var(--borde);border-radius:8px;background:var(--fondo);color:var(--texto);width:160px}
.mensajes{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.burbuja{max-width:min(80%,520px);padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.yo{align-self:flex-end;background:var(--acento);color:var(--sobre-acento);border-bottom-right-radius:4px}
.el{align-self:flex-start;background:var(--fondo);border:1px solid var(--borde);border-bottom-left-radius:4px}
.nota{align-self:center;color:var(--suave);font-size:13px;text-align:center}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}.chip{font-size:12px;color:var(--suave);border:1px solid var(--borde);border-radius:999px;padding:1px 8px}
.envio{display:flex;gap:8px;padding:12px;border-top:1px solid var(--borde)}
.envio input{flex:1;min-width:0;font:inherit;padding:10px 12px;border:1px solid var(--borde);border-radius:8px;background:var(--fondo);color:var(--texto)}
.aviso{background:var(--tarjeta);border:1px solid var(--borde);border-left-width:1px;border-radius:10px;padding:12px 14px;color:var(--suave);font-size:14px;margin-bottom:16px}
"""


@router.get("", response_class=HTMLResponse)
def pagina(_: str = Depends(_usuario)) -> str:
    nombre = escape(_agente_principal().negocio.nombre)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Probar el agente</title>
<style>{ESTILOS}{CHAT_CSS}</style></head><body><main>
<p class="sub"><a href="/panel">← Volver al panel</a></p>
<h1>Probar el agente</h1><p class="sub">{nombre}</p>
<div class="aviso">Escribe como si fueras una clienta. El agente consulta los <b>datos reales</b> del negocio,
pero lo que pase aquí no se guarda: no crea solicitudes reales ni pausa conversaciones de WhatsApp.</div>
<section class="chat" aria-label="Conversación de prueba">
  <div class="barra">
    <label for="tel">Celular de la clienta</label>
    <input id="tel" inputmode="tel" autocomplete="off" placeholder="300 123 4567">
    <button id="reiniciar" type="button">Empezar de nuevo</button>
  </div>
  <div class="mensajes" id="mensajes" aria-live="polite">
    <p class="nota">Pon el celular de una clienta registrada para ver sus pedidos y saldo, o uno cualquiera para probar como cliente nueva.</p>
  </div>
  <form class="envio" id="envio">
    <label for="texto" style="position:absolute;left:-9999px">Mensaje</label>
    <input id="texto" autocomplete="off" placeholder="Escribe un mensaje…" maxlength="1000">
    <button class="principal" id="enviar">Enviar</button>
  </form>
</section>
</main>
<script>
const TOKEN = {_token_formulario()!r};
const lista = document.getElementById('mensajes'), texto = document.getElementById('texto'),
      tel = document.getElementById('tel'), boton = document.getElementById('enviar');
try {{ tel.value = localStorage.getItem('prueba_tel') || ''; }} catch (e) {{}}
const ESTADOS = {{escalado:'Pasó la conversación a una persona', pausado:'En pausa: la atiende una persona. Escribe /devolver para que el agente vuelva.',
  falla:'Falla técnica: respondió el mensaje de respaldo', devuelto:'La conversación volvió al agente'}};
function burbuja(clase, contenido, herramientas) {{
  const b = document.createElement('div'); b.className = 'burbuja ' + clase;
  // *texto* se ve en negrita, como en WhatsApp (sin usar innerHTML: el texto nunca se interpreta como HTML)
  contenido.split(/(\\*[^*\\n]+\\*)/).forEach(parte => {{
    if (/^\\*[^*\\n]+\\*$/.test(parte)) {{ const n = document.createElement('strong'); n.textContent = parte.slice(1, -1); b.appendChild(n); }}
    else b.appendChild(document.createTextNode(parte));
  }});
  if (herramientas && herramientas.length) {{
    const c = document.createElement('div'); c.className = 'chips';
    herramientas.forEach(h => {{ const s = document.createElement('span'); s.className = 'chip'; s.textContent = h; c.appendChild(s); }});
    b.appendChild(c);
  }}
  lista.appendChild(b); lista.scrollTop = lista.scrollHeight; return b;
}}
function nota(t) {{ const p = document.createElement('p'); p.className = 'nota'; p.textContent = t; lista.appendChild(p); lista.scrollTop = lista.scrollHeight; }}
async function llamar(ruta, cuerpo) {{
  const r = await fetch(ruta, {{method:'POST', headers:{{'Content-Type':'application/json','X-Token':TOKEN}}, body: JSON.stringify(cuerpo || {{}})}});
  const datos = await r.json().catch(() => ({{}}));
  if (!r.ok) throw new Error(datos.detail && typeof datos.detail === 'string' ? datos.detail : 'Revisa el celular y el mensaje.');
  return datos;
}}
document.getElementById('envio').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const t = texto.value.trim(), celular = tel.value.trim();
  if (!t) return;
  if (celular.replace(/\\D/g,'').length < 7) {{ nota('Escribe primero un celular de al menos 7 dígitos.'); tel.focus(); return; }}
  try {{ localStorage.setItem('prueba_tel', celular); }} catch (e) {{}}
  burbuja('yo', t); texto.value = ''; boton.disabled = true;
  const espera = burbuja('el', 'Consultando…');
  try {{
    const r = await llamar('/panel/probar/mensaje', {{telefono: celular, texto: t}});
    espera.remove();
    if (r.respuesta) burbuja('el', r.respuesta, r.herramientas);
    if (ESTADOS[r.estado]) nota(ESTADOS[r.estado]);
  }} catch (e) {{ espera.remove(); nota(e.message); }}
  boton.disabled = false; texto.focus();
}});
document.getElementById('reiniciar').addEventListener('click', async () => {{
  try {{ await llamar('/panel/probar/reiniciar'); lista.innerHTML = ''; nota('Conversación nueva. El agente no recuerda nada de la prueba anterior.'); }}
  catch (e) {{ nota(e.message); }}
}});
</script></body></html>"""
