from soa_lib import connect_to_bus, send_message, receive_message
import json

SERVICE_NAME = "ident"

usuarios = {
    "12345678-9": {
        "password": "paciente123",
        "nombre": "Ana Perez",
        "rol": "paciente",
        "permisos": ["ver_citas", "ver_historial_propio", "ver_recetas"],
    },
    "MED101": {
        "password": "medico123",
        "nombre": "Dr. Luis Soto",
        "rol": "medico",
        "permisos": ["ver_historial", "emitir_receta", "solicitar_examen"],
    },
    "ADM001": {
        "password": "admin123",
        "nombre": "Admin SGIM",
        "rol": "administrador",
        "permisos": ["gestionar_usuarios", "ver_reportes", "auditar_accesos"],
    },
}


def respuesta(ok, mensaje, datos=None):
    return json.dumps({"ok": ok, "servicio": SERVICE_NAME, "mensaje": mensaje, "datos": datos or {}})


def token_simulado(identificador, rol):
    return f"jwt-simulado.{identificador}.{rol}"


def procesar(payload):
    accion = payload.get("accion")

    if accion == "login":
        identificador = payload.get("identificador")
        password = payload.get("password")
        usuario = usuarios.get(identificador)
        if not usuario or usuario["password"] != password:
            return respuesta(False, "Credenciales invalidas")
        return respuesta(
            True,
            "Autenticacion exitosa",
            {
                "token": token_simulado(identificador, usuario["rol"]),
                "nombre": usuario["nombre"],
                "rol": usuario["rol"],
                "permisos": usuario["permisos"],
            },
        )

    if accion == "validar_permiso":
        identificador = payload.get("identificador")
        permiso = payload.get("permiso")
        usuario = usuarios.get(identificador)
        autorizado = bool(usuario and permiso in usuario["permisos"])
        return respuesta(True, "Permiso validado", {"autorizado": autorizado})

    if accion == "obtener_rol":
        identificador = payload.get("identificador")
        usuario = usuarios.get(identificador)
        if not usuario:
            return respuesta(False, "Usuario no encontrado")
        return respuesta(True, "Rol encontrado", {"rol": usuario["rol"], "nombre": usuario["nombre"]})

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
