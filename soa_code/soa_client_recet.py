from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "recet"


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
            "accion": "emitir_receta",
            "rut_paciente": "12345678-9",
            "medico_id": 101,
            "medicamentos": ["Paracetamol 500mg cada 8h por 3 dias"],
        }
    )
