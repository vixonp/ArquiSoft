"""
Clientes/portales SGIM para consumir el API Gateway y el ESB.

Ejemplos:
    python soa_service.py
    python soa_client.py demo
    python soa_client.py portal paciente
    python soa_client.py call citas disponibilidad --payload '{"rut_medico":"33333333-3","fecha":"2026-06-08"}'
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, Optional

from soa_lib import request_bus


GATEWAY_URL = "http://127.0.0.1:8000"
DEMO_USERS = {
    "paciente": "ana.paciente@sgim.cl",
    "medico": "camila.medico@sgim.cl",
    "recepcionista": "recepcion@sgim.cl",
    "administrador": "admin@sgim.cl",
}


class APIGatewayClient:
    def __init__(self, base_url: str = GATEWAY_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.usuario: Dict[str, Any] = {}

    def login(self, email: str, clave: str = "sgim123") -> Dict[str, Any]:
        response = self.post("/auth/login", {"email": email, "clave": clave}, auth=False)
        if response.get("ok"):
            self.token = response["data"]["token"]
            self.usuario = response["data"]["usuario"]
        return response

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
        return self._request("GET", path + query)

    def post(self, path: str, payload: Optional[Dict[str, Any]] = None, auth: bool = True) -> Dict[str, Any]:
        return self._request("POST", path, payload or {}, auth=auth)

    def put(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("PUT", path, payload or {})

    def delete(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("DELETE", path, payload or {})

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, auth: bool = True) -> Dict[str, Any]:
        raw = None if method == "GET" else json.dumps(payload or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base_url + path, data=raw, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            return {"ok": False, "status": 503, "error": {"code": "GATEWAY_UNAVAILABLE", "message": str(exc)}}


class PortalPaciente:
    def __init__(self, gateway: APIGatewayClient) -> None:
        self.gateway = gateway

    def agendar_cita(self, rut_medico: str, fecha: str, hora: str) -> Dict[str, Any]:
        return self.gateway.post("/api/citas/crear", {"rut_medico": rut_medico, "fecha": fecha, "hora": hora, "sala": "Box 1"})

    def historial(self) -> Dict[str, Any]:
        return self.gateway.get("/api/citas/historial")

    def ficha(self) -> Dict[str, Any]:
        rut = self.gateway.usuario.get("rut")
        return self.gateway.get(f"/api/clinico/paciente/{rut}/ficha")


class PortalMedico:
    def __init__(self, gateway: APIGatewayClient) -> None:
        self.gateway = gateway

    def agenda(self, fecha: str) -> Dict[str, Any]:
        return request_bus("citas", "agenda", {"fecha": fecha}, token=self.gateway.token)

    def registrar_atencion(self, rut_cliente: str, diagnostico: str, observaciones: str = "", cita_id: Optional[int] = None) -> Dict[str, Any]:
        return self.gateway.post(
            "/api/clinico/atencion",
            {"rut_cliente": rut_cliente, "diagnostico": diagnostico, "observaciones": observaciones, "cita_id": cita_id},
        )

    def emitir_receta(self, rut_cliente: str, medicamento: str, dosificacion: str, duracion: str) -> Dict[str, Any]:
        return self.gateway.post(
            "/api/clinico/receta",
            {"rut_cliente": rut_cliente, "medicamento": medicamento, "dosificacion": dosificacion, "duracion_tratamiento": duracion},
        )


class PortalRecepcionista:
    def __init__(self, gateway: APIGatewayClient) -> None:
        self.gateway = gateway

    def disponibilidad(self, rut_medico: str, fecha: str) -> Dict[str, Any]:
        return self.gateway.get("/api/citas/disponibilidad", {"rut_medico": rut_medico, "fecha": fecha})

    def reprogramar(self, cita_id: int, fecha: str, hora: str) -> Dict[str, Any]:
        return self.gateway.put(f"/api/citas/{cita_id}", {"fecha": fecha, "hora": hora})

    def cancelar(self, cita_id: int, motivo: str) -> Dict[str, Any]:
        return self.gateway.delete(f"/api/citas/{cita_id}", {"motivo": motivo})


class PanelAdministrador:
    def __init__(self, gateway: APIGatewayClient) -> None:
        self.gateway = gateway

    def usuarios(self) -> Dict[str, Any]:
        return self.gateway.get("/api/admin/usuarios")

    def reportes(self) -> Dict[str, Any]:
        return self.gateway.get("/api/reportes/citas")

    def replicar(self) -> Dict[str, Any]:
        return self.gateway.post("/api/admin/replicar", {"destino": "sgim_replica.sqlite3"})


def print_response(title: str, response: Dict[str, Any]) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(response, ensure_ascii=False, indent=2))


def next_weekday(target_weekday: int) -> str:
    today = date.today()
    days = (target_weekday - today.weekday()) % 7
    days = days or 7
    return (today + timedelta(days=days)).isoformat()


def run_demo() -> int:
    fecha_med_general = next_weekday(0)

    paciente_client = APIGatewayClient()
    print_response("Login Portal Paciente", paciente_client.login(DEMO_USERS["paciente"]))
    if not paciente_client.token:
        print("El API Gateway no está disponible. Inicia primero: python soa_service.py", file=sys.stderr)
        return 1
    paciente = PortalPaciente(paciente_client)
    disponibilidad = paciente_client.get("/api/citas/disponibilidad", {"rut_medico": "33333333-3", "fecha": fecha_med_general})
    print_response("Paciente consulta disponibilidad", disponibilidad)
    hora = disponibilidad.get("data", {}).get("horarios", ["09:00"])[0]
    print_response("Paciente agenda cita", paciente.agendar_cita("33333333-3", fecha_med_general, hora))
    print_response("Paciente ve historial", paciente.historial())

    medico_client = APIGatewayClient()
    print_response("Login Portal Médico", medico_client.login(DEMO_USERS["medico"]))
    medico = PortalMedico(medico_client)
    agenda = medico.agenda(fecha_med_general)
    print_response("Médico ve agenda", agenda)
    cita_id = None
    if agenda.get("ok") and agenda["data"]["agenda"]:
        citas_confirmadas = [item for item in agenda["data"]["agenda"] if item["estado"] == "confirmada"]
        cita_id = (citas_confirmadas or agenda["data"]["agenda"])[0]["cita_id"]
    print_response("Médico registra atención", medico.registrar_atencion("11111111-1", "Control preventivo sin hallazgos críticos", "Paciente estable.", cita_id))
    print_response("Médico emite receta", medico.emitir_receta("11111111-1", "Paracetamol 500mg", "1 comprimido cada 8 horas", "3 días"))

    recep_client = APIGatewayClient()
    print_response("Login Portal Recepcionista", recep_client.login(DEMO_USERS["recepcionista"]))
    recepcion = PortalRecepcionista(recep_client)
    print_response("Recepción consulta disponibilidad", recepcion.disponibilidad("33333333-3", fecha_med_general))

    admin_client = APIGatewayClient()
    print_response("Login Panel Administrador", admin_client.login(DEMO_USERS["administrador"]))
    admin = PanelAdministrador(admin_client)
    print_response("Admin lista usuarios", admin.usuarios())
    print_response("Admin revisa reporte citas", admin.reportes())
    print_response("Admin ejecuta replicación diaria", admin.replicar())

    print_response("Paciente consulta ficha clínica", paciente.ficha())
    return 0


def run_portal(role: str) -> int:
    client = APIGatewayClient()
    login = client.login(DEMO_USERS[role])
    print_response(f"Login {role}", login)
    if not login.get("ok"):
        return 1
    fecha = next_weekday(0)
    if role == "paciente":
        portal = PortalPaciente(client)
        disponibilidad = client.get("/api/citas/disponibilidad", {"rut_medico": "33333333-3", "fecha": fecha})
        print_response("Disponibilidad", disponibilidad)
        hora = disponibilidad.get("data", {}).get("horarios", ["10:00"])[0]
        print_response("Agendar cita", portal.agendar_cita("33333333-3", fecha, hora))
        print_response("Historial", portal.historial())
    elif role == "medico":
        portal = PortalMedico(client)
        print_response("Agenda", portal.agenda(fecha))
    elif role == "recepcionista":
        portal = PortalRecepcionista(client)
        print_response("Disponibilidad", portal.disponibilidad("33333333-3", fecha))
    else:
        portal = PanelAdministrador(client)
        print_response("Usuarios", portal.usuarios())
        print_response("Reportes", portal.reportes())
    return 0


def run_direct_call(args: argparse.Namespace) -> int:
    payload = json.loads(args.payload or "{}")
    token = args.token
    if args.email:
        client = APIGatewayClient()
        login = client.login(args.email, args.clave)
        if not login.get("ok"):
            print_response("Login fallido", login)
            return 1
        token = client.token
    response = request_bus(args.service, args.action, payload, token=token)
    print_response(f"{args.service}.{args.action}", response)
    return 0 if response.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Portales SGIM para paciente, médico, recepción y administración.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("demo", help="Ejecuta un flujo completo de demostración.")

    portal_parser = subparsers.add_parser("portal", help="Ejecuta operaciones de ejemplo para un portal.")
    portal_parser.add_argument("role", choices=sorted(DEMO_USERS))

    call_parser = subparsers.add_parser("call", help="Invoca directamente un servicio SOA por ESB.")
    call_parser.add_argument("service")
    call_parser.add_argument("action")
    call_parser.add_argument("--payload", default="{}")
    call_parser.add_argument("--token")
    call_parser.add_argument("--email", help="Hace login y usa el token resultante.")
    call_parser.add_argument("--clave", default="sgim123")

    args = parser.parse_args()
    if args.command == "demo":
        return run_demo()
    if args.command == "portal":
        return run_portal(args.role)
    if args.command == "call":
        return run_direct_call(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
