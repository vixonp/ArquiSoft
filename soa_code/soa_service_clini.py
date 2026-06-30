from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "clini"

historiales = {
    "12345678-9": [
        {
            "fecha": "2026-05-20",
            "medico_id": 101,
            "diagnostico": "Hipertension arterial controlada",
            "observaciones": "Continuar monitoreo semanal de presion arterial.",
        }
    ]
}


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "obtener_historial":
        rut = payload.get("rut_paciente")
        if not rut:
            return respuesta(False, "rut_paciente es obligatorio")
        return respuesta(True, "Historial obtenido", {"rut_paciente": rut, "atenciones": historiales.get(rut, [])})

    if accion == "registrar_atencion":
        requeridos = ["rut_paciente", "medico_id", "diagnostico"]
        faltantes = [campo for campo in requeridos if not payload.get(campo)]
        if faltantes:
            return respuesta(False, "Faltan campos obligatorios", {"faltantes": faltantes})
        rut = payload["rut_paciente"]
        atencion = {
            "fecha": payload.get("fecha", "2026-06-05"),
            "medico_id": payload["medico_id"],
            "diagnostico": payload["diagnostico"],
            "observaciones": payload.get("observaciones", ""),
        }
        historiales.setdefault(rut, []).append(atencion)
        return respuesta(True, "Atencion registrada", atencion)

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
                salida = procesar(json.loads(data[5:].decode()))
            except json.JSONDecodeError:
                salida = respuesta(False, "Payload JSON invalido")
            except Exception as exc:
                salida = respuesta(False, f"Error interno: {exc}")
            send_message(sock, SERVICE_NAME, salida)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
