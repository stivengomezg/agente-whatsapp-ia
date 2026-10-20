"""Orquestación del agente con LangGraph.

    inicio ──(escalamiento abierto)──────────────────────────────► FIN (no responde)
      │
      ▼
    agente ◄──────────┐
      │ pide herramientas
      ▼               │
    herramientas ─────┘ (máx. MAX_RONDAS_HERRAMIENTAS rondas)
      │  escaló ──► cierre_escalado ──► memoria ──► FIN
      │  falla  ──► falla (avisa y escala) ──────────► FIN
      ▼
    agente responde ──► memoria ──► FIN
"""
from __future__ import annotations

import logging
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage, SystemMessage,
                                     ToolMessage, trim_messages)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app import config
from app.avisos import alertar_desarrollador
from app.almacen import Almacen
from app.herramientas import NOMBRE_ESCALAR, crear_herramientas
from app.negocio import Negocio
from app.prompt import PROMPT_MEMORIA, construir_contexto, instrucciones_fijas
from app.conector import ServicioNoDisponible

log = logging.getLogger("agente.grafo")


class Estado(MessagesState):
    telefono: str
    cliente: dict | None
    memoria: str
    resultado: str          # respondido | escalado | falla | pausado
    rondas: int
    herramientas_usadas: list[str]
    error: str
    contexto: str           # fecha, cliente y memoria; fijo durante todo el mensaje


def _texto(m: BaseMessage) -> str:
    if isinstance(m.content, str):
        return m.content
    return " ".join(b.get("text", "") for b in m.content if isinstance(b, dict))


BLOQUES_RAZONAMIENTO = ("thinking", "redacted_thinking")


def _sin_razonamiento_previo(mensajes: list[BaseMessage]) -> list[BaseMessage]:
    """Quita el razonamiento interno de los mensajes anteriores al actual.

    La API lo exige solo dentro del turno en curso (mientras usa herramientas) y
    lo valida contra las instrucciones con que se creó; reenviarlo de turnos
    viejos no aporta y puede ser rechazado.
    """
    ultimo_humano = max((i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)), default=-1)
    limpios = []
    for i, m in enumerate(mensajes):
        if i < ultimo_humano and isinstance(m, AIMessage) and isinstance(m.content, list):
            bloques = [b for b in m.content if not (isinstance(b, dict) and b.get("type") in BLOQUES_RAZONAMIENTO)]
            m = m.model_copy(update={"content": bloques or ""})
        limpios.append(m)
    return limpios


def _cerrar_llamadas_pendientes(mensajes: list[BaseMessage], nota: str) -> list[ToolMessage]:
    """Responde las llamadas a herramientas que quedaron sin resultado.

    La API exige que cada llamada tenga su resultado; si no, la conversación
    siguiente fallaría.
    """
    ultimo = mensajes[-1] if mensajes else None
    if isinstance(ultimo, AIMessage) and ultimo.tool_calls:
        return [ToolMessage(content=nota, tool_call_id=tc["id"], name=tc["name"])
                for tc in ultimo.tool_calls]
    return []


