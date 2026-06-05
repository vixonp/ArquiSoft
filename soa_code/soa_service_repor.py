from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "repor"

indicadores = {
    "citas_por_estado": {"confirmada": 128, "anulada": 9, "pendiente": 17},
    "examenes_por_estado": {"solicitado": 34, "disponible": 91},
    "recetas_emitidas_mes": 214,
    "sincronizacion_replica": {
        "modo": "near real-time",
        "ultimo_evento": "2026-06-05T15:45:00-04:00",
        "lag_segundos": 12,
    },
}


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "resumen_operacional":
        return respuesta(True, "Resumen generado desde replica de lectura", indicadores)

    if accion == "citas_por_estado":
        return respuesta(True, "Reporte de citas generado", indicadores["citas_por_estado"])

    if accion == "estado_replica":
        return respuesta(True, "Estado de replica consultado", indicadores["sincronizacion_replica"])

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
