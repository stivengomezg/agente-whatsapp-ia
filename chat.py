"""Chat de prueba en la terminal, sin WhatsApp.

    python chat.py                    # como clienta de demo con pedidos
    python chat.py --telefono 3109998877   # como número nuevo

Comandos: /devolver (devuelve la conversación al agente), /salir
"""
from __future__ import annotations

import argparse
import logging

from app.agente import Agente


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--telefono", default="3000000001")
    args = p.parse_args()
    logging.basicConfig(level=logging.ERROR)  # sin avisos de webhook en pruebas locales

    agente = Agente()
    print(f"Agente de {agente.negocio.nombre}. Escribes como {args.telefono}. /salir para terminar.\n")
    while True:
        try:
            texto = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not texto:
            continue
        if texto == "/salir":
            break
        if texto == "/devolver":
            agente.devolver_al_agente(args.telefono)
            print("(La conversación volvió al agente)\n")
            continue
        r = agente.responder(args.telefono, texto)
        detalle = f"[{r.estado}" + (f" · {', '.join(r.herramientas)}" if r.herramientas else "") + "]"
        print(f"Agente: {r.respuesta or '(en pausa: la atiende una persona)'}  {detalle}\n")


if __name__ == "__main__":
    main()
