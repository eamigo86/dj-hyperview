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
| ADR-011 | Aceptada | Matriz objetivo Python 3.12–3.14 × Django 5.2/6.1; caché Redis en al menos una celda. | Cubre LTS y versión moderna con runtimes actuales. | Aumenta costo de CI; cambios de compatibilidad deben agruparse y probarse. |
| ADR-012 | Aceptada | TDD estricto, cobertura de ramas ≥95% y Feature Branch Chain con work units ≤400. | Conserva trazabilidad RED/GREEN y revisión/rollback manejables. | Más commits y gates, pero reduce riesgo de cambios opacos. |
| ADR-013 | Aceptada | Docstrings Google-style completas en superficie pública, type hints obligatorios y cero backticks dentro de docstrings. | Contrato documental homogéneo sin duplicar tipos. | Exige auditoría final de APIs heredadas además de lint. |
| ADR-014 | Aceptada | Zensical para docs; PR/main sólo validan; Pages se despliega después de publicar el release. | Evita que documentación pública describa una versión que todavía no existe en PyPI. | Pipeline de release tiene dependencia explícita y permisos por job. |
| ADR-015 | Aceptada | El proyecto legacy es sólo referencia de lectura. | El entregable es el paquete reusable, no una modernización del prototipo. | El trabajo histórico realizado allí no cuenta como Tasks 9–10 y no se continúa. |
| ADR-016 | Aceptada | Tasks 9–10 usan `tests/consumer_project/`, planificado dentro de [`tests/`](../../tests/) en el [repo del paquete](https://github.com/eamigo86/dj-hyperview). | Produce tests deterministas y distribuibles sin acoplarse al dominio legacy. | Debe modelar instalación/settings/URLs y aceptación HTTP completa con dominios genéricos. |
| ADR-017 | Aceptada | Las 40 filas experimentales legacy no se leen, copian, migran ni convierten en fixtures obligatorios. | Son datos de una prueba, no requisitos del producto. | Sólo sirven como contexto humano de referencia; no participan en tests ni gates. |
| ADR-018 | Aceptada | Task 12 valida el paquete contra [`hyperview` 0.110.0](https://www.npmjs.com/package/hyperview/v/0.110.0), estable al 2026-09-04. | El release Python necesita un contrato de protocolo, no modernizar una app móvil ajena. | La modernización de una app consumidora real es otro proyecto y no bloquea el release. |

## Decisiones pendientes

| Tema | Qué debe decidirse | Cuándo |
|---|---|---|
| Publicación | Entornos/secrets de PyPI y protección de GitHub Pages | Antes de Task 13.5 |
| Caché en producción | Sizing, aislamiento y política de eviction del backend compartido | En guía operativa de Task 13.2 |

## Relación con el plan

Las decisiones se traducen en criterios verificables en [specification.md](specification.md) y en work units en [tasks.md](tasks.md). Cualquier cambio de alcance debe modificar esos tres documentos juntos.
