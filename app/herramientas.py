"""Herramientas que el modelo puede usar.

Se crean por conversación y quedan atadas al teléfono de quien escribe: el
modelo nunca recibe ni elige un teléfono, así que aunque lo engañen
(prompt injection) no tiene forma de pedir datos de otra persona.
"""
from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from app.almacen import Almacen
from app.avisos import avisar_equipo
from app.negocio import Negocio

NOMBRE_ESCALAR = "escalar_a_humano"


def _json(datos) -> str:
    return json.dumps(datos, ensure_ascii=False, default=str)


def crear_herramientas(negocio: Negocio, almacen: Almacen, telefono: str) -> list[BaseTool]:
    datos = negocio.conector  # solo lectura

    @tool
    def buscar_productos(texto: str) -> str:
        """Busca productos del inventario por nombre o categoría (por ejemplo "bolso" o
        "perfume"). Devuelve nombre, precio en COP y unidades disponibles. Úsala
        siempre antes de hablar de precios o disponibilidad."""
        resultados = datos.buscar_productos(texto[:80])
        return _json(resultados or {"resultado": "sin coincidencias", "busqueda": texto})

    @tool
    def consultar_mis_pedidos() -> str:
        """Consulta los pedidos recientes de la persona que escribe, con el estado de
        cada producto, total, abonado y saldo."""
        return _json(datos.pedidos_cliente(telefono) or {"resultado": "no tiene pedidos registrados"})

    @tool
    def consultar_mi_saldo() -> str:
        """Consulta el saldo pendiente y el saldo a favor de la persona que escribe."""
        return _json(datos.saldo_cliente(telefono))

    @tool
    def registrar_solicitud(descripcion: str) -> str:
        """PLACEHOLDER"""
        resultado = almacen.registrar_solicitud(telefono, descripcion[:1000])
        avisar_equipo(f"🛍️ Nueva solicitud {resultado['codigo']} de {telefono}: {descripcion[:300]}")
        return _json(resultado)

    registrar_solicitud.description = (
        negocio.texto_solicitud
        + " Incluye en la descripción producto, cantidad y cualquier detalle que dio la persona."
    )

    @tool(NOMBRE_ESCALAR)
    def escalar_a_humano(motivo: str, resumen: str) -> str:
        """Pasa la conversación a una persona del equipo y deja de responder hasta que
        la persona la devuelva. Úsala en los casos de escalamiento de tus reglas.
        motivo: categoría corta (queja, descuento, pago, otro).
        resumen: 1 a 3 frases con lo que pide la persona, para que el equipo no tenga
        que leer toda la conversación."""
        almacen.crear_escalamiento(telefono, motivo, resumen)
        avisar_equipo(f"🙋 Conversación escalada ({motivo}) — {telefono}: {resumen[:500]}")
        return _json({"escalado": True})

    return [buscar_productos, consultar_mis_pedidos, consultar_mi_saldo,
            registrar_solicitud, escalar_a_humano]
