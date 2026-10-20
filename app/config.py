"""Configuración leída de variables de entorno (.env)."""
import os

from dotenv import load_dotenv

load_dotenv()

NEGOCIO = os.getenv("NEGOCIO", "demo")

# --- IA ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODELO = os.getenv("MODELO", "claude-sonnet-5-5")              # conversación
MODELO_RAPIDO = os.getenv("MODELO_RAPIDO", "claude-haiku-5-5")  # resúmenes de memoria
MAX_RONDAS_HERRAMIENTAS = int(os.getenv("MAX_RONDAS_HERRAMIENTAS", "6"))
MENSAJES_EN_CONTEXTO = int(os.getenv("MENSAJES_EN_CONTEXTO", "12"))

# --- Base PROPIA del agente (memoria, solicitudes, métricas). Nunca la del negocio. ---
AGENTE_DB_URL = os.getenv("AGENTE_DB_URL", "sqlite:///agente.db")

# --- Conexión de SOLO LECTURA al negocio (la usa el conector de cada negocio) ---
CONECTOR_DB_URL = os.getenv("CONECTOR_DB_URL", "")

# --- Panel web para el negocio ---
PANEL_USUARIO = os.getenv("PANEL_USUARIO", "admin")
PANEL_CLAVE = os.getenv("PANEL_CLAVE", "")

# --- WhatsApp Cloud API ---
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

# --- Avisos: errores técnicos → desarrollador; escalamientos y solicitudes → equipo ---
ALERTA_WEBHOOK_URL = os.getenv("ALERTA_WEBHOOK_URL", "")
EQUIPO_WEBHOOK_URL = os.getenv("EQUIPO_WEBHOOK_URL", "")

# --- Operación ---
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")                     # del proveedor: suspender, prueba, devolver
LIMITE_MENSAJES_10_MIN = int(os.getenv("LIMITE_MENSAJES_10_MIN", "20"))
ZONA_HORARIA = "America/Bogota"
