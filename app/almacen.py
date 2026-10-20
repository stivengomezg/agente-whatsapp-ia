"""Base de datos PROPIA del agente (no la del negocio).

Guarda memoria, solicitudes, escalamientos, eventos y el estado del servicio.
Funciona con SQLite (desarrollo, demo) o con un Postgres/Supabase propio del
proveedor del agente, cambiando solo AGENTE_DB_URL.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, Integer, MetaData, String,
                        Table, Text, create_engine, func, insert, select, update)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.conector import con_reintentos, telefono_norm

meta = MetaData()
_id = BigInteger().with_variant(Integer, "sqlite")  # autoincremento en ambos motores

memoria = Table("memoria", meta,
                Column("telefono", String(15), primary_key=True),
                Column("resumen", Text, nullable=False, default=""),
                Column("actualizado_en", DateTime(timezone=True)))

solicitudes = Table("solicitudes", meta,
                    Column("id", _id, primary_key=True, autoincrement=True),
                    Column("codigo", String(20), unique=True),
                    Column("telefono", String(15), nullable=False, index=True),
                    Column("descripcion", Text, nullable=False),
                    Column("estado", String(30), nullable=False, default="pendiente_revision"),
                    Column("creada_en", DateTime(timezone=True)),
                    Column("revisada_en", DateTime(timezone=True)))

escalamientos = Table("escalamientos", meta,
                      Column("id", _id, primary_key=True, autoincrement=True),
                      Column("telefono", String(15), nullable=False, index=True),
                      Column("motivo", String(200), nullable=False),
                      Column("resumen", Text, nullable=False),
                      Column("resuelto", Boolean, nullable=False, default=False),
                      Column("creado_en", DateTime(timezone=True)),
                      Column("resuelto_en", DateTime(timezone=True)))

eventos = Table("eventos", meta,
                Column("id", _id, primary_key=True, autoincrement=True),
                Column("telefono", String(15), index=True),
                Column("tipo", String(40), nullable=False, index=True),
                Column("detalle", JSON, nullable=False, default=dict),
                Column("creado_en", DateTime(timezone=True), index=True))

ajustes = Table("ajustes", meta,
                Column("clave", String(40), primary_key=True),
                Column("valor", Text, nullable=False))

ESTADOS_SOLICITUD = ("pendiente_revision", "convertida_en_pedido", "descartada")


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def crear_motor(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


class Almacen:
    def __init__(self, url: str = "sqlite://") -> None:
        self.motor = crear_motor(url)
        meta.create_all(self.motor)

    # ---------------- memoria ----------------
    @con_reintentos()
    def leer_memoria(self, telefono: str) -> str:
        with self.motor.connect() as c:
            fila = c.execute(select(memoria.c.resumen).where(memoria.c.telefono == telefono_norm(telefono))).first()
        return fila[0] if fila else ""

    @con_reintentos()
    def guardar_memoria(self, telefono: str, resumen: str) -> None:
        tel, datos = telefono_norm(telefono), {"resumen": resumen[:2000], "actualizado_en": _ahora()}
        with self.motor.begin() as c:
            if c.execute(update(memoria).where(memoria.c.telefono == tel).values(**datos)).rowcount == 0:
                c.execute(insert(memoria).values(telefono=tel, **datos))

    # ---------------- solicitudes ----------------
    @con_reintentos(intentos=1)  # escribir: no reintentar para no duplicar
    def registrar_solicitud(self, telefono: str, descripcion: str) -> dict:
        with self.motor.begin() as c:
            nuevo = c.execute(insert(solicitudes).values(
                telefono=telefono_norm(telefono), descripcion=descripcion[:1000],
                estado="pendiente_revision", creada_en=_ahora())).inserted_primary_key[0]
            codigo = f"SOL-{nuevo:04d}"
            c.execute(update(solicitudes).where(solicitudes.c.id == nuevo).values(codigo=codigo))
        return {"codigo": codigo, "estado": "pendiente_revision"}

    def listar_solicitudes(self, estado: str | None = "pendiente_revision", limite: int = 100) -> list[dict]:
        q = select(solicitudes).order_by(solicitudes.c.id.desc()).limit(limite)
        if estado:
            q = q.where(solicitudes.c.estado == estado)
        with self.motor.connect() as c:
            return [dict(f._mapping) for f in c.execute(q)]

    def cambiar_estado_solicitud(self, solicitud_id: int, estado: str) -> None:
        if estado not in ESTADOS_SOLICITUD:
            raise ValueError(f"Estado inválido: {estado}")
        with self.motor.begin() as c:
            c.execute(update(solicitudes).where(solicitudes.c.id == solicitud_id)
                      .values(estado=estado, revisada_en=_ahora()))

    # ---------------- escalamientos ----------------
    @con_reintentos()
    def escalamiento_abierto(self, telefono: str) -> bool:
        with self.motor.connect() as c:
            return c.execute(select(escalamientos.c.id).where(
                escalamientos.c.telefono == telefono_norm(telefono),
                escalamientos.c.resuelto.is_(False)).limit(1)).first() is not None

    @con_reintentos()
    def crear_escalamiento(self, telefono: str, motivo: str, resumen: str) -> None:
        with self.motor.begin() as c:
            c.execute(insert(escalamientos).values(telefono=telefono_norm(telefono), motivo=motivo[:200],
                                                   resumen=resumen[:2000], resuelto=False, creado_en=_ahora()))

    @con_reintentos()
    def resolver_escalamiento(self, telefono: str) -> None:
        with self.motor.begin() as c:
            c.execute(update(escalamientos).where(escalamientos.c.telefono == telefono_norm(telefono),
                                                  escalamientos.c.resuelto.is_(False))
                      .values(resuelto=True, resuelto_en=_ahora()))

    def escalamientos_abiertos(self) -> list[dict]:
        with self.motor.connect() as c:
            return [dict(f._mapping) for f in c.execute(
                select(escalamientos).where(escalamientos.c.resuelto.is_(False)).order_by(escalamientos.c.id))]

    def ultimo_escalamiento(self) -> dict | None:
        with self.motor.connect() as c:
            fila = c.execute(select(escalamientos).order_by(escalamientos.c.id.desc()).limit(1)).first()
        return dict(fila._mapping) if fila else None

    # ---------------- eventos y métricas ----------------
    def registrar_evento(self, telefono: str | None, tipo: str, detalle: dict) -> None:
        try:  # el registro nunca debe tumbar una conversación
            with self.motor.begin() as c:
                c.execute(insert(eventos).values(telefono=telefono and telefono_norm(telefono), tipo=tipo,
                                                 detalle=detalle, creado_en=_ahora()))
        except Exception:  # noqa: BLE001
            pass

    def contar_eventos(self, tipo: str) -> int:
        with self.motor.connect() as c:
            return c.execute(select(func.count()).select_from(eventos).where(eventos.c.tipo == tipo)).scalar_one()

    def metricas(self, dias: int = 30) -> dict:
        desde = _ahora() - timedelta(days=dias)
        with self.motor.connect() as c:
            filas = c.execute(select(eventos.c.tipo, eventos.c.telefono, eventos.c.detalle)
                              .where(eventos.c.creado_en >= desde)).all()
            solic = c.execute(select(solicitudes.c.estado, func.count()).where(solicitudes.c.creada_en >= desde)
                              .group_by(solicitudes.c.estado)).all()
            escal = c.execute(select(escalamientos.c.telefono).where(escalamientos.c.creado_en >= desde)).all()
        entrantes = [f for f in filas if f.tipo == "mensaje_entrante"]
        clientes = {f.telefono for f in entrantes}
        escalados = {f.telefono for f in escal}
        llamadas = [f.detalle or {} for f in filas if f.tipo == "llamada_modelo"]
        return {
            "dias": dias,
            "clientes_atendidos": len(clientes),
            "mensajes_recibidos": len(entrantes),
            "resueltos_sin_persona": len(clientes - escalados),
            "escalamientos": len(escal),
            "fallas_tecnicas": sum(f.tipo == "error" for f in filas),
            "solicitudes": {estado: n for estado, n in solic},
            "tokens_entrada": sum(d.get("tokens_entrada") or 0 for d in llamadas),
            "tokens_salida": sum(d.get("tokens_salida") or 0 for d in llamadas),
        }

    # ---------------- estado del servicio ----------------
    def leer_ajuste(self, clave: str, defecto: str = "") -> str:
        with self.motor.connect() as c:
            fila = c.execute(select(ajustes.c.valor).where(ajustes.c.clave == clave)).first()
        return fila[0] if fila else defecto

    def guardar_ajuste(self, clave: str, valor: str) -> None:
        with self.motor.begin() as c:
            if c.execute(update(ajustes).where(ajustes.c.clave == clave).values(valor=valor)).rowcount == 0:
                c.execute(insert(ajustes).values(clave=clave, valor=valor))

    def estado_servicio(self, hoy: date | None = None) -> str:
        """activo | suspendido (proveedor) | prueba_vencida | pausado (cliente)."""
        if self.leer_ajuste("suspendido") == "si":
            return "suspendido"
        fin = self.leer_ajuste("prueba_hasta")
        if fin and (hoy or datetime.now().date()) > date.fromisoformat(fin):
            return "prueba_vencida"
        if self.leer_ajuste("pausado") == "si":
            return "pausado"
        return "activo"
