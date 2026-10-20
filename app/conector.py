"""Contrato del conector: la ÚNICA forma en que el agente ve datos del negocio.

Es de solo lectura y cada método está limitado al cliente que escribe (por su
teléfono). Todo lo que el agente guarda (memoria, solicitudes, escalamientos,
métricas) va en su propia base (app/almacen.py), nunca en la del negocio.
Así el agente se conecta y se desconecta sin dejar rastro en el sistema del cliente.
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Any, Callable, Protocol


class ServicioNoDisponible(Exception):
    """La fuente de datos no respondió después de reintentar."""


def con_reintentos(intentos: int = 3, espera: float = 0.5) -> Callable:
    """Reintenta con espera creciente; si sigue fallando, lanza ServicioNoDisponible."""

    def decorador(fn: Callable) -> Callable:
        @wraps(fn)
        def envoltura(*args: Any, **kwargs: Any) -> Any:
            ultimo: Exception | None = None
            for i in range(intentos):
                try:
                    return fn(*args, **kwargs)
                except ServicioNoDisponible:
                    raise
                except Exception as e:  # noqa: BLE001 - cualquier fallo de red/BD
                    ultimo = e
                    if i < intentos - 1:
                        time.sleep(espera * (2**i))
            raise ServicioNoDisponible(f"{fn.__name__}: {ultimo}") from ultimo

        return envoltura

    return decorador


def telefono_norm(telefono: str) -> str:
    """Últimos 10 dígitos: así coinciden +57 300..., 300... y 57300..."""
    return "".join(c for c in telefono if c.isdigit())[-10:]


class Conector(Protocol):
    def cliente(self, telefono: str) -> dict | None: ...
    def buscar_productos(self, texto: str) -> list[dict]: ...
    def pedidos_cliente(self, telefono: str) -> list[dict]: ...
    def saldo_cliente(self, telefono: str) -> dict: ...
