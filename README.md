# ArquiSoft

Proyecto SOA para SGIM - Clinica MediCentro.

## Archivos principales

- `soa_code/soa_lib.py`: libreria base del Bus ESB TCP/IP.
- `soa_code/soa_service_*.py`: servicios SOA registrados con nombres de 5 caracteres.
- `soa_code/soa_client_*.py`: clientes de ejemplo para invocar cada servicio.
- `informe_3_sgim.md`: informe consolidado con correcciones, contratos y validacion.

## Ejecucion

1. Iniciar el Bus ESB del profesor en `localhost:5000`.
2. Ejecutar un servicio, por ejemplo:

```powershell
python soa_code/soa_service_citas.py
```

3. En otra terminal, ejecutar el cliente correspondiente:

```powershell
python soa_code/soa_client_citas.py
```
