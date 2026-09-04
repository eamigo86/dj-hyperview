# Registro de decisiones

Este ledger resume las decisiones vigentes. “Aceptada” significa que guía la implementación; no implica por sí sola que la Task correspondiente ya esté terminada.

| ID | Estado | Decisión | Por qué | Tradeoff / impacto |
|---|---|---|---|---|
| ADR-001 | Aceptada | Distribución `dj-hyperview`; namespace y app `dj_hyperview`. | Evita colisión con el paquete abandonado `django-hyperview`/`django_hv` y usa convenciones PyPI/Python claras. | No es un reemplazo import-compatible; la migración debe ser explícita. |
| ADR-002 | Aceptada | El paquete no contiene pantallas ni XML/HXML runtime. | Infraestructura y contenido tienen ciclos de vida distintos; cada host conserva su UI. | Los ejemplos ejecutables viven sólo en tests/docs y el consumidor debe crear sus plantillas. |
| ADR-003 | Aceptada | Fuentes ordenadas detrás de `TemplateSource` y `TemplateResolver`. | Filesystem, DB y futuras fuentes comparten nombres, misses y precedencia. | Una fuente dinámica necesita identidad de caché correcta o debe quedar uncached. |
| ADR-004 | Aceptada | Engine DjangoTemplates dedicado con `ResolverLoader`; sin monkey-patch global. | Aísla HXML de los templates HTML y hace homogéneos raíz/include/extends. | Mantiene un engine separado y no ofrece snapshot transaccional global entre nombres. |
| ADR-005 | Aceptada | La caché usa sólo APIs públicas Django y almacena raw templates, nunca compilados. | Permite LocMem/Redis/otros backends y evita hacer de la caché una autoridad. | La corrección de tombstones depende del sizing/eviction del backend. |
| ADR-006 | Aceptada | Invalidación generacional compartida y fail-closed. | Evita que una lectura en vuelo repueble la generación vigente con datos stale. | Claims permanentes ocupan espacio; fallos de barrera son visibles incluso con modo normal `bypass`. |
| ADR-007 | Aceptada | Toda invalidación causada por una mutación se programa con `transaction.on_commit()` en el alias de escritura. | No se expone contenido no confirmado y rollback no invalida datos válidos. | Un error del backend después de commit no puede revertir la DB y debe tratarse operativamente. |
| ADR-008 | Aceptada | Servicios validados para publish/rename/delete con revisión optimista y locks. | Centralizan invariantes, multi-DB y conflictos seguros para admin/consumidores. | `save()` directo sigue la semántica Django; se recomienda el servicio cuando se necesita contrato fuerte. |
| ADR-009 | Aceptada | `QuerySet.update()` controlado en v1; hooks automáticos bulk quedan post-MVP. | `bulk_create/update`, expresiones y conflictos requieren semántica adicional para no invalidar de más o dos veces. | Bulk y SQL externo deben llamar servicios o agendar invalidación explícita poscommit. |
| ADR-010 | Aceptada | “Real-time” significa visible en la siguiente petición después del commit. | Es exactamente lo requerido para refrescar una pantalla Hyperview sin ampliar el producto. | No hay WebSockets, push ni cancelación de requests en vuelo. |
| ADR-011 | Aceptada | Matriz Python 3.12–3.14 × Django 5.2/6.1 definida por `tools/test_matrix.py`; Redis sólo por opt-in. | Cubre LTS y versión moderna con comandos portables y sin exigir servicios al flujo local. | Las seis celdas y Redis real aumentan costo y quedan en CI Task 13.4. |
| ADR-012 | Aceptada | TDD estricto, cobertura de ramas ≥95% y Feature Branch Chain con work units ≤400. | Conserva trazabilidad RED/GREEN y revisión/rollback manejables. | Más commits y gates, pero reduce riesgo de cambios opacos. |
| ADR-013 | Aceptada | Docstrings Google-style completas en superficie pública, type hints obligatorios y cero backticks dentro de docstrings. | Contrato documental homogéneo sin duplicar tipos. | Exige auditoría final de APIs heredadas además de lint. |
| ADR-014 | Aceptada | Zensical se prepara en 13.2, PR/main validan en 13.4, el preview 13.5 sólo sube artifact y Pages se despliega en 13.6 después de PyPI. | Evita que documentación pública describa una versión que todavía no existe en PyPI. | Pipeline de seis children con dependencias y permisos explícitos. |
| ADR-015 | Aceptada | El proyecto legacy es sólo referencia de lectura. | El entregable es el paquete reusable, no una modernización del prototipo. | El trabajo histórico realizado allí no cuenta como Tasks 9–10 y no se continúa. |
| ADR-016 | Aceptada | Tasks 9–10 usan `tests/consumer_project/` dentro de [`tests/`](../../tests/) en el [repo del paquete](https://github.com/eamigo86/dj-hyperview). | Produce tests deterministas y distribuibles sin acoplarse al dominio legacy. | Modela instalación/settings/URLs y aceptación HTTP completa con dominios genéricos. |
| ADR-017 | Aceptada | Las 40 filas experimentales legacy no se leen, copian, migran ni convierten en fixtures obligatorios. | Son datos de una prueba, no requisitos del producto. | Sólo sirven como contexto humano de referencia; no participan en tests ni gates. |
| ADR-018 | Aceptada | Hyperview 0.110.0 es el contrato del HXML/HTTP producido; provenance en release npm, tag `v0.110.0` y commit upstream. | El release Python necesita un contrato reproducible del protocolo, no ejecutar o modernizar un cliente móvil. | El schema es enfocado/test-only; no se afirma compatibilidad binaria ni cobertura completa del cliente. |
| ADR-019 | Aceptada | Metadata con rangos compatibles y `uv.lock` exacto versionado; 11.1 se divide en metadata y lock. | Los consumidores conservan resolución flexible mientras desarrollo/CI es reproducible y cada diff queda bajo 400 líneas. | Toda actualización debe mantener sincronizados metadata, lock y guard offline. |
| ADR-020 | Aceptada | Compatibilidad Django se demuestra con conducta pública y deprecations como errores, sin shim runtime preventivo. | Evita código especulativo y detecta APIs obsoletas de forma fail-closed. | Un shim sólo se añade ante un fallo reproducible de comportamiento soportado. |
| ADR-021 | Aceptada | El validador Hyperview test-only exige UTF-8 estricto, rechaza DTD/entities y combina XSD enfocado con integridad `xs:ID`/`xs:IDREF`. | Los flags seguros del parser evitan expansión, pero no bastan para prohibir declaraciones ni referencias colgantes. | Es un gate de compatibilidad bajo tests, no lógica runtime ni schema upstream completo. |

## Decisiones pendientes

| Tema | Qué debe decidirse | Cuándo |
|---|---|---|
| Publicación | Entornos/secrets de PyPI y protección de GitHub Pages | Antes de Task 13.6 |
| Caché en producción | Sizing, aislamiento y política de eviction del backend compartido | En guía operativa de Task 13.3 |

## Relación con el plan

Las decisiones se traducen en criterios verificables en [specification.md](specification.md) y en work units en [tasks.md](tasks.md). Cualquier cambio de alcance debe modificar esos tres documentos juntos.
