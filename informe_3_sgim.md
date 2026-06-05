# Informe 3 - SGIM Clinica MediCentro

Integrantes: Benjamin Aceituno, Daniel Concha, Vicente Silva  
Curso: Arquitectura de Software - Semestre 1 / 2026

## 8. Implementacion de servicios y clientes SOA

El SGIM se implementa sobre una arquitectura SOA con comunicacion mediante el Bus ESB TCP/IP entregado por el profesor. No se utiliza REST ni HTTP para comunicar clientes y servicios. Cada servicio se registra en el bus con el mensaje `sinit` y un nombre de exactamente cinco caracteres.

Formato general del mensaje:

```text
[5 digitos de longitud][nombre_servicio de 5 caracteres][payload JSON]
```

Servicios implementados:

| Servicio | Archivo servicio | Archivo cliente | Responsabilidad principal |
|---|---|---|---|
| `citas` | `soa_code/soa_service_citas.py` | `soa_code/soa_client_citas.py` | Crear, consultar y anular citas; consultar disponibilidad medica. |
| `ident` | `soa_code/soa_service_ident.py` | `soa_code/soa_client_ident.py` | Autenticacion simulada, roles y permisos RBAC. |
| `clini` | `soa_code/soa_service_clini.py` | `soa_code/soa_client_clini.py` | Consultar y registrar atenciones del historial clinico. |
| `recet` | `soa_code/soa_service_recet.py` | `soa_code/soa_client_recet.py` | Emitir, consultar y anular recetas medicas. |
| `exame` | `soa_code/soa_service_exame.py` | `soa_code/soa_client_exame.py` | Solicitar examenes, registrar resultados y consultar examenes. |
| `notif` | `soa_code/soa_service_notif.py` | `soa_code/soa_client_notif.py` | Encolar notificaciones por email o SMS. |
| `repor` | `soa_code/soa_service_repor.py` | `soa_code/soa_client_repor.py` | Consultar indicadores operacionales desde una replica de lectura. |

Todos los servicios:

- Importan `connect_to_bus`, `send_message` y `receive_message` desde `soa_lib.py`.
- Se registran con `send_message(sock, "sinit", SERVICE_NAME)`.
- Procesan solicitudes JSON.
- Responden con JSON serializado.
- Manejan errores de JSON invalido, acciones no soportadas y fallas internas.
- Usan datos simulados en memoria, sin dependencia de una base de datos real.

Ejemplo de solicitud al servicio de citas:

```python
import json
from soa_lib import connect_to_bus, send_message, receive_message

sock = connect_to_bus()
payload = json.dumps({
    "accion": "crear_cita",
    "rut_paciente": "12345678-9",
    "medico_id": 101,
    "fecha": "2026-06-10",
    "hora": "11:00"
})
send_message(sock, "citas", payload)
data = receive_message(sock)
print(data[5:].decode())
sock.close()
```

## 9. Informe final consolidado

### 9.1 Correcciones al Informe 2

Se corrigio la descripcion de base de datos separando estructura, relaciones y propiedades transaccionales. ACID se presenta como un conjunto de propiedades de las transacciones, no como una estructura de almacenamiento. La tabla `calendario` se define una sola vez y se relaciona con pacientes, medicos y citas.

Se reemplazo la idea de API REST directa por el uso del Bus ESB TCP/IP provisto por el curso. El ESB actua como mediador de mensajes, enruta solicitudes segun el nombre de servicio de cinco caracteres, desacopla clientes y servicios, y permite mensajeria sin que el cliente conozca la ubicacion interna del servicio.

El Servicio Clinico original se dividio en tres servicios con responsabilidades mas acotadas: `clini` para historial clinico, `recet` para recetas y `exame` para examenes. Esta separacion mejora cohesion, mantenibilidad y trazabilidad de datos clinicos.

La replica de reportería se redefine como replica de lectura con sincronizacion continua o near real-time, evitando una perdida de informacion critica asociada a una sincronizacion diaria. Para datos clinicos sensibles, el retraso aceptable debe medirse en segundos o pocos minutos, no en dias.

