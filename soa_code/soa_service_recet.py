from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "recet"

recetas = [
    {
        "id": 1,
        "rut_paciente": "12345678-9",
        "medico_id": 101,
        "fecha": "2026-05-20",
        "medicamentos": ["Losartan 50mg cada 24h"],
        "estado": "activa",
    }
]


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "emitir_receta":
        requeridos = ["rut_paciente", "medico_id", "medicamentos"]
        faltantes = [campo for campo in requeridos if not payload.get(campo)]
        if faltantes:
            return respuesta(False, "Faltan campos obligatorios", {"faltantes": faltantes})
        nueva = {
            "id": len(recetas) + 1,
            "rut_paciente": payload["rut_paciente"],
            "medico_id": payload["medico_id"],
            "fecha": payload.get("fecha", "2026-06-05"),
            "medicamentos": payload["medicamentos"],
            "estado": "activa",
        }
        recetas.append(nueva)
        return respuesta(True, "Receta emitida", nueva)

    if accion == "consultar_recetas":
        rut = payload.get("rut_paciente")
        resultado = [receta for receta in recetas if not rut or receta["rut_paciente"] == rut]
        return respuesta(True, "Recetas encontradas", {"recetas": resultado})

    if accion == "anular_receta":
        receta_id = payload.get("id")
        for receta in recetas:
            if receta["id"] == receta_id:
                receta["estado"] = "anulada"
                return respuesta(True, "Receta anulada", receta)
        return respuesta(False, "Receta no encontrada")

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
