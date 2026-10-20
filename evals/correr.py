"""Corre el set de evaluaciones con el modelo real y los datos de demo.

    python evals/correr.py            # todos los casos
    python evals/correr.py descuento  # solo los que contienen "descuento" en el nombre

Cada caso arranca con datos limpios. Guarda el detalle en evals/resultados_<fecha>.json.
Usa la API de Anthropic: con los modelos por defecto cuesta pocos centavos de dólar.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agente import Agente  # noqa: E402
from app.almacen import Almacen  # noqa: E402
from app.negocio import cargar_negocio  # noqa: E402

CARPETA = Path(__file__).resolve().parent


def simple(t: str) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def evaluar(caso: dict) -> dict:
    agente = Agente(cargar_negocio("demo"), Almacen("sqlite://"))  # base limpia por caso
    telefono = caso.get("telefono", "3001234567")
    inicio = time.perf_counter()
    herramientas, respuestas = [], []
    for mensaje in caso["mensajes"]:  # se evalúa la conversación completa, no solo el último mensaje
        r = agente.responder(telefono, mensaje)
        herramientas += r.herramientas
        respuestas.append(r.respuesta or "")
    respuesta = simple(" ".join(respuestas))

    fallas = []
    for h in caso.get("debe_usar", []):
        if h not in herramientas:
            fallas.append(f"no usó {h}")
    for h in caso.get("no_debe_usar", []):
        if h in herramientas:
            fallas.append(f"usó {h}")
    if caso.get("estado") and r.estado != caso["estado"]:
        fallas.append(f"estado {r.estado}, se esperaba {caso['estado']}")
    for t in caso.get("debe_contener", []):
        if simple(t) not in respuesta:
            fallas.append(f"falta '{t}'")
    for t in caso.get("no_debe_contener", []):
        if simple(t) in respuesta:
            fallas.append(f"contiene '{t}'")

    return {"caso": caso["nombre"], "ok": not fallas, "fallas": fallas, "estado": r.estado,
            "herramientas": herramientas, "respuestas": respuestas,
            "segundos": round(time.perf_counter() - inicio, 1)}


def main() -> None:
    logging.basicConfig(level=logging.ERROR)  # sin avisos de webhook durante las evaluaciones
    filtro = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    casos = [c for c in yaml.safe_load((CARPETA / "casos.yaml").read_text(encoding="utf-8"))
             if filtro in c["nombre"].lower()]
    resultados = []
    for caso in casos:
        res = evaluar(caso)
        resultados.append(res)
        marca = "✅" if res["ok"] else "❌"
        print(f"{marca} {res['caso']}  ({res['segundos']} s)" + ("" if res["ok"] else f"  → {'; '.join(res['fallas'])} [terminó en: {res['estado']}]"))

    aprobados = sum(r["ok"] for r in resultados)
    print(f"\n{aprobados}/{len(resultados)} casos aprobados ({aprobados / max(len(resultados), 1):.0%})")
    salida = CARPETA / f"resultados_{datetime.now():%Y%m%d_%H%M}.json"
    salida.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detalle: {salida}")
    sys.exit(0 if aprobados == len(resultados) else 1)


if __name__ == "__main__":
    main()
