"""
SGIM - Servicios SOA, ESB y API Gateway.

Ejecutar:
    python soa_service.py

Levanta:
    - ESB TCP en 127.0.0.1:5000
    - API Gateway HTTP en 127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler, ThreadingTCPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from soa_lib import (
    DATABASE_PATH,
    DEFAULT_BUS_HOST,
    DEFAULT_BUS_PORT,
    SGIMDatabase,
    TTLCache,
    add_minutes,
    create_token,
    error,
    ok,
    parse_date,
    receive_frame,
    request_bus,
    row_to_dict,
    rows_to_dicts,
    send_frame,
    slot_range,
    utcnow,
    validate_token,
    verify_password,
)


PUBLIC_ACTIONS = {("identidad", "login"), ("identidad", "validate_token")}
ROLE_PERMISSIONS = {
    "paciente": {
        ("citas", "disponibilidad"),
        ("citas", "crear"),
        ("citas", "historial"),
        ("citas", "cancelar"),
        ("clinico", "ficha"),
        ("clinico", "receta_obtener"),
        ("clinico", "examen_obtener"),
        ("notificaciones", "estado"),
        ("identidad", "logout"),
        ("identidad", "refresh"),
    },
    "medico": {
        ("citas", "disponibilidad"),
        ("citas", "agenda"),
        ("citas", "bloquear"),
        ("citas", "modificar"),
        ("clinico", "ficha"),
        ("clinico", "registrar_atencion"),
        ("clinico", "receta_emitir"),
        ("clinico", "examen_registrar"),
        ("clinico", "receta_obtener"),
        ("clinico", "examen_obtener"),
        ("notificaciones", "enviar"),
        ("notificaciones", "estado"),
        ("identidad", "logout"),
        ("identidad", "refresh"),
    },
    "recepcionista": {
        ("citas", "disponibilidad"),
        ("citas", "crear"),
        ("citas", "historial"),
        ("citas", "modificar"),
        ("citas", "cancelar"),
        ("citas", "agenda"),
        ("citas", "bloquear"),
        ("citas", "todas"),
        ("notificaciones", "enviar"),
        ("notificaciones", "estado"),
        ("identidad", "logout"),
        ("identidad", "refresh"),
    },
    "administrador": {
        ("citas", "disponibilidad"),
        ("citas", "crear"),
        ("citas", "historial"),
        ("citas", "modificar"),
        ("citas", "cancelar"),
        ("citas", "agenda"),
        ("clinico", "ficha"),
        ("citas", "todas"),
        ("notificaciones", "enviar"),
        ("notificaciones", "estado"),
        ("reporteria", "citas"),
        ("reporteria", "ocupacion"),
        ("reporteria", "inasistencia"),
        ("reporteria", "exportar"),
        ("admin", "usuarios"),
        ("admin", "logs"),
        ("admin", "replicar"),
        ("identidad", "logout"),
        ("identidad", "refresh"),
    },
}


class BaseService:
    name = "base"

    def __init__(self, db: SGIMDatabase, cache: TTLCache, esb: "EnterpriseServiceBus") -> None:
        self.db = db
        self.cache = cache
        self.esb = esb

    def dispatch(self, action: str, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        handler = getattr(self, f"handle_{action}", None)
        if not handler:
            return error(f"Acción no soportada: {self.name}.{action}", 404, "ACTION_NOT_FOUND")
        try:
            return handler(payload, claims)
        except ValueError as exc:
            return error(str(exc), 400, "VALIDATION_ERROR")
        except Exception as exc:
            return error(f"Error interno en {self.name}: {exc}", 500, "SERVICE_ERROR")


class IdentityService(BaseService):
    name = "identidad"

    def handle_login(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        email = payload.get("email") or payload.get("usuario")
        password = payload.get("password") or payload.get("clave")
        if not email or not password:
            raise ValueError("email/usuario y password/clave son obligatorios.")

        with self.db.read() as conn:
            user = None
            rol = None
            for table, rut_field, default_role in (
                ("tabla_clientes", "rut_cliente", "paciente"),
                ("tabla_empleados", "rut_empleado", None),
            ):
                try:
                    row = conn.execute(f"SELECT * FROM {table} WHERE email=?", (email,)).fetchone()
                except Exception:
                    row = None
                if row:
                    user = row
                    rol = row["rol"] if "rol" in row.keys() and row["rol"] else default_role
                    rut = row[rut_field]
                    break

        if not user:
            return error("Credenciales inválidas.", 401, "INVALID_CREDENTIALS")

        keys = set(user.keys())
        stored_password = None
        for key in ("password_hash", "clave_hash", "contrasena_hash", "password", "clave"):
            if key in keys:
                stored_password = user[key]
                break

        if stored_password and not verify_password(password, stored_password):
            return error("Credenciales inválidas.", 401, "INVALID_CREDENTIALS")

        token = create_token({"sub": email, "rut": rut, "rol": rol})
        return ok({"token": token, "usuario": {"email": email, "rut": rut, "rol": rol}})

    def handle_validate_token(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        token = payload.get("token")
        if not token:
            raise ValueError("token es obligatorio.")
        return validate_token(token)

    def handle_logout(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        token = payload.get("token")
        if token:
            self.cache.set(f"revoked:{token}", True, 3600)
        return ok({"estado": "sesion_cerrada"})

    def handle_refresh(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        token = create_token({"sub": claims.get("sub"), "rut": claims.get("rut"), "rol": claims.get("rol")})
        return ok({"token": token})


class AppointmentService(BaseService):
    name = "citas"

    def handle_disponibilidad(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut_medico = payload.get("medico") or payload.get("rut_medico")
        fecha = payload.get("fecha")
        if not rut_medico or not fecha:
            raise ValueError("Debe indicar rut_medico/medico y fecha YYYY-MM-DD.")
        exclude_cita_id = payload.get("exclude_cita_id") or payload.get("cita_id")
        cache_key = f"availability:{rut_medico}:{fecha}:exclude:{exclude_cita_id or ''}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return ok({"cache": "hit", "horarios": cached})
        day = parse_date(fecha).weekday()
        with self.db.read() as conn:
            medico = conn.execute(
                """
                SELECT e.rut_empleado, e.nombre, e.especialidad_id, esp.duracion_atencion_minutos
                FROM tabla_empleados e
                JOIN tabla_especialidades esp ON esp.especialidad_id = e.especialidad_id
                WHERE e.rut_empleado=? AND e.rol='medico'
                """,
                (rut_medico,),
            ).fetchone()
            if not medico:
                return error("Médico no encontrado.", 404, "MEDICO_NOT_FOUND")
            horarios = conn.execute(
                "SELECT hora_inicio,hora_fin FROM tabla_horarios_medico WHERE rut_medico=? AND dia_semana=? AND estado='activo'",
                (rut_medico, day),
            ).fetchall()
            params: list[Any] = [rut_medico, fecha]
            sql = "SELECT hora_inicio FROM tabla_citas WHERE rut_medico=? AND fecha=? AND estado IN ('solicitada','confirmada','atendida','bloqueada')"
            if exclude_cita_id:
                sql += " AND cita_id<>?"
                params.append(exclude_cita_id)
            ocupadas = {
                row["hora_inicio"]
                for row in conn.execute(sql, params)
            }
        slots = []
        for horario in horarios:
            slots.extend(slot_range(horario["hora_inicio"], horario["hora_fin"], medico["duracion_atencion_minutos"]))
        libres = [slot for slot in slots if slot not in ocupadas]
        self.cache.set(cache_key, libres, 300)
        return ok({"cache": "miss", "medico": row_to_dict(medico), "horarios": libres})

    def handle_crear(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut_cliente = payload.get("rut_cliente") or (claims.get("rut") if claims.get("rol") == "paciente" else None)
        rut_medico = payload.get("rut_medico") or payload.get("medico")
        fecha = payload.get("fecha")
        hora_inicio = payload.get("hora") or payload.get("hora_inicio")
        sala = payload.get("sala", "Box 1")
        if not rut_cliente or not rut_medico or not fecha or not hora_inicio:
            raise ValueError("rut_cliente, rut_medico, fecha y hora_inicio son obligatorios.")
        with self.db.transaction() as conn:
            medico = conn.execute(
                """
                SELECT e.especialidad_id, esp.duracion_atencion_minutos
                FROM tabla_empleados e
                JOIN tabla_especialidades esp ON esp.especialidad_id=e.especialidad_id
                WHERE e.rut_empleado=? AND e.rol='medico'
                """,
                (rut_medico,),
            ).fetchone()
            if not medico:
                return error("Médico no encontrado.", 404, "MEDICO_NOT_FOUND")
            disponibilidad = self._slot_is_available(conn, rut_medico, fecha, hora_inicio)
            if not disponibilidad:
                return error("Horario no disponible.", 409, "SLOT_NOT_AVAILABLE")
            hora_fin = add_minutes(hora_inicio, int(medico["duracion_atencion_minutos"]))
            now = utcnow()
            cursor = conn.execute(
                """
                INSERT INTO tabla_citas(rut_cliente,rut_medico,especialidad_id,fecha,hora_inicio,hora_fin,sala,estado,fecha_creacion,fecha_modificacion)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (rut_cliente, rut_medico, medico["especialidad_id"], fecha, hora_inicio, hora_fin, sala, "confirmada", now, now),
            )
            cita_id = cursor.lastrowid
            self.db.audit(conn, claims.get("sub", "sistema"), "CREAR_CITA", "tabla_citas")
        self.cache.delete_prefix(f"availability:{rut_medico}:{fecha}")
        self.esb.publish("cita.creada", {"cita_id": cita_id, "rut_cliente": rut_cliente, "rut_medico": rut_medico, "fecha": fecha, "hora": hora_inicio})
        return ok({"cita_id": cita_id, "estado": "confirmada"}, 201)

    def handle_modificar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        cita_id = payload.get("cita_id") or payload.get("id")
        nueva_fecha = payload.get("fecha")
        nueva_hora = payload.get("hora") or payload.get("hora_inicio")
        if not cita_id or not nueva_fecha or not nueva_hora:
            raise ValueError("cita_id, fecha y hora son obligatorios.")
        with self.db.transaction() as conn:
            cita = conn.execute("SELECT * FROM tabla_citas WHERE cita_id=?", (cita_id,)).fetchone()
            if not cita:
                return error("Cita no encontrada.", 404, "CITA_NOT_FOUND")
            if not self._slot_is_available(conn, cita["rut_medico"], nueva_fecha, nueva_hora, exclude_cita_id=int(cita_id)):
                return error("Nuevo horario no disponible.", 409, "SLOT_NOT_AVAILABLE")
            duracion = conn.execute(
                "SELECT duracion_atencion_minutos FROM tabla_especialidades WHERE especialidad_id=?",
                (cita["especialidad_id"],),
            ).fetchone()[0]
            conn.execute(
                "UPDATE tabla_citas SET fecha=?, hora_inicio=?, hora_fin=?, fecha_modificacion=? WHERE cita_id=?",
                (nueva_fecha, nueva_hora, add_minutes(nueva_hora, duracion), utcnow(), cita_id),
            )
            self.db.audit(conn, claims.get("sub", "sistema"), "MODIFICAR_CITA", "tabla_citas")
        self.cache.delete_prefix(f"availability:{cita['rut_medico']}:")
        self.esb.publish("cita.modificada", {"cita_id": cita_id, "fecha": nueva_fecha, "hora": nueva_hora})
        return ok({"cita_id": cita_id, "estado": "modificada"})

    def handle_cancelar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        cita_id = payload.get("cita_id") or payload.get("id")
        motivo = payload.get("motivo", "Cancelada por usuario")
        if not cita_id:
            raise ValueError("cita_id es obligatorio.")
        with self.db.transaction() as conn:
            cita = conn.execute("SELECT * FROM tabla_citas WHERE cita_id=?", (cita_id,)).fetchone()
            if not cita:
                return error("Cita no encontrada.", 404, "CITA_NOT_FOUND")
            conn.execute(
                "UPDATE tabla_citas SET estado='cancelada', motivo_cancelacion=?, fecha_modificacion=? WHERE cita_id=?",
                (motivo, utcnow(), cita_id),
            )
            self.db.audit(conn, claims.get("sub", "sistema"), "CANCELAR_CITA", "tabla_citas")
        self.cache.delete_prefix(f"availability:{cita['rut_medico']}:{cita['fecha']}")
        self.esb.publish("cita.cancelada", {"cita_id": cita_id, "motivo": motivo})
        return ok({"cita_id": cita_id, "estado": "cancelada"})

    def handle_historial(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut_cliente = payload.get("paciente") or payload.get("rut_cliente") or claims.get("rut")
        with self.db.read() as conn:
            rows = conn.execute(
                """
                SELECT c.*, e.nombre AS medico, esp.nombre AS especialidad
                FROM tabla_citas c
                JOIN tabla_empleados e ON e.rut_empleado=c.rut_medico
                JOIN tabla_especialidades esp ON esp.especialidad_id=c.especialidad_id
                WHERE c.rut_cliente=?
                ORDER BY c.fecha DESC, c.hora_inicio DESC
                """,
                (rut_cliente,),
            ).fetchall()
        return ok({"citas": rows_to_dicts(rows)})

    def handle_todas(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute(
                """
                SELECT c.*, p.nombre AS paciente, e.nombre AS medico, esp.nombre AS especialidad
                FROM tabla_citas c
                JOIN tabla_clientes p ON p.rut_cliente=c.rut_cliente
                JOIN tabla_empleados e ON e.rut_empleado=c.rut_medico
                JOIN tabla_especialidades esp ON esp.especialidad_id=c.especialidad_id
                ORDER BY c.fecha DESC, c.hora_inicio DESC
                """,
            ).fetchall()
        return ok({"citas": rows_to_dicts(rows)})

    def handle_agenda(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut_medico = payload.get("rut_medico") or payload.get("medico") or claims.get("rut")
        fecha = payload.get("fecha") or date.today().isoformat()
        with self.db.read() as conn:
            rows = conn.execute(
                """
                SELECT c.*, p.nombre AS paciente
                FROM tabla_citas c JOIN tabla_clientes p ON p.rut_cliente=c.rut_cliente
                WHERE c.rut_medico=? AND c.fecha=?
                ORDER BY c.hora_inicio
                """,
                (rut_medico, fecha),
            ).fetchall()
        return ok({"agenda": rows_to_dicts(rows)})

    def handle_bloquear(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload)
        payload.setdefault("rut_cliente", "11111111-1")
        payload.setdefault("sala", "Bloqueo")
        response = self.handle_crear(payload, claims)
        if response.get("ok"):
            cita_id = response["data"]["cita_id"]
            with self.db.transaction() as conn:
                conn.execute("UPDATE tabla_citas SET estado='bloqueada' WHERE cita_id=?", (cita_id,))
            response["data"]["estado"] = "bloqueada"
        return response

    def _slot_is_available(self, conn: Any, rut_medico: str, fecha: str, hora: str, exclude_cita_id: Optional[int] = None) -> bool:
        weekday = parse_date(fecha).weekday()
        schedule = conn.execute(
            "SELECT 1 FROM tabla_horarios_medico WHERE rut_medico=? AND dia_semana=? AND hora_inicio<=? AND hora_fin>? AND estado='activo'",
            (rut_medico, weekday, hora, hora),
        ).fetchone()
        if not schedule:
            return False
        params: list[Any] = [rut_medico, fecha, hora]
        sql = "SELECT 1 FROM tabla_citas WHERE rut_medico=? AND fecha=? AND hora_inicio=? AND estado IN ('solicitada','confirmada','atendida','bloqueada')"
        if exclude_cita_id:
            sql += " AND cita_id<>?"
            params.append(exclude_cita_id)
        return conn.execute(sql, params).fetchone() is None


class ClinicalService(BaseService):
    name = "clinico"

    def handle_ficha(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut = payload.get("rut") or payload.get("rut_cliente") or claims.get("rut")
        with self.db.read() as conn:
            paciente = conn.execute("SELECT * FROM tabla_clientes WHERE rut_cliente=?", (rut,)).fetchone()
            fichas = conn.execute("SELECT * FROM tabla_fichas_clinicas WHERE rut_cliente=? ORDER BY fecha_registro DESC", (rut,)).fetchall()
            recetas = conn.execute("SELECT * FROM tabla_recetas WHERE rut_cliente=? ORDER BY fecha_emision DESC", (rut,)).fetchall()
            examenes = conn.execute(
                """
                SELECT r.*, i.nombre AS examen
                FROM tabla_resultados_examenes r JOIN tabla_examen_info i ON i.examen_id=r.examen_id
                WHERE r.rut_cliente=? ORDER BY r.fecha_resultado DESC
                """,
                (rut,),
            ).fetchall()
        if not paciente:
            return error("Paciente no encontrado.", 404, "PACIENTE_NOT_FOUND")
        return ok({"paciente": row_to_dict(paciente), "fichas": rows_to_dicts(fichas), "recetas": rows_to_dicts(recetas), "examenes": rows_to_dicts(examenes)})

    def handle_registrar_atencion(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        required = ["rut_cliente", "diagnostico"]
        for key in required:
            if not payload.get(key):
                raise ValueError(f"{key} es obligatorio.")
        rut_medico = payload.get("rut_medico") or claims.get("rut")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tabla_fichas_clinicas(rut_cliente,rut_medico,cita_id,diagnostico,observaciones,fecha_registro)
                VALUES (?,?,?,?,?,?)
                """,
                (payload["rut_cliente"], rut_medico, payload.get("cita_id"), payload["diagnostico"], payload.get("observaciones", ""), utcnow()),
            )
            if payload.get("cita_id"):
                conn.execute("UPDATE tabla_citas SET estado='atendida', fecha_modificacion=? WHERE cita_id=?", (utcnow(), payload["cita_id"]))
            self.db.audit(conn, claims.get("sub", "sistema"), "REGISTRAR_ATENCION", "tabla_fichas_clinicas")
            return ok({"ficha_id": cursor.lastrowid}, 201)

    def handle_receta_emitir(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("rut_cliente", "medicamento", "dosificacion", "duracion_tratamiento"):
            if not payload.get(key):
                raise ValueError(f"{key} es obligatorio.")
        rut_medico = payload.get("rut_medico") or claims.get("rut")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tabla_recetas(rut_cliente,rut_medico,ficha_id,medicamento,dosificacion,duracion_tratamiento,fecha_emision)
                VALUES (?,?,?,?,?,?,?)
                """,
                (payload["rut_cliente"], rut_medico, payload.get("ficha_id"), payload["medicamento"], payload["dosificacion"], payload["duracion_tratamiento"], utcnow()),
            )
            self.db.audit(conn, claims.get("sub", "sistema"), "EMITIR_RECETA", "tabla_recetas")
            receta_id = cursor.lastrowid
        self.esb.publish("receta.emitida", {"receta_id": receta_id, "rut_cliente": payload["rut_cliente"]})
        return ok({"receta_id": receta_id}, 201)

    def handle_receta_obtener(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            receta = conn.execute("SELECT * FROM tabla_recetas WHERE receta_id=?", (payload.get("receta_id") or payload.get("id"),)).fetchone()
        return ok({"receta": row_to_dict(receta)}) if receta else error("Receta no encontrada.", 404, "RECETA_NOT_FOUND")

    def handle_examen_registrar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("rut_cliente", "examen_id", "resultado", "fecha_examen"):
            if not payload.get(key):
                raise ValueError(f"{key} es obligatorio.")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tabla_resultados_examenes(rut_cliente,examen_id,rut_medico_solicitante,resultado,fecha_examen,fecha_resultado)
                VALUES (?,?,?,?,?,?)
                """,
                (payload["rut_cliente"], payload["examen_id"], payload.get("rut_medico") or claims.get("rut"), payload["resultado"], payload["fecha_examen"], utcnow()),
            )
            self.db.audit(conn, claims.get("sub", "sistema"), "REGISTRAR_EXAMEN", "tabla_resultados_examenes")
            return ok({"resultado_id": cursor.lastrowid}, 201)

    def handle_examen_obtener(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            examen = conn.execute("SELECT * FROM tabla_resultados_examenes WHERE resultado_id=?", (payload.get("resultado_id") or payload.get("id"),)).fetchone()
        return ok({"examen": row_to_dict(examen)}) if examen else error("Examen no encontrado.", 404, "EXAMEN_NOT_FOUND")


class NotificationService(BaseService):
    name = "notificaciones"

    def __init__(self, db: SGIMDatabase, cache: TTLCache, esb: "EnterpriseServiceBus") -> None:
        super().__init__(db, cache, esb)
        self.queue: "queue.Queue[int]" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def handle_enviar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("destinatario", "asunto", "mensaje"):
            if not payload.get(key):
                raise ValueError(f"{key} es obligatorio.")
        notification_id = self.enqueue(payload["destinatario"], payload.get("canal", "email"), payload["asunto"], payload["mensaje"])
        return ok({"notificacion_id": notification_id, "estado": "encolada"}, 202)

    def handle_estado(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        notification_id = payload.get("notificacion_id") or payload.get("id")
        with self.db.read() as conn:
            item = conn.execute("SELECT * FROM tabla_notificaciones WHERE notificacion_id=?", (notification_id,)).fetchone()
        return ok({"notificacion": row_to_dict(item)}) if item else error("Notificación no encontrada.", 404, "NOTIFICACION_NOT_FOUND")

    def enqueue(self, destinatario: str, canal: str, asunto: str, mensaje: str) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tabla_notificaciones(destinatario,canal,asunto,mensaje,estado,intentos,fecha_creacion)
                VALUES (?,?,?,?,?,?,?)
                """,
                (destinatario, canal, asunto, mensaje, "encolada", 0, utcnow()),
            )
            notification_id = cursor.lastrowid
        self.queue.put(notification_id)
        return notification_id

    def _worker(self) -> None:
        while True:
            notification_id = self.queue.get()
            time.sleep(0.2)
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE tabla_notificaciones SET estado='enviada', intentos=intentos+1, fecha_envio=? WHERE notificacion_id=?",
                    (utcnow(), notification_id),
                )
            self.queue.task_done()


class ReportingService(BaseService):
    name = "reporteria"

    def handle_citas(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        periodo = payload.get("periodo", "total")
        with self.db.read() as conn:
            rows = conn.execute("SELECT estado, COUNT(*) AS total FROM tabla_citas GROUP BY estado").fetchall()
        return ok({"periodo": periodo, "resumen": rows_to_dicts(rows)})

    def handle_ocupacion(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        rut_medico = payload.get("medico") or payload.get("rut_medico")
        with self.db.read() as conn:
            rows = conn.execute(
                """
                SELECT e.rut_empleado, e.nombre, esp.nombre AS especialidad,
                       COUNT(c.cita_id) AS citas_agendadas,
                       SUM(CASE WHEN c.estado IN ('confirmada','atendida') THEN 1 ELSE 0 END) AS citas_efectivas
                FROM tabla_empleados e
                LEFT JOIN tabla_especialidades esp ON esp.especialidad_id=e.especialidad_id
                LEFT JOIN tabla_citas c ON c.rut_medico=e.rut_empleado
                WHERE e.rol='medico' AND (? IS NULL OR e.rut_empleado=?)
                GROUP BY e.rut_empleado, e.nombre, esp.nombre
                """,
                (rut_medico, rut_medico),
            ).fetchall()
        return ok({"ocupacion": rows_to_dicts(rows)})

    def handle_inasistencia(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            total = conn.execute("SELECT COUNT(*) FROM tabla_citas").fetchone()[0]
            missed = conn.execute("SELECT COUNT(*) FROM tabla_citas WHERE estado='inasistida'").fetchone()[0]
        tasa = round((missed / total) * 100, 2) if total else 0
        return ok({"total_citas": total, "inasistidas": missed, "tasa_porcentaje": tasa})

    def handle_exportar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        formato = payload.get("formato", "csv").lower()
        if formato not in {"csv", "txt", "pdf", "excel"}:
            return error("Formato soportado: csv, txt, pdf, excel.", 400, "FORMAT_NOT_SUPPORTED")
        report = self.handle_citas(payload, claims)["data"]["resumen"]
        filename = f"reporte_citas.{ 'csv' if formato in {'csv','excel'} else 'txt' }"
        with open(filename, "w", encoding="utf-8") as handle:
            handle.write("estado,total\n")
            for row in report:
                handle.write(f"{row['estado']},{row['total']}\n")
        return ok({"archivo": filename, "formato": formato, "nota": "Exportación liviana para demo; compatible con Excel vía CSV."})


class AdminService(BaseService):
    name = "admin"

    def handle_usuarios(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            pacientes = conn.execute("SELECT rut_cliente AS rut,nombre,email,'paciente' AS rol FROM tabla_clientes").fetchall()
            empleados = conn.execute("SELECT rut_empleado AS rut,nombre,email,rol FROM tabla_empleados").fetchall()
        return ok({"usuarios": rows_to_dicts(pacientes) + rows_to_dicts(empleados)})

    def handle_logs(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM tabla_logs_auditoria ORDER BY log_id DESC LIMIT 100").fetchall()
        return ok({"logs": rows_to_dicts(rows)})

    def handle_replicar(self, payload: Dict[str, Any], claims: Dict[str, Any]) -> Dict[str, Any]:
        target = payload.get("destino", "sgim_replica.sqlite3")
        shutil.copyfile(DATABASE_PATH, target)
        return ok({"origen": DATABASE_PATH, "replica": target, "modo": "replicación diaria bajo demanda"})


class EnterpriseServiceBus:
    def __init__(self, db: SGIMDatabase, cache: TTLCache) -> None:
        self.db = db
        self.cache = cache
        self.services: Dict[str, BaseService] = {}
        self.routes: Dict[str, Callable[[Dict[str, Any]], None]] = {}

    def register(self, service: BaseService) -> None:
        self.services[service.name] = service

    def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        print(f"[ESB] Evento {event_type}: {data}")
        if event_type.startswith("cita."):
            self._notify_for_event("Cita SGIM", f"Evento {event_type}: {json.dumps(data, ensure_ascii=False)}")
        elif event_type == "receta.emitida":
            self._notify_for_event("Receta emitida", f"Se emitió una receta: {json.dumps(data, ensure_ascii=False)}")

    def route(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        service_name = envelope.get("service")
        action = envelope.get("action")
        payload = envelope.get("payload") or {}
        token = envelope.get("token")
        claims: Dict[str, Any] = {}
        if (service_name, action) not in PUBLIC_ACTIONS:
            if not token:
                return error("Token requerido.", 401, "JWT_REQUIRED")
            if self.cache.get(f"revoked:{token}"):
                return error("Token revocado.", 401, "JWT_REVOKED")
            validation = validate_token(token)
            if not validation.get("ok"):
                return validation
            claims = validation["data"]["claims"]
            if (service_name, action) not in ROLE_PERMISSIONS.get(claims.get("rol"), set()):
                return error("Rol sin permiso para esta operación.", 403, "RBAC_FORBIDDEN")
        service = self.services.get(service_name)
        if not service:
            return error(f"Servicio no registrado: {service_name}", 404, "SERVICE_NOT_FOUND")
        response = service.dispatch(action, payload, claims)
        status = response.get("status", 200)
        print(f"[ESB] {service_name}.{action} -> {status}")
        return response

    def _notify_for_event(self, subject: str, message: str) -> None:
        service = self.services.get("notificaciones")
        if isinstance(service, NotificationService):
            service.enqueue("auditoria@sgim.local", "email", subject, message)


class BusTCPHandler(BaseRequestHandler):
    esb: EnterpriseServiceBus

    def handle(self) -> None:
        envelope = receive_frame(self.request)
        if envelope is None:
            return
        response = self.esb.route(envelope)
        send_frame(self.request, response)


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "SGIMGateway/1.0"

    def do_OPTIONS(self) -> None:
        self._send_json(ok())

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[Gateway] {self.address_string()} - {fmt % args}")

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_body()
        payload = {**self._query(parsed), **body}
        route = self._resolve_route(self.command, parsed.path, payload)
        if not route:
            self._send_json(error("Endpoint no encontrado.", 404, "HTTP_ROUTE_NOT_FOUND"))
            return
        service, action, payload = route
        auth_header = self.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip() or payload.pop("token", None)
        response = request_bus(service, action, payload, token=token)
        self._send_json(response)

    def _read_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"raw": raw}

    def _query(self, parsed: Any) -> Dict[str, Any]:
        return {key: values[-1] for key, values in parse_qs(parsed.query).items()}

    def _send_json(self, response: Dict[str, Any]) -> None:
        status = int(response.get("status", 200))
        raw = json.dumps(response, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _resolve_route(self, method: str, path: str, payload: Dict[str, Any]) -> Optional[tuple[str, str, Dict[str, Any]]]:
        if method == "POST" and path == "/auth/login":
            return ("identidad", "login", payload)
        if method == "POST" and path == "/auth/logout":
            return ("identidad", "logout", payload)
        if method == "POST" and path == "/auth/refresh":
            return ("identidad", "refresh", payload)
        if method == "POST" and path == "/auth/validate-token":
            return ("identidad", "validate_token", payload)
        if method == "GET" and path == "/api/citas/disponibilidad":
            return ("citas", "disponibilidad", payload)
        if method == "POST" and path == "/api/citas/crear":
            return ("citas", "crear", payload)
        if method == "GET" and path == "/api/citas/historial":
            return ("citas", "historial", payload)
        if method == "GET" and path == "/api/citas/todas":
            return ("citas", "todas", payload)
        if method == "PUT" and path.startswith("/api/citas/"):
            payload["cita_id"] = path.rsplit("/", 1)[-1]
            return ("citas", "modificar", payload)
        if method == "DELETE" and path.startswith("/api/citas/"):
            payload["cita_id"] = path.rsplit("/", 1)[-1]
            return ("citas", "cancelar", payload)
        if method == "GET" and path.startswith("/api/clinico/paciente/") and path.endswith("/ficha"):
            payload["rut"] = path.split("/")[4]
            return ("clinico", "ficha", payload)
        if method == "POST" and path == "/api/clinico/atencion":
            return ("clinico", "registrar_atencion", payload)
        if method == "POST" and path == "/api/clinico/receta":
            return ("clinico", "receta_emitir", payload)
        if method == "GET" and path.startswith("/api/clinico/receta/"):
            payload["receta_id"] = path.rsplit("/", 1)[-1]
            return ("clinico", "receta_obtener", payload)
        if method == "POST" and path == "/api/clinico/examen":
            return ("clinico", "examen_registrar", payload)
        if method == "GET" and path.startswith("/api/clinico/examen/"):
            payload["resultado_id"] = path.rsplit("/", 1)[-1]
            return ("clinico", "examen_obtener", payload)
        if method == "POST" and path == "/api/notificaciones/enviar":
            return ("notificaciones", "enviar", payload)
        if method == "GET" and path.startswith("/api/notificaciones/estado/"):
            payload["notificacion_id"] = path.rsplit("/", 1)[-1]
            return ("notificaciones", "estado", payload)
        if method == "GET" and path == "/api/reportes/citas":
            return ("reporteria", "citas", payload)
        if method == "GET" and path == "/api/reportes/ocupacion":
            return ("reporteria", "ocupacion", payload)
        if method == "GET" and path == "/api/reportes/inasistencia":
            return ("reporteria", "inasistencia", payload)
        if method == "GET" and path == "/api/reportes/exportar":
            return ("reporteria", "exportar", payload)
        if method == "GET" and path == "/api/admin/usuarios":
            return ("admin", "usuarios", payload)
        if method == "GET" and path == "/api/admin/logs":
            return ("admin", "logs", payload)
        if method == "POST" and path == "/api/admin/replicar":
            return ("admin", "replicar", payload)
        return None

def build_esb() -> EnterpriseServiceBus:
    db = SGIMDatabase()
    cache = TTLCache()
    esb = EnterpriseServiceBus(db, cache)
    for service_cls in (IdentityService, AppointmentService, ClinicalService, NotificationService, ReportingService, AdminService):
        esb.register(service_cls(db, cache, esb))
    return esb


def run_servers(bus_host: str, bus_port: int, gateway_host: str, gateway_port: int) -> None:
    esb = build_esb()
    BusTCPHandler.esb = esb
    bus_server = ThreadingTCPServer((bus_host, bus_port), BusTCPHandler)
    gateway_server = ThreadingHTTPServer((gateway_host, gateway_port), GatewayHandler)
    threading.Thread(target=bus_server.serve_forever, daemon=True).start()
    threading.Thread(target=gateway_server.serve_forever, daemon=True).start()
    print(f"SGIM ESB TCP escuchando en {bus_host}:{bus_port}")
    print(f"SGIM API Gateway escuchando en http://{gateway_host}:{gateway_port}")
    print("Usuarios demo: ana.paciente@sgim.cl / camila.medico@sgim.cl / recepcion@sgim.cl / admin@sgim.cl")
    print("Clave demo para todos: sgim123")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando SGIM...")
        bus_server.shutdown()
        gateway_server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SGIM SOA: ESB + API Gateway + servicios.")
    parser.add_argument("--bus-host", default=DEFAULT_BUS_HOST)
    parser.add_argument("--bus-port", default=DEFAULT_BUS_PORT, type=int)
    parser.add_argument("--gateway-host", default="127.0.0.1")
    parser.add_argument("--gateway-port", default=8000, type=int)
    args = parser.parse_args()
    run_servers(args.bus_host, args.bus_port, args.gateway_host, args.gateway_port)
