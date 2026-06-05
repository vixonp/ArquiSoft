from soa_lib import connect_to_bus, send_message, receive_message
import time

sock = connect_to_bus()

try:
    # 1. Registro inicial (sinit)
    print("Registrando servicio 'servi'...")
    send_message(sock, "sinit", "servi")
    
    # 2. Procesar respuesta del sinit
    init_data = receive_message(sock)
    print(f"Confirmación de bus recibida: {init_data!r}")
    print("Servicio listo para recibir transacciones.\n")
    
    # 3. Bucle principal de trabajo
    while True:
        data = receive_message(sock)
        if not data:
            print("Conexión cerrada por el bus.")
            break
            
        # Extraer el payload (salta los 5 caracteres del nombre del servicio)
        mensaje = data[5:].decode()
        print(f"Mensaje recibido del cliente: '{mensaje}'")
        try:
            segundos = int(mensaje)
            print(f"Simulando trabajo: durmiendo {segundos}s...")
            
            time.sleep(segundos)
            
            # Responder al cliente a través del bus
            send_message(sock, "servi", "OK")
            print("Respuesta 'OK' enviada.")
            
        except ValueError:
            print(f"Error: '{mensaje}' no es un número válido.")
            send_message(sock, "servi", "Error: Formato incorrecto")

except Exception as e:
    print(f"Error en el servicio: {e}")
finally:
    print('Cerrando socket del servicio')
    sock.close()
