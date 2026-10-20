"""Modelo de IA falso para probar el flujo sin gastar tokens ni depender de la red."""
from __future__ import annotations

import itertools
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_ids = itertools.count(1)


def llamar(nombre: str, **args: Any) -> AIMessage:
    """Respuesta del modelo que pide usar una herramienta."""
    return AIMessage(content="", tool_calls=[{"name": nombre, "args": args, "id": f"call_{next(_ids)}"}])


def decir(texto: str) -> AIMessage:
    return AIMessage(content=texto)


class ModeloGuionado(BaseChatModel):
    """Devuelve las respuestas en el orden dado y guarda lo que recibió."""

    guion: list[Any] = []
    recibidos: list[list[BaseMessage]] = []
    herramientas_ofrecidas: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "guionado"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        self.herramientas_ofrecidas = [t.name for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001
        self.recibidos.append(list(messages))
        if not self.guion:
            raise AssertionError("El modelo falso se quedó sin respuestas")
        siguiente = self.guion.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        mensaje = siguiente if isinstance(siguiente, AIMessage) else AIMessage(content=str(siguiente))
        return ChatResult(generations=[ChatGeneration(message=mensaje)])


def modelo(*guion: Any) -> ModeloGuionado:
    return ModeloGuionado(guion=list(guion), recibidos=[], herramientas_ofrecidas=[])
