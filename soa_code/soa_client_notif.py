from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "notif"


def llamar(payload):
    sock = connect_to_bus()
    try:
        send_message(sock, SERVICE_NAME, json.dumps(payload))
        data = receive_message(sock)
        if not data:
            print("Sin respuesta del bus")
            return
        print(json.dumps(json.loads(data[5:].decode()), indent=2, ensure_ascii=False))
    finally:
        sock.close()


if __name__ == "__main__":
    llamar(
        {
            "accion": "enviar_notificacion",
            "destinatario": "12345678-9",
            "canal": "email",
            "mensaje": "Su cita medica fue confirmada para el 10-06-2026 a las 11:00.",
        }
    )