def construir_grafo(negocio: Negocio, almacen: Almacen, llm: BaseChatModel, llm_rapido: BaseChatModel,
                    checkpointer=None):
    datos = negocio.conector  # sistema del negocio, solo lectura (almacen = base propia del agente)
    fijas = instrucciones_fijas(negocio)

    # ---------------- nodos ----------------
    def inicio(estado: Estado) -> dict:
        tel = estado["telefono"]
        base = {"rondas": 0, "herramientas_usadas": [], "error": "", "resultado": ""}
        try:
            if almacen.escalamiento_abierto(tel):
                almacen.registrar_evento(tel, "mensaje_en_pausa", {"texto": _texto(estado["messages"][-1])[:500]})
                return {**base, "resultado": "pausado"}
            cliente = datos.cliente(tel)
            memoria = almacen.leer_memoria(tel)
        except ServicioNoDisponible as e:
            return {**base, "resultado": "falla", "error": str(e)}
        almacen.registrar_evento(tel, "mensaje_entrante", {"texto": _texto(estado["messages"][-1])[:500]})
        return {**base, "cliente": cliente, "memoria": memoria, "contexto": construir_contexto(cliente, memoria)}

    def agente(estado: Estado) -> dict:
        herramientas = crear_herramientas(negocio, almacen, estado["telefono"])
        historial = trim_messages(estado["messages"], strategy="last", token_counter=len,
                                  max_tokens=config.MENSAJES_EN_CONTEXTO,
                                  start_on="human", include_system=False)
        sistema = SystemMessage(content=[
            {"type": "text", "text": fijas, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": estado.get("contexto") or construir_contexto(None, "")},
        ])
        try:
            respuesta = llm.bind_tools(herramientas).invoke([sistema, *_sin_razonamiento_previo(historial)])
        except Exception as e:  # noqa: BLE001 - caída o límite del proveedor de IA
            return {"resultado": "falla", "error": f"modelo: {e}"}
        uso = getattr(respuesta, "usage_metadata", None) or {}
        almacen.registrar_evento(estado["telefono"], "llamada_modelo", {
            "tokens_entrada": uso.get("input_tokens"), "tokens_salida": uso.get("output_tokens"),
            "tokens_cache": (uso.get("input_token_details") or {}).get("cache_read")})
        return {"messages": [respuesta]}

    def herramientas(estado: Estado) -> dict:
        tel = estado["telefono"]
        por_nombre = {h.name: h for h in crear_herramientas(negocio, almacen, tel)}
        ultimo: AIMessage = estado["messages"][-1]
        resultados, usadas, escalo, error = [], list(estado.get("herramientas_usadas", [])), False, ""

        for llamada in ultimo.tool_calls:
            nombre = llamada["name"]
            if error:  # ya falló la base: no seguir ejecutando
                contenido = "No ejecutado por una falla anterior."
            elif nombre not in por_nombre:
                contenido = f"La herramienta '{nombre}' no existe."
            else:
                try:
                    contenido = por_nombre[nombre].invoke(llamada["args"])
                    usadas.append(nombre)
                    escalo = escalo or nombre == NOMBRE_ESCALAR
                except ServicioNoDisponible as e:
                    error, contenido = str(e), "Error: el sistema no respondió."
                except Exception as e:  # noqa: BLE001 - argumentos inválidos, etc.
                    contenido = f"Error al ejecutar {nombre}: {e}"
            almacen.registrar_evento(tel, "herramienta", {"nombre": nombre, "args": llamada["args"],
                                                       "ok": not contenido.startswith("Error")})
            resultados.append(ToolMessage(content=contenido, tool_call_id=llamada["id"], name=nombre))

        cambios = {"messages": resultados, "rondas": estado.get("rondas", 0) + 1,
                   "herramientas_usadas": usadas}
        if error:
            cambios.update(resultado="falla", error=error)
        elif escalo:
            cambios["resultado"] = "escalado"
        return cambios

    def limite_pasos(estado: Estado) -> dict:
        """El modelo encadenó demasiadas herramientas: se corta y se escala."""
        tel = estado["telefono"]
        cerradas = _cerrar_llamadas_pendientes(estado["messages"], "No ejecutado: límite de pasos.")
        alertar_desarrollador("limite_pasos", f"Límite de {config.MAX_RONDAS_HERRAMIENTAS} pasos con {tel}")
        try:
            almacen.crear_escalamiento(tel, "limite_pasos", "El agente no logró resolver la consulta.")
        except ServicioNoDisponible:
            pass
        return {"messages": [*cerradas, AIMessage(negocio.mensaje_escalamiento)], "resultado": "escalado"}

    def cierre_escalado(estado: Estado) -> dict:
        almacen.registrar_evento(estado["telefono"], "escalamiento", {})
        return {"messages": [AIMessage(negocio.mensaje_escalamiento)]}

    def falla(estado: Estado) -> dict:
        """No se inventa nada: se avisa a la persona, al equipo y al desarrollador."""
        tel = estado["telefono"]
        cerradas = _cerrar_llamadas_pendientes(estado["messages"], "No ejecutado por una falla.")
        alertar_desarrollador(f"falla:{estado.get('error', '')[:40]}", f"Falla con {tel}: {estado.get('error')}")
        try:
            almacen.crear_escalamiento(tel, "falla_tecnica", "El agente no pudo consultar el sistema.")
        except ServicioNoDisponible:
            pass  # la base sigue caída; el aviso al desarrollador ya salió
        almacen.registrar_evento(tel, "error", {"detalle": estado.get("error", "")[:500]})
        return {"messages": [*cerradas, AIMessage(negocio.mensaje_falla)]}

    def memoria(estado: Estado) -> dict:
        """Actualiza el resumen de largo plazo solo si pasó algo relevante (ahorra costo)."""
        respuesta = estado["messages"][-1]
        if isinstance(respuesta, AIMessage) and estado.get("resultado") != "escalado":
            almacen.registrar_evento(estado["telefono"], "respuesta", {"texto": _texto(respuesta)[:500]})
        if not estado.get("herramientas_usadas"):
            return {"resultado": estado.get("resultado") or "respondido"}

        recientes = [m for m in estado["messages"][-10:] if isinstance(m, (HumanMessage, AIMessage)) and _texto(m)]
        conversacion = "\n".join(
            f"{'Cliente' if isinstance(m, HumanMessage) else 'Agente'}: {_texto(m)}" for m in recientes)
        try:
            nueva = llm_rapido.invoke(PROMPT_MEMORIA.format(
                nombre=negocio.nombre, memoria=estado.get("memoria") or "vacía", conversacion=conversacion))
            almacen.guardar_memoria(estado["telefono"], _texto(nueva).strip()[:1500])
        except Exception as e:  # noqa: BLE001 - la memoria nunca debe tumbar la respuesta
            log.warning("No se pudo actualizar la memoria: %s", e)
        return {"resultado": estado.get("resultado") or "respondido"}

    # ---------------- rutas ----------------
    def despues_de_inicio(estado: Estado) -> Literal["agente", "falla", "__end__"]:
        if estado["resultado"] == "pausado":
            return END
        return "falla" if estado["resultado"] == "falla" else "agente"

    def despues_de_agente(estado: Estado) -> Literal["herramientas", "limite_pasos", "falla", "memoria"]:
        if estado.get("resultado") == "falla":
            return "falla"
        ultimo = estado["messages"][-1]
        if isinstance(ultimo, AIMessage) and ultimo.tool_calls:
            if estado.get("rondas", 0) >= config.MAX_RONDAS_HERRAMIENTAS:
                return "limite_pasos"
            return "herramientas"
        return "memoria"

    def despues_de_herramientas(estado: Estado) -> Literal["agente", "cierre_escalado", "falla"]:
        if estado.get("resultado") == "falla":
            return "falla"
        return "cierre_escalado" if estado.get("resultado") == "escalado" else "agente"

    g = StateGraph(Estado)
    for nombre, fn in [("inicio", inicio), ("agente", agente), ("herramientas", herramientas),
                       ("limite_pasos", limite_pasos), ("cierre_escalado", cierre_escalado),
                       ("falla", falla), ("memoria", memoria)]:
        g.add_node(nombre, fn)
    g.add_edge(START, "inicio")
    g.add_conditional_edges("inicio", despues_de_inicio)
    g.add_conditional_edges("agente", despues_de_agente)
    g.add_conditional_edges("herramientas", despues_de_herramientas)
    g.add_edge("limite_pasos", "memoria")
    g.add_edge("cierre_escalado", "memoria")
    g.add_edge("falla", END)
    g.add_edge("memoria", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