La cache Redis, si se utiliza en una futura version, debe limitarse a datos no criticos o facilmente invalidables: disponibilidad horaria calculada, sesiones, tokens revocados y resultados de reportes agregados. No debe almacenar diagnosticos, recetas o resultados clinicos como fuente autoritativa.

### 9.2 Arquitectura SOA consolidada

La arquitectura final se organiza en tres capas. La capa cliente contiene Portal Paciente, Portal Medico, Portal Recepcionista y Panel Administrador. La capa de servicios contiene Identidad, Citas, Historial Clinico, Recetas, Examenes, Notificaciones y Reporteria. La capa de datos contiene una base SQL principal y una replica de lectura para reportes.

El Bus ESB TCP/IP recibe mensajes con nombre de servicio y payload JSON. Su responsabilidad es enrutar la solicitud hacia el servicio registrado, recibir la respuesta y devolverla al cliente solicitante. La mensajeria es sincrona para servicios como identidad, citas e historial, y conceptualmente asincrona para notificaciones, donde la respuesta confirma el encolamiento.

### 9.3 Modelo de datos corregido

Entidades principales:

| Entidad | Campos principales | Relaciones |
|---|---|---|
| `tabla_clientes` | `rut`, `nombre`, `fecha_nacimiento`, `email`, `telefono`, `direccion` | Un paciente tiene muchas citas, atenciones, recetas y examenes. |
| `tabla_empleados` | `id_empleado`, `nombre`, `rol`, `especialidad`, `email` | Un medico atiende muchas citas y emite recetas o solicitudes de examen. |
| `calendario` | `id_cita`, `rut_paciente`, `id_medico`, `fecha`, `hora`, `estado` | Pertenece a un paciente y a un medico. |
| `labs_info` | `id_examen`, `rut_paciente`, `id_medico`, `tipo`, `fecha_solicitud`, `estado` | Registra la solicitud del examen. |
| `labs` | `id_resultado`, `id_examen`, `resultado`, `fecha_resultado` | Cada resultado pertenece a una solicitud de examen. |
| `seguridad` | `rut_usuario`, `hash_password`, `rol`, `estado` | Controla acceso de pacientes. |
| `seguridad_empleados` | `id_empleado`, `hash_password`, `rol`, `estado` | Controla acceso de funcionarios. |

Cardinalidades:

- Un paciente puede tener muchas citas; cada cita pertenece a un paciente.
- Un medico puede tener muchas citas; cada cita pertenece a un medico.
- Un paciente puede tener muchas entradas de historial clinico; cada entrada pertenece a un paciente.
- Una solicitud de examen puede tener cero o un resultado asociado.
- Un medico puede emitir muchas recetas; cada receta pertenece a un paciente.

Normalizacion:

- Los datos de pacientes, empleados, agenda, seguridad y examenes se separan para evitar duplicidad.
- Las claves foraneas permiten mantener integridad referencial.
- Los campos clinicos y administrativos no se mezclan en una misma tabla.

### 9.4 Contratos de interfaz

Todas las solicitudes usan JSON con el campo obligatorio `accion`. Todas las respuestas tienen esta forma:

```json
{
  "ok": true,
  "servicio": "citas",
  "mensaje": "Cita creada correctamente",
  "datos": {}
}
```

Contratos principales:

| Servicio | Accion | Campos de entrada | Respuesta esperada |
|---|---|---|---|
| `ident` | `login` | `identificador`, `password` | Token simulado, rol y permisos. |
| `ident` | `validar_permiso` | `identificador`, `permiso` | Booleano `autorizado`. |
| `citas` | `crear_cita` | `rut_paciente`, `medico_id`, `fecha`, `hora` | Cita confirmada. |
| `citas` | `consultar_citas` | `rut_paciente` opcional | Lista de citas. |
| `clini` | `obtener_historial` | `rut_paciente` | Lista de atenciones. |
| `clini` | `registrar_atencion` | `rut_paciente`, `medico_id`, `diagnostico` | Atencion registrada. |
| `recet` | `emitir_receta` | `rut_paciente`, `medico_id`, `medicamentos` | Receta activa. |
| `exame` | `solicitar_examen` | `rut_paciente`, `medico_id`, `tipo` | Solicitud de examen. |
| `notif` | `enviar_notificacion` | `destinatario`, `canal`, `mensaje` | Notificacion encolada. |
| `repor` | `resumen_operacional` | Sin campos adicionales | Indicadores agregados. |

