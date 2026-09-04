# Roadmap corregido de dj-hyperview

El objetivo es publicar una app Django reutilizable que permita servir UI Hyperview propiedad del proyecto consumidor desde archivos XML o desde un modelo/admin opcional, con caché configurable y coherencia en la siguiente petición después de un commit.

## Alcance del producto

[`dj-hyperview`](https://github.com/eamigo86/dj-hyperview) aporta infraestructura de resolución, render, validación, HTTP, caché, persistencia opcional y publicación. El consumidor aporta todas sus pantallas y decide qué fuentes habilitar.

No se persigue convertir el proyecto legacy en el producto ni extender la librería móvil Hyperview. El legacy es únicamente referencia read-only.

## Estado de Tasks 1–13

| Task | Resultado | Estado | Dependencia principal |
|---:|---|---|---|
| 1 | Frontera, identidad y scaffold del paquete | **Verificado** | — |
| 2 | Configuración tipada y system checks | **Verificado** | 1 |
| 3 | Contrato HTTP, detección, middleware y CSRF | **Verificado** | 2 |
| 4 | Fuentes, resolver, loader y engine dedicado | **Verificado** | 3 |
| 5 | Validación HXML/XML/XSD y aislamiento de renders | **Verificado** | 4 |
| 6 | Caché cruda e invalidación segura frente a carreras | **Verificado** | 5 |
| 7 | Contribución DB y admin opcionales | **Verificado** | 6 |
| 8 | Publicación, mutaciones e invalidación poscommit | **Verificado** | 7 |
| 9 | Consumidor Django sintético en el repo del paquete | **Verificado** | 8 |
| 10 | Aceptación HTTP end-to-end sobre el consumidor sintético | **Verificado** | 9 |
| 11 | Dependencias auditadas, lock exacto y matriz reproducible | **Verificado** | 10 |
| 12 | Contrato HXML/HTTP test-only con Hyperview 0.110.0 | **Verificado** | 11 |
| 13 | Auditoría final, Zensical, CI, release, PyPI y Pages | **Pendiente** | 11 y 12 |

Para el detalle y la trazabilidad de cada subtask, consultar [tasks.md](tasks.md).

## Orden corregido

```text
1 Paquete → 2 Configuración → 3 HTTP → 4 Resolución → 5 Seguridad HXML
    → 6 Caché → 7 DB/admin → 8 Publicación → 9 Consumidor sintético
    → 10 Aceptación HTTP → 11 Compatibilidad
    → 12 Contrato Hyperview 0.110.0 → 13 Release
```

### Implementación package-only de Tasks 9–10

El alcance corregido ya está completado y verificado:

- Task 9 creó `tests/consumer_project/` dentro de [`tests/`](../../tests/) y fixtures exclusivamente dentro de [`tests/fixtures/`](../../tests/fixtures/).
- Task 10 ejerce views/responses/middleware/CSRF/full+fragment, filesystem/DB/cache/admin poscommit, multi-DB y fallos sobre ese consumidor.
- Los ocho children forman una cadena exact-parent, cada uno con un commit reversible de hasta 400 líneas; ver [Tasks 9–10](tasks.md).
- No se copiaron pantallas, datos, modelos de negocio ni rutas del legacy.
- No se promete compatibilidad drop-in con el namespace abandonado `django_hv`.
- Las pruebas ejercitan sólo APIs públicas de `dj_hyperview`.
- El cierre independiente de Task 10 obtuvo PASS sin hallazgos.

### Compatibilidad cerrada en Task 11

- La metadata usa rangos compatibles auditados y `uv.lock` versiona la resolución exacta.
- Django 5.2.17 y 6.1.1 pasan con deprecations como errores y sin shim runtime.
- `tools/test_matrix.py` define Python 3.12–3.14 × Django 5.2/6.1 y Redis opt-in.
- La ejecución real de las seis celdas y Redis queda como gate CI de Task 13.4.
- El cierre independiente obtuvo PASS sin hallazgos.

### Decisión de alcance para Task 12

Task 12 valida dentro del repo Python el protocolo contra [`hyperview` 0.110.0](https://www.npmjs.com/package/hyperview/v/0.110.0), versión estable comprobada el 2026-09-04. No modifica ni moderniza una aplicación mobile. Adoptar Hyperview en una app consumidora real será un proyecto independiente y no un release gate de `dj-hyperview`.

### Contrato Hyperview cerrado en Task 12

- Fixtures, schema enfocado, manifest y validadores viven exclusivamente bajo [`tests/`](../../tests/).
- La provenance fija release npm, tag `v0.110.0` y commit upstream `f715ae5cdf07733a4b846d7744518e42dff40407`.
- El contrato HXML/HTTP exige UTF-8 estricto, rechaza DTD/entities y valida referencias `xs:ID`/`xs:IDREF`.
- No se ejecutó un cliente Node/mobile y no se incorporaron plantillas runtime.
- Detalle mantenible: [compatibilidad Hyperview 0.110.0](../hyperview-0.110.0.md).
- El cierre independiente obtuvo PASS sin CRITICAL ni WARNING; la siguiente acción es **Task 13.1**.

## Hitos

### Hito A — Núcleo reusable

**Completado y verificado:** Tasks 1–8. Incluye las dos fuentes, el admin opcional y el flujo poscommit necesario para ver un cambio en la siguiente petición.

### Hito B — Contrato de consumo

**Completado y verificado:** Tasks 9–10 demuestran instalación desde cero, capacidades opcionales aisladas y HTTP end-to-end sobre el consumidor sintético.

### Hito C — Compatibilidad

**Completado y verificado:** Tasks 11–12 fijan dependencias/matriz y validan el HXML/HTTP producido contra Hyperview 0.110.0 sin ejecutar un cliente móvil.

### Hito D — Publicación

**Pendiente:** Task 13 conserva seis children exact-parent: 13.1 auditoría pública, 13.2 Zensical, 13.3 guías, 13.4 CI/docs sin deploy, 13.5 preview manual y 13.6 PyPI→Pages.

## Reglas de ejecución

- Paquete Python y consumidor sintético dentro de su repositorio: TDD estricto, ciclos RED → GREEN → REFACTOR verificables.
- Cobertura de ramas: al menos 95% en cada gate del paquete.
- Compatibilidad objetivo: Python 3.12–3.14 y Django 5.2/6.1.
- Cambios revisables: Feature Branch Chain, parent exacto, work units de hasta 400 líneas modificadas cuando sea viable.
- Un comportamiento, sus pruebas y sus documentos viajan juntos.
- Commits convencionales, reversibles y sin atribución automática.
- No se ejecutan builds locales; la inspección de wheel y Zensical pertenece a CI.
- La app mínima filesystem-only no requiere DB, admin, Redis ni acceso de red.

## Post-MVP

Los siguientes elementos están deliberadamente excluidos de la primera versión:

- Invalidación automática de `bulk_create()`.
- Invalidación automática de `bulk_update()`.
- Intercepción de SQL crudo.
- WebSockets o push de pantallas al cliente.
- Plantillas XML/HXML incluidas en el wheel.
- Compatibilidad automática con importaciones de `django_hv`.

Hasta que existan hooks bulk, el consumidor debe usar los servicios públicos o programar `invalidate_templates()` con `transaction.on_commit()` después de mutaciones externas exitosas.
