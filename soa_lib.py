"""
Utilidades compartidas para SGIM.

Incluye protocolo SOA por sockets, persistencia SQLite ACID, autenticacion
firmada, cache TTL y respuestas normalizadas para el API Gateway y el ESB.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Optional


DEFAULT_BUS_HOST = "127.0.0.1"
DEFAULT_BUS_PORT = 5000
DATABASE_PATH = os.getenv("SGIM_DB_PATH", "sgim.sqlite3")
JWT_SECRET = os.getenv("JWT_SECRET", "sgim-demo-secret-cambiar-en-produccion")
TOKEN_TTL_SECONDS = int(os.getenv("SGIM_TOKEN_TTL", "1800"))


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def ok(data: Optional[Dict[str, Any]] = None, status: int = 200) -> Dict[str, Any]:
    return {"ok": True, "status": status, "data": data or {}}


def error(message: str, status: int = 400, code: str = "SGIM_ERROR") -> Dict[str, Any]:
    return {"ok": False, "status": status, "error": {"code": code, "message": message}}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Protocolo SOA por sockets
# ---------------------------------------------------------------------------


def connect_to_bus(host: str = DEFAULT_BUS_HOST, port: int = DEFAULT_BUS_PORT) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    return sock


def send_frame(sock: socket.socket, envelope: Dict[str, Any]) -> None:
    payload = json_dumps(envelope).encode("utf-8")
    sock.sendall(str(len(payload)).zfill(8).encode("ascii") + payload)


def receive_frame(sock: socket.socket) -> Optional[Dict[str, Any]]:
    raw_len = _recv_exact(sock, 8)
    if not raw_len:
        return None
    try:
        expected = int(raw_len.decode("ascii"))
    except ValueError:
        return None
    raw_payload = _recv_exact(sock, expected)
    if not raw_payload:
        return None
    return json.loads(raw_payload.decode("utf-8"))


def request_bus(
    service: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    host: str = DEFAULT_BUS_HOST,
    port: int = DEFAULT_BUS_PORT,
    timeout: int = 10,
) -> Dict[str, Any]:
    envelope = {"service": service, "action": action, "payload": payload or {}, "token": token}
    with connect_to_bus(host, port) as sock:
        sock.settimeout(timeout)
        send_frame(sock, envelope)
        response = receive_frame(sock)
        return response or error("El ESB no entregó respuesta.", 502, "ESB_NO_RESPONSE")


def send_message(sock: socket.socket, service_name: str, payload: str) -> None:
    """Compatibilidad con el demo original: envia service/action en JSON."""
    try:
        body = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        body = {"mensaje": payload}
    send_frame(sock, {"service": service_name, "action": body.pop("action", "legacy"), "payload": body})


def receive_message(sock: socket.socket) -> Optional[bytes]:
    frame = receive_frame(sock)
    if frame is None:
        return None
    return json_dumps(frame).encode("utf-8")


def _recv_exact(sock: socket.socket, amount: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < amount:
        chunk = sock.recv(amount - len(chunks))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


# ---------------------------------------------------------------------------
# Seguridad: hash de password y token JWT compacto HS256
# ---------------------------------------------------------------------------


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$", 2)
    except ValueError:
        return False
    actual = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def create_token(claims: Dict[str, Any], ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = dict(claims)
    payload["exp"] = int(time.time()) + ttl_seconds
    payload["iat"] = int(time.time())
    encoded = [_b64json(header), _b64json(payload)]
    signature = _b64bytes(hmac.new(JWT_SECRET.encode(), ".".join(encoded).encode(), hashlib.sha256).digest())
    return ".".join(encoded + [signature])


def validate_token(token: str) -> Dict[str, Any]:
    try:
        header, payload, signature = token.split(".")
        expected = _b64bytes(hmac.new(JWT_SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return error("Token con firma inválida.", 401, "JWT_INVALID_SIGNATURE")
        claims = json.loads(_b64decode(payload))
        if int(claims.get("exp", 0)) < int(time.time()):
            return error("Token expirado.", 401, "JWT_EXPIRED")
        return ok({"claims": claims})
    except Exception:
        return error("Token inválido.", 401, "JWT_INVALID")


def _b64json(data: Dict[str, Any]) -> str:
    return _b64bytes(json_dumps(data).encode("utf-8"))


def _b64bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


# ---------------------------------------------------------------------------
# Cache TTL en memoria, equivalente liviano para sesiones/disponibilidad Redis
# ---------------------------------------------------------------------------


class TTLCache:
    def __init__(self) -> None:
        self._items: Dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.time():
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._items[key] = (time.time() + ttl_seconds, value)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in list(self._items):
                if key.startswith(prefix):
                    self._items.pop(key, None)


# ---------------------------------------------------------------------------
# Base de datos SQL normalizada y transaccional
# ---------------------------------------------------------------------------


class SGIMDatabase:
    def __init__(self, path: str = DATABASE_PATH) -> None:
        self.path = path
        self._write_lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterable[sqlite3.Connection]:
        with self._write_lock:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterable[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._write_lock:
            conn = self.connect()
            conn.executescript(SCHEMA_SQL)
            self._seed(conn)
            conn.close()

    def audit(self, conn: sqlite3.Connection, usuario_id: str, accion: str, tabla: str) -> None:
        conn.execute(
            "INSERT INTO tabla_logs_auditoria(usuario_id, accion, tabla_afectada, timestamp) VALUES (?,?,?,?)",
            (usuario_id, accion, tabla, utcnow()),
        )

    def _seed(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT COUNT(*) FROM tabla_especialidades").fetchone()[0]:
            return
        especialidades = [
            (1, "Medicina General", 30, "Atenciones generales y controles preventivos"),
            (2, "Cardiología", 45, "Diagnóstico y control cardiovascular"),
            (3, "Pediatría", 30, "Atención integral infantil"),
        ]
        conn.executemany("INSERT INTO tabla_especialidades VALUES (?,?,?,?)", especialidades)
        clientes = [
            ("11111111-1", "Ana Pérez", "ana.paciente@sgim.cl", "+56911111111", 34, "F", "Penicilina", utcnow()),
            ("22222222-2", "Luis Rojas", "luis.paciente@sgim.cl", "+56922222222", 42, "M", "Sin alergias", utcnow()),
        ]
        conn.executemany("INSERT INTO tabla_clientes VALUES (?,?,?,?,?,?,?,?)", clientes)
        empleados = [
            ("33333333-3", "Dra. Camila Soto", "camila.medico@sgim.cl", "+56933333333", 39, "F", "medico", 1, utcnow()),
            ("44444444-4", "Dr. Martín Vera", "martin.medico@sgim.cl", "+56944444444", 45, "M", "medico", 2, utcnow()),
            ("55555555-5", "Recepción Central", "recepcion@sgim.cl", "+56222222222", 31, "F", "recepcionista", None, utcnow()),
            ("66666666-6", "Admin SGIM", "admin@sgim.cl", "+56233333333", 37, "M", "administrador", None, utcnow()),
        ]
        conn.executemany("INSERT INTO tabla_empleados VALUES (?,?,?,?,?,?,?,?,?)", empleados)
        for rut, _, email, *_ in clientes:
            conn.execute(
                "INSERT INTO tabla_seguridad_clientes(rut_cliente,email,clave_hash,estado) VALUES (?,?,?,?)",
                (rut, email, hash_password("sgim123"), "activo"),
            )
        for rut, _, email, _, _, _, rol, _, _ in empleados:
            conn.execute(
                "INSERT INTO tabla_seguridad_empleados(rut_empleado,email,clave_hash,estado) VALUES (?,?,?,?)",
                (rut, email, hash_password("sgim123"), "activo"),
            )
        horarios = [
            ("33333333-3", 0, "09:00", "13:00", "activo"),
            ("33333333-3", 2, "09:00", "13:00", "activo"),
            ("33333333-3", 4, "14:00", "18:00", "activo"),
            ("44444444-4", 1, "09:00", "13:00", "activo"),
            ("44444444-4", 3, "14:00", "18:00", "activo"),
        ]
        conn.executemany(
            "INSERT INTO tabla_horarios_medico(rut_medico,dia_semana,hora_inicio,hora_fin,estado) VALUES (?,?,?,?,?)",
            horarios,
        )
        conn.executemany(
            "INSERT INTO tabla_examen_info(examen_id,nombre,requerimientos,especialidad_id) VALUES (?,?,?,?)",
            [(1, "Hemograma", "Ayuno no requerido", 1), (2, "Electrocardiograma", "Reposo previo 10 minutos", 2)],
        )
        conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
    return dict(row) if row else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[Dict[str, Any]]:
    return [dict(row) for row in rows]


def parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def add_minutes(hhmm: str, minutes: int) -> str:
    base = dt.datetime.strptime(hhmm, "%H:%M")
    return (base + dt.timedelta(minutes=minutes)).strftime("%H:%M")


def slot_range(start: str, end: str, minutes: int) -> list[str]:
    slots = []
    current = start
    while add_minutes(current, minutes) <= end:
        slots.append(current)
        current = add_minutes(current, minutes)
    return slots


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tabla_especialidades (
  especialidad_id INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL UNIQUE,
  duracion_atencion_minutos INTEGER NOT NULL CHECK(duracion_atencion_minutos > 0),
  descripcion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_clientes (
  rut_cliente TEXT PRIMARY KEY,
  nombre TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  telefono TEXT,
  edad INTEGER CHECK(edad >= 0),
  sexo TEXT CHECK(sexo IN ('F','M','X')),
  alergias TEXT,
  fecha_registro TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_empleados (
  rut_empleado TEXT PRIMARY KEY,
  nombre TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  telefono TEXT,
  edad INTEGER CHECK(edad >= 0),
  sexo TEXT CHECK(sexo IN ('F','M','X')),
  rol TEXT NOT NULL CHECK(rol IN ('medico','recepcionista','administrador')),
  especialidad_id INTEGER REFERENCES tabla_especialidades(especialidad_id),
  fecha_contratacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_citas (
  cita_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_cliente TEXT NOT NULL REFERENCES tabla_clientes(rut_cliente),
  rut_medico TEXT NOT NULL REFERENCES tabla_empleados(rut_empleado),
  especialidad_id INTEGER NOT NULL REFERENCES tabla_especialidades(especialidad_id),
  fecha TEXT NOT NULL,
  hora_inicio TEXT NOT NULL,
  hora_fin TEXT NOT NULL,
  sala TEXT NOT NULL,
  estado TEXT NOT NULL CHECK(estado IN ('solicitada','confirmada','cancelada','atendida','inasistida','bloqueada')),
  motivo_cancelacion TEXT,
  fecha_creacion TEXT NOT NULL,
  fecha_modificacion TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_citas_medico_horario_activa
ON tabla_citas(rut_medico, fecha, hora_inicio)
WHERE estado IN ('solicitada','confirmada','atendida','bloqueada');

CREATE INDEX IF NOT EXISTS idx_citas_paciente_fecha ON tabla_citas(rut_cliente, fecha);
CREATE INDEX IF NOT EXISTS idx_citas_medico_fecha ON tabla_citas(rut_medico, fecha);

CREATE TABLE IF NOT EXISTS tabla_fichas_clinicas (
  ficha_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_cliente TEXT NOT NULL REFERENCES tabla_clientes(rut_cliente),
  rut_medico TEXT NOT NULL REFERENCES tabla_empleados(rut_empleado),
  cita_id INTEGER REFERENCES tabla_citas(cita_id),
  diagnostico TEXT NOT NULL,
  observaciones TEXT,
  fecha_registro TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_recetas (
  receta_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_cliente TEXT NOT NULL REFERENCES tabla_clientes(rut_cliente),
  rut_medico TEXT NOT NULL REFERENCES tabla_empleados(rut_empleado),
  ficha_id INTEGER REFERENCES tabla_fichas_clinicas(ficha_id),
  medicamento TEXT NOT NULL,
  dosificacion TEXT NOT NULL,
  duracion_tratamiento TEXT NOT NULL,
  fecha_emision TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_examen_info (
  examen_id INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL,
  requerimientos TEXT,
  especialidad_id INTEGER REFERENCES tabla_especialidades(especialidad_id)
);

CREATE TABLE IF NOT EXISTS tabla_resultados_examenes (
  resultado_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_cliente TEXT NOT NULL REFERENCES tabla_clientes(rut_cliente),
  examen_id INTEGER NOT NULL REFERENCES tabla_examen_info(examen_id),
  rut_medico_solicitante TEXT NOT NULL REFERENCES tabla_empleados(rut_empleado),
  resultado TEXT NOT NULL,
  fecha_examen TEXT NOT NULL,
  fecha_resultado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tabla_horarios_medico (
  horario_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_medico TEXT NOT NULL REFERENCES tabla_empleados(rut_empleado),
  dia_semana INTEGER NOT NULL CHECK(dia_semana BETWEEN 0 AND 6),
  hora_inicio TEXT NOT NULL,
  hora_fin TEXT NOT NULL,
  estado TEXT NOT NULL CHECK(estado IN ('activo','inactivo'))
);

CREATE TABLE IF NOT EXISTS tabla_seguridad_clientes (
  usuario_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_cliente TEXT NOT NULL UNIQUE REFERENCES tabla_clientes(rut_cliente),
  email TEXT NOT NULL UNIQUE,
  clave_hash TEXT NOT NULL,
  fecha_ultimo_acceso TEXT,
  estado TEXT NOT NULL CHECK(estado IN ('activo','bloqueado'))
);

CREATE TABLE IF NOT EXISTS tabla_seguridad_empleados (
  usuario_id INTEGER PRIMARY KEY AUTOINCREMENT,
  rut_empleado TEXT NOT NULL UNIQUE REFERENCES tabla_empleados(rut_empleado),
  email TEXT NOT NULL UNIQUE,
  clave_hash TEXT NOT NULL,
  fecha_ultimo_acceso TEXT,
  estado TEXT NOT NULL CHECK(estado IN ('activo','bloqueado'))
);

CREATE TABLE IF NOT EXISTS tabla_notificaciones (
  notificacion_id INTEGER PRIMARY KEY AUTOINCREMENT,
  destinatario TEXT NOT NULL,
  canal TEXT NOT NULL,
  asunto TEXT NOT NULL,
  mensaje TEXT NOT NULL,
  estado TEXT NOT NULL,
  intentos INTEGER NOT NULL DEFAULT 0,
  fecha_creacion TEXT NOT NULL,
  fecha_envio TEXT
);

CREATE TABLE IF NOT EXISTS tabla_logs_auditoria (
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  usuario_id TEXT,
  accion TEXT NOT NULL,
  tabla_afectada TEXT NOT NULL,
  timestamp TEXT NOT NULL
);
"""
