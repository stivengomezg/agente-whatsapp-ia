"""Pruebas del servidor: webhook de WhatsApp, rutas del proveedor y panel del negocio."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import config, panel, whatsapp
from app.agente import Resultado
from app.almacen import Almacen
from app.negocio import cargar_negocio

PROVEEDOR = {"Authorization": "Bearer admin"}
DUENA = ("duena", "clave-panel")


@pytest.fixture
def cliente(monkeypatch):
    enviados, atendidos = [], []
    almacen = Almacen("sqlite://")

    class AgenteFalso:
        negocio = cargar_negocio("demo")

        def __init__(self):
            self.almacen = almacen

        def responder(self, telefono, texto):
            atendidos.append((telefono, texto))
            return Resultado(f"eco: {texto}", "respondido", [])

        def devolver_al_agente(self, telefono):
            almacen.resolver_escalamiento(telefono)
            atendidos.append((telefono, "/devolver"))

    for clave, valor in {"WHATSAPP_APP_SECRET": "secreto", "WHATSAPP_VERIFY_TOKEN": "verifica",
                         "ADMIN_TOKEN": "admin", "LIMITE_MENSAJES_10_MIN": 3,
                         "PANEL_USUARIO": DUENA[0], "PANEL_CLAVE": DUENA[1]}.items():
        monkeypatch.setattr(config, clave, valor)
    monkeypatch.setattr(whatsapp, "_agente", AgenteFalso())
    monkeypatch.setattr(whatsapp, "enviar_whatsapp", lambda tel, txt: enviados.append((tel, txt)))
    whatsapp._procesados.clear()
    whatsapp._recientes.clear()
    c = TestClient(whatsapp.app)
    c.enviados, c.atendidos, c.almacen = enviados, atendidos, almacen
    return c


def mensaje(id_: str, tipo: str = "text", texto: str = "hola", de: str = "573001112233") -> dict:
    msg = {"id": id_, "from": de, "type": tipo}
    if tipo == "text":
        msg["text"] = {"body": texto}
    return {"entry": [{"changes": [{"value": {"messages": [msg]}}]}]}


def publicar(c, cuerpo: dict, secreto: str = "secreto"):
    datos = json.dumps(cuerpo).encode()
    firma = "sha256=" + hmac.new(secreto.encode(), datos, hashlib.sha256).hexdigest()
    return c.post("/webhook", content=datos, headers={"X-Hub-Signature-256": firma,
                                                       "Content-Type": "application/json"})


# ---------------- webhook ----------------

def test_verificacion_de_meta(cliente):
    params = {"hub.mode": "subscribe", "hub.verify_token": "verifica", "hub.challenge": "123"}
    assert cliente.get("/webhook", params=params).text == "123"
    assert cliente.get("/webhook", params={**params, "hub.verify_token": "x"}).status_code == 403


def test_rechaza_firma_falsa(cliente):
    assert publicar(cliente, mensaje("m1"), secreto="otro").status_code == 401
    assert cliente.atendidos == []


def test_responde_y_no_duplica_reintentos_de_meta(cliente):
    publicar(cliente, mensaje("m1", texto="¿precio?"))
    publicar(cliente, mensaje("m1", texto="¿precio?"))
    assert cliente.atendidos == [("573001112233", "¿precio?")]
    assert cliente.enviados == [("573001112233", "eco: ¿precio?")]


def test_audio_recibe_aviso_sin_llamar_a_la_ia(cliente):
    publicar(cliente, mensaje("m2", tipo="audio"))
    assert cliente.atendidos == [] and cliente.enviados[0][1] == whatsapp.NO_TEXTO


def test_limite_de_mensajes_por_numero(cliente):
    for i in range(6):
        publicar(cliente, mensaje(f"x{i}", texto=str(i)))
    assert len(cliente.atendidos) == 3
    assert [t for _, t in cliente.enviados].count(whatsapp.DEMASIADOS) == 1


# ---------------- proveedor: suspender y prueba ----------------

def test_rutas_del_proveedor_exigen_token(cliente):
    assert cliente.get("/admin/estado").status_code == 401
    assert cliente.post("/admin/servicio", json={"suspendido": True}).status_code == 401
    assert cliente.post("/admin/servicio", json={"suspendido": True},
                        headers={"Authorization": "Bearer otro"}).status_code == 401


def test_suspender_apaga_todo_y_reactivar_lo_prende(cliente):
    r = cliente.post("/admin/servicio", json={"suspendido": True}, headers=PROVEEDOR)
    assert r.json()["estado"] == "suspendido"
    publicar(cliente, mensaje("s1", texto="hola"))
    publicar(cliente, mensaje("s2", tipo="audio"))
    assert cliente.atendidos == [] and cliente.enviados == []  # silencio total, ni avisos automáticos

    cliente.post("/admin/servicio", json={"suspendido": False}, headers=PROVEEDOR)
    publicar(cliente, mensaje("s3", texto="hola"))
    assert cliente.enviados == [("573001112233", "eco: hola")]


def test_fecha_de_prueba(cliente):
    assert cliente.post("/admin/servicio", json={"prueba_hasta": "2000-01-01"},
                        headers=PROVEEDOR).json()["estado"] == "prueba_vencida"
    assert cliente.post("/admin/servicio", json={"quitar_prueba": True},
                        headers=PROVEEDOR).json()["estado"] == "activo"


# ---------------- panel del negocio ----------------

def test_panel_exige_usuario_y_clave(cliente):
    assert cliente.get("/panel").status_code == 401
    assert cliente.get("/panel", auth=("duena", "mala")).status_code == 401
    assert "Agente de WhatsApp" in cliente.get("/panel", auth=DUENA).text


def test_panel_rechaza_formularios_sin_token(cliente):
    r = cliente.post("/panel/pausa", data={"valor": "si", "token": "falso"}, auth=DUENA)
    assert r.status_code == 403 and cliente.almacen.estado_servicio() == "activo"


def test_negocio_puede_pausar_pero_no_quitar_una_suspension(cliente):
    token = panel._token_formulario()
    cliente.post("/panel/pausa", data={"valor": "si", "token": token}, auth=DUENA)
    assert cliente.almacen.estado_servicio() == "pausado"
    cliente.post("/panel/pausa", data={"valor": "no", "token": token}, auth=DUENA)
    assert cliente.almacen.estado_servicio() == "activo"

    cliente.post("/admin/servicio", json={"suspendido": True}, headers=PROVEEDOR)
    pagina = cliente.get("/panel", auth=DUENA).text
    assert "Servicio suspendido" in pagina and "Reanudar agente" not in pagina
    cliente.post("/panel/pausa", data={"valor": "no", "token": token}, auth=DUENA)
    assert cliente.almacen.estado_servicio() == "suspendido"


def test_panel_gestiona_solicitudes_y_escalamientos(cliente):
    a, token = cliente.almacen, panel._token_formulario()
    a.registrar_solicitud("3001112233", "2 perfumes <script>alert(1)</script>")
    a.crear_escalamiento("3001112233", "descuento", "Pide descuento")

    pagina = cliente.get("/panel", auth=DUENA).text
    assert "SOL-0001" in pagina and "Pide descuento" in pagina
    assert "<script>alert(1)</script>" not in pagina  # el texto del cliente se escapa

    cliente.post("/panel/solicitudes/1", data={"estado": "convertida_en_pedido", "token": token}, auth=DUENA)
    assert a.listar_solicitudes() == []
    cliente.post("/panel/escalamientos/3001112233/devolver", data={"token": token}, auth=DUENA)
    assert a.escalamientos_abiertos() == []


# ---------------- chat de prueba en el panel ----------------

@pytest.fixture
def cliente_prueba(monkeypatch):
    """Servidor con el agente real (conector de demo) y un modelo falso."""
    from app.agente import Agente
    from app import probar
    from tests.fakes import decir, llamar, modelo

    for clave, valor in {"PANEL_USUARIO": DUENA[0], "PANEL_CLAVE": DUENA[1], "ADMIN_TOKEN": "admin"}.items():
        monkeypatch.setattr(config, clave, valor)
    principal = Agente(cargar_negocio("demo"), Almacen("sqlite://"),
                       llm=modelo(llamar("escalar_a_humano", motivo="descuento", resumen="Pide descuento"),
                                  decir("¡Hola!")),
                       llm_rapido=modelo())
    monkeypatch.setattr(whatsapp, "_agente", principal)
    monkeypatch.setattr(probar, "_prueba", None)
    c = TestClient(whatsapp.app)
    c.principal = principal
    return c


def test_chat_de_prueba_exige_login_y_token(cliente_prueba):
    assert cliente_prueba.get("/panel/probar").status_code == 401
    assert "Probar el agente" in cliente_prueba.get("/panel/probar", auth=DUENA).text
    r = cliente_prueba.post("/panel/probar/mensaje", json={"telefono": "3000000001", "texto": "hola"},
                            auth=DUENA, headers={"X-Token": "falso"})
    assert r.status_code == 403


def test_chat_de_prueba_no_toca_la_base_real(cliente_prueba):
    token = {"X-Token": panel._token_formulario()}
    r = cliente_prueba.post("/panel/probar/mensaje", json={"telefono": "3000000001", "texto": "Quiero descuento"},
                            auth=DUENA, headers=token)
    assert r.json()["estado"] == "escalado"
    # El escalamiento quedó en la base de prueba, no en la real: WhatsApp sigue atendiendo a esa clienta
    assert cliente_prueba.principal.almacen.escalamientos_abiertos() == []
    assert cliente_prueba.principal.almacen.estado_servicio() == "activo"

    r = cliente_prueba.post("/panel/probar/mensaje", json={"telefono": "3000000001", "texto": "/devolver"},
                            auth=DUENA, headers=token)
    assert r.json()["estado"] == "devuelto"
    r = cliente_prueba.post("/panel/probar/mensaje", json={"telefono": "3000000001", "texto": "Hola"},
                            auth=DUENA, headers=token)
    assert r.json()["respuesta"] == "¡Hola!"


def test_chat_de_prueba_se_apaga_si_el_servicio_esta_suspendido(cliente_prueba):
    cliente_prueba.principal.almacen.guardar_ajuste("suspendido", "si")
    r = cliente_prueba.post("/panel/probar/mensaje", json={"telefono": "3000000001", "texto": "hola"},
                            auth=DUENA, headers={"X-Token": panel._token_formulario()})
    assert r.status_code == 403


def test_licencia_del_asistente_sigue_al_interruptor_del_proveedor(cliente):
    assert cliente.get("/licencia/asistente").json() == {"activo": True}
    cliente.almacen.guardar_ajuste("pausado", "si")          # pausar el bot no apaga el asistente
    assert cliente.get("/licencia/asistente").json() == {"activo": True}
    cliente.post("/admin/servicio", json={"suspendido": True}, headers=PROVEEDOR)
    assert cliente.get("/licencia/asistente").json() == {"activo": False}
    cliente.post("/admin/servicio", json={"suspendido": False, "prueba_hasta": "2000-01-01"}, headers=PROVEEDOR)
    assert cliente.get("/licencia/asistente").json() == {"activo": False}
