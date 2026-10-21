"""Punto de entrada único: recibe un mensaje y devuelve la respuesta (o None)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app import config
from app.almacen import Almacen
from app.avisos import alertar_desarrollador
from app.conector import telefono_norm
from app.grafo import construir_grafo
from app.negocio import Negocio, cargar_negocio


def formato_whatsapp(texto: str) -> str:
    """WhatsApp usa *negrita* con un asterisco; el markdown **así** se vería con asteriscos."""
    texto = re.sub(r"\*\*(.+?)\*\*", r"*\1*", texto)
    texto = re.sub(r"__(.+?)__", r"_\1_", texto)
    return re.sub(r"^#{1,6}\s*", "", texto, flags=re.MULTILINE)


@dataclass
class Resultado:
    respuesta: str | None   # None = el agente no responde
    estado: str             # respondido | escalado | falla | pausado | suspendido | prueba_vencida
    herramientas: list[str]


class Agente:
    def __init__(self, negocio: Negocio | None = None, almacen: Almacen | None = None,
                 llm: BaseChatModel | None = None, llm_rapido: BaseChatModel | None = None,
                 checkpointer=None) -> None:
        self.negocio = negocio or cargar_negocio(config.NEGOCIO)
        self.almacen = almacen or Almacen(config.AGENTE_DB_URL)
        if llm is None or llm_rapido is None:
            from langchain_anthropic import ChatAnthropic
            llm = llm or ChatAnthropic(model=config.MODELO, max_tokens=1024, timeout=30, max_retries=2)
            llm_rapido = llm_rapido or ChatAnthropic(model=config.MODELO_RAPIDO, max_tokens=400,
                                                     timeout=20, max_retries=2)
        self.llm, self.llm_rapido = llm, llm_rapido
        self.grafo = construir_grafo(self.negocio, self.almacen, llm, llm_rapido, checkpointer)

    def responder(self, telefono: str, texto: str) -> Resultado:
        tel = telefono_norm(telefono)
        if len(tel) < 7:
            raise ValueError("Teléfono inválido")

        # Interruptor: si el servicio no está activo, no se gasta IA ni se toca el negocio.
        servicio = self.almacen.estado_servicio()
        if servicio != "activo":
            self.almacen.registrar_evento(tel, "mensaje_sin_servicio", {"estado": servicio})
            if servicio == "prueba_vencida":
                alertar_desarrollador("prueba_vencida", f"Terminó la prueba de {self.negocio.nombre}: el agente no responde.")
            return Resultado(None, servicio, [])

        final = self.grafo.invoke(
            {"messages": [HumanMessage(texto[:2000])], "telefono": tel},
            config={"configurable": {"thread_id": tel}},
        )
        estado = final.get("resultado") or "respondido"
        if estado == "pausado":
            return Resultado(None, estado, [])
        ultimo = final["messages"][-1]
        respuesta = ultimo.content if isinstance(ultimo, AIMessage) else None
        if isinstance(respuesta, list):
            respuesta = " ".join(b.get("text", "") for b in respuesta if isinstance(b, dict)).strip()
        return Resultado(formato_whatsapp(respuesta) if respuesta else None, estado,
                         final.get("herramientas_usadas", []))

    def devolver_al_agente(self, telefono: str) -> None:
        """Una persona terminó de atender: el agente vuelve a responder."""
        self.almacen.resolver_escalamiento(telefono)
        self.almacen.registrar_evento(telefono, "devuelto_al_agente", {})
