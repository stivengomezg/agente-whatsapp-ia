"""Carga un negocio: sus reglas (negocio.yaml) y su conector de solo lectura."""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.conector import Conector

RAIZ_NEGOCIOS = Path(__file__).resolve().parent.parent / "negocios"


@dataclass
class Negocio:
    clave: str
    nombre: str
    descripcion: str
    reglas: list[str]
    escalar_cuando: list[str]
    horario: str
    mensaje_escalamiento: str
    mensaje_falla: str
    texto_solicitud: str = "Registra una solicitud para que el equipo la confirme."
    conector: Conector = field(repr=False, default=None)  # type: ignore[assignment]


def cargar_negocio(clave: str) -> Negocio:
    carpeta = RAIZ_NEGOCIOS / clave
    if not (carpeta / "negocio.yaml").exists():
        raise FileNotFoundError(f"No existe el negocio '{clave}' en {carpeta}")

    datos = yaml.safe_load((carpeta / "negocio.yaml").read_text(encoding="utf-8"))
    conector = importlib.import_module(f"negocios.{clave}.conector").crear_conector()

    return Negocio(
        clave=clave,
        nombre=datos["nombre"],
        descripcion=datos["descripcion"].strip(),
        reglas=datos.get("reglas", []),
        escalar_cuando=datos.get("escalar_cuando", []),
        horario=datos.get("horario", ""),
        mensaje_escalamiento=datos["mensaje_escalamiento"].strip(),
        mensaje_falla=datos["mensaje_falla"].strip(),
        texto_solicitud=datos.get("texto_solicitud", Negocio.texto_solicitud).strip(),
        conector=conector,
    )
