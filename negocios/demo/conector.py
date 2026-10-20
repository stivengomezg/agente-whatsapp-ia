"""Conector de demo: datos ficticios en memoria, sin base de datos."""
from __future__ import annotations

import unicodedata

from app.conector import telefono_norm


def normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower().strip()


class ConectorDemo:
    def __init__(self) -> None:
        self.productos = [
            {"nombre": "Bolso tote cuero café", "categoria": "Bolsos", "precio": 185000, "disponibles": 4},
            {"nombre": "Tenis blancos urbanos talla 37", "categoria": "Calzado", "precio": 220000, "disponibles": 2},
            {"nombre": "Perfume floral 100 ml", "categoria": "Perfumería", "precio": 145000, "disponibles": 10},
            {"nombre": "Set de vitaminas x3", "categoria": "Salud", "precio": 98000, "disponibles": 0},
            {"nombre": "Gafas de sol polarizadas", "categoria": "Accesorios", "precio": 120000, "disponibles": 6},
        ]
        self.clientes = {
            "3000000001": {"nombre": "Laura (demo)", "municipio": "Rionegro", "saldo_pendiente": 85000, "saldo_favor": 0},
            "3000000002": {"nombre": "Camilo (demo)", "municipio": "Medellín", "saldo_pendiente": 0, "saldo_favor": 20000},
        }
        self.pedidos = [
            {"numero": "PED-1001", "telefono": "3000000001", "fecha": "2026-10-02", "total": 185000, "abonado": 100000,
             "productos": [{"nombre": "Bolso tote cuero café", "cantidad": 1, "estado": "En tránsito"}]},
            {"numero": "PED-0987", "telefono": "3000000001", "fecha": "2026-09-10", "total": 145000, "abonado": 145000,
             "productos": [{"nombre": "Perfume floral 100 ml", "cantidad": 1, "estado": "Entregado"}]},
        ]

    def cliente(self, telefono: str) -> dict | None:
        c = self.clientes.get(telefono_norm(telefono))
        return {"nombre": c["nombre"], "municipio": c["municipio"]} if c else None

    def buscar_productos(self, texto: str) -> list[dict]:
        palabras = normalizar(texto).split()
        return [dict(p) for p in self.productos
                if all(w in normalizar(f"{p['nombre']} {p['categoria']}") for w in palabras)][:8]

    def pedidos_cliente(self, telefono: str) -> list[dict]:
        tel = telefono_norm(telefono)
        return [{**{k: v for k, v in p.items() if k != "telefono"}, "saldo": p["total"] - p["abonado"]}
                for p in self.pedidos if p["telefono"] == tel]

    def saldo_cliente(self, telefono: str) -> dict:
        c = self.clientes.get(telefono_norm(telefono))
        if not c:
            return {"registrado": False}
        return {"registrado": True, "saldo_pendiente": c["saldo_pendiente"], "saldo_a_favor": c["saldo_favor"]}


def crear_conector() -> ConectorDemo:
    return ConectorDemo()
