from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "notif"

notificaciones = []


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "enviar_notificacion":
        requeridos = ["destinatario", "canal", "mensaje"]
        faltantes = [campo for campo in requeridos if not payload.get(campo)]
        if faltantes:
            return respuesta(False, "Faltan campos obligatorios", {"faltantes": faltantes})
        nueva = {
            "id": len(notificaciones) + 1,
            "destinatario": payload["destinatario"],
            "canal": payload["canal"],
            "mensaje": payload["mensaje"],
            "estado": "encolada",
        }
        notificaciones.append(nueva)
        return respuesta(True, "Notificacion encolada", nueva)

    if accion == "consultar_estado":
        destinatario = payload.get("destinatario")
        resultado = [
            item for item in notificaciones if not destinatario or item["destinatario"] == destinatario
        ]
        return respuesta(True, "Notificaciones encontradas", {"notificaciones": resultado})

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