### 9.5 Requisitos funcionales consolidados

- RF1: El sistema debe autenticar usuarios y aplicar permisos por rol.
- RF2: El sistema debe permitir crear, consultar y anular citas medicas.
- RF3: El sistema debe permitir consultar y registrar historial clinico.
- RF4: El sistema debe permitir emitir y consultar recetas medicas.
- RF5: El sistema debe permitir solicitar examenes y consultar resultados.
- RF6: El sistema debe encolar notificaciones de eventos relevantes.
- RF7: El sistema debe entregar reportes operacionales desde una replica de lectura.

### 9.6 Requisitos no funcionales

- Seguridad: Los servicios deben validar identidad y permisos antes de exponer datos clinicos.
- Disponibilidad: Los servicios criticos deben mantenerse operativos durante horario clinico.
- Integridad: Las operaciones de citas, recetas y examenes deben respetar transacciones ACID en la base principal.
- Trazabilidad: Cada operacion clinica debe poder auditarse por usuario, fecha y servicio.
- Interoperabilidad: El payload JSON permite contratos simples sobre el bus TCP/IP.
- Mantenibilidad: La separacion de servicios reduce acoplamiento y permite evolucion independiente.
- Rendimiento: Reporteria usa replica de lectura para no afectar la base transaccional.
- Privacidad: La informacion medica debe limitarse al minimo necesario segun rol.

## 10. Validacion y pruebas

La validacion propuesta considera pruebas unitarias de procesamiento de payload, pruebas de contrato JSON, pruebas de conexion al bus y pruebas funcionales de cada cliente contra su servicio.

Casos minimos:

| Caso | Entrada | Resultado esperado |
|---|---|---|
| Login correcto | `ident/login` con credenciales validas | `ok=true` y token simulado. |
| Login incorrecto | Password invalida | `ok=false`. |
| Crear cita | Datos completos | Cita nueva con estado `confirmada`. |
| Crear cita incompleta | Falta `hora` | Error con lista de campos faltantes. |
| Consultar historial | RUT valido | Lista de atenciones. |
| Enviar notificacion | Canal y mensaje validos | Notificacion con estado `encolada`. |
| Reporte operacional | Accion `resumen_operacional` | Indicadores agregados. |

Ejecucion manual:

1. Iniciar el bus ESB del profesor en `localhost:5000`.
2. Ejecutar el servicio requerido, por ejemplo `python soa_code/soa_service_citas.py`.
3. En otra terminal, ejecutar el cliente correspondiente: `python soa_code/soa_client_citas.py`.
4. Verificar que la respuesta JSON incluya `ok`, `servicio`, `mensaje` y `datos`.

## 11. Conclusiones

El Informe 3 consolida una arquitectura SOA coherente con el bus TCP/IP del curso. La separacion de servicios permite alinear responsabilidades con dominios clinicos concretos: identidad, agenda, historial, recetas, examenes, notificaciones y reporteria.

La correccion mas importante respecto del informe anterior es que el sistema ya no se describe como una arquitectura REST directa. La comunicacion ocurre mediante un ESB que enruta mensajes por sockets, con contratos JSON sobre el payload del bus.

El prototipo no implementa persistencia real, pero deja definidos contratos, responsabilidades y comportamiento base de cada servicio. Para una version productiva, la siguiente etapa debe incorporar base de datos SQL, control transaccional, auditoria, cifrado de credenciales, autorizacion real con JWT y replicacion near real-time para reportería.
