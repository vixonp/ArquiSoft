from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "citas"

citas = [
    {
        "id": 1,
        "rut_paciente": "12345678-9",
        "medico_id": 101,
        "especialidad": "Medicina general",
        "fecha": "2026-06-10",
        "hora": "10:00",
        "estado": "confirmada",
    }
]

disponibilidad = {
    "101": ["2026-06-10 10:00", "2026-06-10 11:00", "2026-06-11 09:30"],
    "102": ["2026-06-12 15:00", "2026-06-12 16:00"],
}


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "crear_cita":
        requeridos = ["rut_paciente", "medico_id", "fecha", "hora"]
        faltantes = [campo for campo in requeridos if not payload.get(campo)]
        if faltantes:
            return respuesta(False, "Faltan campos obligatorios", {"faltantes": faltantes})

        nueva = {
            "id": len(citas) + 1,
            "rut_paciente": payload["rut_paciente"],
            "medico_id": payload["medico_id"],
            "especialidad": payload.get("especialidad", "Sin especificar"),
            "fecha": payload["fecha"],
            "hora": payload["hora"],
            "estado": "confirmada",
        }
        citas.append(nueva)
        return respuesta(True, "Cita creada correctamente", nueva)

    if accion == "consultar_citas":
        rut = payload.get("rut_paciente")
        resultado = [cita for cita in citas if not rut or cita["rut_paciente"] == rut]
        return respuesta(True, "Citas encontradas", {"citas": resultado})

    if accion == "anular_cita":
        cita_id = payload.get("id")
        for cita in citas:
            if cita["id"] == cita_id:
                cita["estado"] = "anulada"
                return respuesta(True, "Cita anulada", cita)
        return respuesta(False, "Cita no encontrada")

    if accion == "consultar_disponibilidad":
        medico_id = str(payload.get("medico_id", ""))
        return respuesta(True, "Disponibilidad consultada", {"horarios": disponibilidad.get(medico_id, [])})

    return respuesta(False, "Accion no soportada", {"accion": accion})


def main():
    sock = connect_to_bus()
    try:
        print(f"Registrando servicio '{SERVICE_NAME}'...")
        send_message(sock, "sinit", SERVICE_NAME)
        print(f"Confirmacion del bus: {receive_message(sock)!r}")

        while True:
            data = receive_message(sock)
            if not data:
                break
            try:
                payload = json.loads(data[5:].decode())
                salida = procesar(payload)
            except json.JSONDecodeError:
                salida = respuesta(False, "Payload JSON invalido")
            except Exception as exc:
                salida = respuesta(False, f"Error interno: {exc}")
            send_message(sock, SERVICE_NAME, salida)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
