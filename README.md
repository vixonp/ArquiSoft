# SGIM - Sistema de Gestión de Información Médica

Implementación demostrable del SGIM para Clínica MediCentro con arquitectura SOA:

- API Gateway HTTP en `127.0.0.1:8000`.
- ESB interno por TCP/IP sockets en `127.0.0.1:5000`.
- Servicios especializados: Identidad, Citas, Clínico, Notificaciones, Reportería y Administración.
- Persistencia SQL normalizada con SQLite, transacciones ACID, claves foráneas e índices.
- Tokens firmados tipo JWT, RBAC por rol, cache TTL para disponibilidad/sesiones y cola asincrónica de notificaciones.
- Replicación diaria demostrable copiando la BD principal a `sgim_replica.sqlite3`.

## Archivos principales

- `soa_lib.py`: protocolo SOA, seguridad, cache y base de datos.
- `soa_service.py`: ESB, API Gateway y servicios SGIM.
- `soa_client.py`: portales de paciente, médico, recepcionista y administrador.

## Requisitos

Python 3.8 o superior. No requiere instalar paquetes externos para la demo.

## Ejecución

En una terminal:

```bash
python3 soa_service.py
```

En otra terminal:

```bash
python3 soa_client.py demo
```

También puedes ejecutar un portal específico:

```bash
python3 soa_client.py portal paciente
python3 soa_client.py portal medico
python3 soa_client.py portal recepcionista
python3 soa_client.py portal administrador
```

O invocar el ESB directamente:

```bash
python3 soa_client.py call citas disponibilidad \
  --email recepcion@sgim.cl \
  --payload '{"rut_medico":"33333333-3","fecha":"2026-06-08"}'
```

## Usuarios de prueba

Todos usan la clave `sgim123`.

| Rol | Email |
| --- | --- |
| Paciente | `ana.paciente@sgim.cl` |
| Médico | `camila.medico@sgim.cl` |
| Recepcionista | `recepcion@sgim.cl` |
| Administrador | `admin@sgim.cl` |

## Endpoints del API Gateway

### Identidad

- `POST /auth/login`
- `POST /auth/logout`
- `POST /auth/refresh`
- `POST /auth/validate-token`

### Citas

- `GET /api/citas/disponibilidad?rut_medico=33333333-3&fecha=2026-06-08`
- `POST /api/citas/crear`
- `GET /api/citas/historial`
- `PUT /api/citas/{id}`
- `DELETE /api/citas/{id}`

### Clínico

- `GET /api/clinico/paciente/{rut}/ficha`
- `POST /api/clinico/atencion`
- `POST /api/clinico/receta`
- `GET /api/clinico/receta/{id}`
- `POST /api/clinico/examen`
- `GET /api/clinico/examen/{id}`

### Notificaciones

- `POST /api/notificaciones/enviar`
- `GET /api/notificaciones/estado/{id}`

### Reportería y administración

- `GET /api/reportes/citas`
- `GET /api/reportes/ocupacion`
- `GET /api/reportes/inasistencia`
- `GET /api/reportes/exportar?formato=csv`
- `GET /api/admin/usuarios`
- `GET /api/admin/logs`
- `POST /api/admin/replicar`

## Casos de uso demostrados

La demo automática ejecuta:

1. Login del paciente y generación de token.
2. Consulta de disponibilidad con cache TTL.
3. Agenda de cita con validación transaccional ACID.
4. Consulta de historial.
5. Login médico, revisión de agenda, registro de atención y emisión de receta.
6. Encolado asincrónico de notificaciones vía ESB.
7. Login recepcionista y consulta de disponibilidad actualizada.
8. Login administrador, listado de usuarios, reporte de citas y replicación diaria.
9. Consulta de ficha clínica con diagnósticos y recetas.

## Modelo de datos

La BD se crea automáticamente en `sgim.sqlite3` con tablas normalizadas:

- `tabla_clientes`
- `tabla_empleados`
- `tabla_especialidades`
- `tabla_citas`
- `tabla_fichas_clinicas`
- `tabla_recetas`
- `tabla_examen_info`
- `tabla_resultados_examenes`
- `tabla_horarios_medico`
- `tabla_seguridad_clientes`
- `tabla_seguridad_empleados`
- `tabla_notificaciones`
- `tabla_logs_auditoria`

Las relaciones se declaran con claves foráneas y `PRAGMA foreign_keys = ON`. Las citas activas tienen índice único por médico, fecha y hora para evitar doble reserva.

## Notas de alcance

Esta versión es una implementación funcional de demostración para el informe: usa SQLite y cache en memoria para evitar dependencias externas. En producción se reemplazaría SQLite por PostgreSQL/MySQL, cache TTL por Redis real, exportación CSV por PDF/XLSX con librerías especializadas y envío simulado de notificaciones por SMTP/SMS.
