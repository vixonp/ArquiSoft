from soa_lib import connect_to_bus, send_message, receive_message

sock = connect_to_bus()

try:
    while True:
        entrada = input('\nIngrese segundos de espera (o "q" para salir): ')
        
        if entrada.lower() == 'q':
            break
            
        if not entrada.isdigit():
            print("¡Error! Por favor ingrese solo números.")
            continue
        
        # Enviar el número como string al servicio "servi"
        send_message(sock, "servi", entrada)
        
        print(f"Esperando respuesta del servicio ({entrada}s)...")
        data = receive_message(sock)
        
        if data:
            # Mostrar el mensaje (quitando los 5 caracteres del nombre del servicio)
            print(f"Respuesta recibida: {data[5:].decode()}")
finally:
    print('Cerrando conexión')
    sock.close()

