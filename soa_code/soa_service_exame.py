from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "exame"

examenes = [
    {
        "id": 1,
        "rut_paciente": "12345678-9",
        "tipo": "Hemograma",
        "fecha": "2026-05-22",
        "estado": "disponible",
        "resultado": "Parametros dentro de rango esperado",
    }
]


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def procesar(payload):
    accion = payload.get("accion")

    if accion == "solicitar_examen":
        requeridos = ["rut_paciente", "medico_id", "tipo"]
        faltantes = [campo for campo in requeridos if not payload.get(campo)]
        if faltantes:
            return respuesta(False, "Faltan campos obligatorios", {"faltantes": faltantes})
        nuevo = {
            "id": len(examenes) + 1,
            "rut_paciente": payload["rut_paciente"],
            "medico_id": payload["medico_id"],
            "tipo": payload["tipo"],
            "fecha": payload.get("fecha", "2026-06-05"),
            "estado": "solicitado",
            "resultado": None,
        }
        examenes.append(nuevo)
        return respuesta(True, "Examen solicitado", nuevo)

    if accion == "registrar_resultado":
        examen_id = payload.get("id")
        for examen in examenes:
            if examen["id"] == examen_id:
                examen["estado"] = "disponible"
                examen["resultado"] = payload.get("resultado", "Resultado no informado")
                return respuesta(True, "Resultado registrado", examen)
        return respuesta(False, "Examen no encontrado")

    if accion == "consultar_examenes":
        rut = payload.get("rut_paciente")
        resultado = [examen for examen in examenes if not rut or examen["rut_paciente"] == rut]
        return respuesta(True, "Examenes encontrados", {"examenes": resultado})

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
