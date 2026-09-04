# Estado del proyecto — 2026-09-04

El estado funcional verificado de `dj-hyperview` culmina en [`4d0c502`](https://github.com/eamigo86/dj-hyperview/commit/4d0c502): **Tasks 1–11 completadas y verificadas** exclusivamente dentro del repositorio del paquete. Esta actualización documental parte de ese commit; Tasks 12–13 siguen pendientes.

## Snapshot

| Campo | Valor |
|---|---|
| Repositorio | [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview) |
| Rama funcional verificada | `deps/03-django-61-python-matrix` |
| HEAD funcional | [`4d0c502`](https://github.com/eamigo86/dj-hyperview/commit/4d0c502) — `test(compat): add reproducible support matrix` |
| Rama documental | `docs/status-through-task-11`, parent exacto `4d0c502` |
| Versión declarada | `0.1.0a1` |
| Python declarado | `>=3.12,<3.15` |
| Django declarado | `>=5.2,<6.2` |
| Tasks válidas | 1–11 verificadas; 12–13 pendientes |
| Próxima acción | Task 12.1 — fixtures/schema de Hyperview 0.110.0 |

## Capacidades realmente integradas

| Capacidad | Estado | Evidencia principal |
|---|---|---|
| App y packaging sin UI | **Verificado** | guard de source/wheel, AppConfig, fixtures sólo bajo tests |
| Settings y checks | **Verificado** | defaults, configuración completa y [`checks.py`](../../src/dj_hyperview/checks.py) |
| HTTP/middleware/CSRF | **Verificado** | full/fragment, sync/async, escaping, status/headers y CSRF real |
| Filesystem + resolver/engine | **Verificado** | precedencia, canonicalización, includes/extends y snapshots |
| Seguridad HXML | **Verificado** | XML/XSD, XXE/external access, límites y rutas compiladas |
| Caché | **Verificado con límites documentados** | envelopes, failure policy, fingerprints y generaciones |
| DB/admin opcionales | **Verificado** | modelo/migraciones/source, servicios/admin y multi-DB |
| Publicación poscommit | **Verificado** | save/delete/update/rename, commit/rollback e invalidación |
| Consumidor sintético | **Verificado** | Task 9, cinco perfiles package-owned y fixtures sólo de tests |
| Aceptación HTTP E2E | **Verificado** | Task 10, full/fragment/CSRF/sources/cache/admin/multi-DB |
| Dependencias y compatibilidad | **Verificado** | rangos auditados, lock exacto, Django 5.2/6.1 y Python 3.12–3.14 |
| Contrato Hyperview 0.110.0 | **Pendiente, alcance resuelto** | Task 12 package-owned; cliente móvil real excluido |
| Release/Pages | **Pendiente** | Task 13 |

## Cadena verificada de Tasks 9–10

| Child | Rama | Commit | Parent | Líneas |
|---:|---|---|---|---:|
| 9.1 | `integration/01-consumer-scaffold` | [`36ed0c9`](https://github.com/eamigo86/dj-hyperview/commit/36ed0c9) | `7be11a6` | 143 |
| 9.2 | `integration/02-consumer-filesystem` | [`43f1829`](https://github.com/eamigo86/dj-hyperview/commit/43f1829) | `36ed0c9` | 214 |
| 9.3 | `integration/03-consumer-optional-stack` | [`01d9940`](https://github.com/eamigo86/dj-hyperview/commit/01d9940) | `43f1829` | 373 |
| 10.1 | `integration/04-http-full-fragment` | [`e57344f`](https://github.com/eamigo86/dj-hyperview/commit/e57344f) | `01d9940` | 227 |
| 10.2 | `integration/05-middleware-csrf` | [`1fc99fc`](https://github.com/eamigo86/dj-hyperview/commit/1fc99fc) | `e57344f` | 206 |
| 10.3 | `integration/06-sources-errors` | [`12ba223`](https://github.com/eamigo86/dj-hyperview/commit/12ba223) | `1fc99fc` | 395 |
| 10.4 | `integration/07-admin-postcommit` | [`936f8d4`](https://github.com/eamigo86/dj-hyperview/commit/936f8d4) | `12ba223` | 269 |
| 10.5 | `integration/08-multidb-clean-process` | [`1c17ec1`](https://github.com/eamigo86/dj-hyperview/commit/1c17ec1) | `936f8d4` | 340 |

Cada child es un único commit convencional, reversible y dentro del límite de 400 líneas.

## Cadena verificada de Task 11

| Child | Rama | Commit | Parent | Líneas |
|---:|---|---|---|---:|
| 11.1a | `deps/01-package-metadata-policy` | [`40950bc`](https://github.com/eamigo86/dj-hyperview/commit/40950bc) | `5eb04c4` | 50 |
| 11.1b | `deps/01b-package-exact-lock` | [`e33f567`](https://github.com/eamigo86/dj-hyperview/commit/e33f567) | `40950bc` | 390 |
| 11.2 | `deps/02-django-52-compat` | [`27cbefc`](https://github.com/eamigo86/dj-hyperview/commit/27cbefc) | `e33f567` | 46 |
| 11.3 | `deps/03-django-61-python-matrix` | [`4d0c502`](https://github.com/eamigo86/dj-hyperview/commit/4d0c502) | `27cbefc` | 329 |

La separación 11.1a/11.1b mantiene metadata y lock exacto en dos unidades reversibles dentro del presupuesto.

## Evidencia final de calidad

El cierre independiente de Task 11 obtuvo **PASS: 0 CRITICAL, 0 WARNING, 0 SUGGESTION**.

| Matriz | Suite base | Admin adicional | Cobertura agregada | Cobertura branches |
|---|---:|---:|---:|---:|
| Django 5.2.17 | 727 passed, 36 skipped | 34 passed | 98.15% | 96.70% |
| Django 6.1.1 | 727 passed, 36 skipped | 34 passed | 98.26% | 96.93% |

El focal offline pasó en CPython 3.12.11, 3.13.9 y 3.14.0. También pasaron diez perfiles con `django check`, migraciones sin cambios, Ruff, `uv lock --check` offline y package boundary. La advertencia anterior de coverage quedó resuelta por el runner canónico base+admin, no silenciada.

### Versiones auditadas — 2026-09-04

| Componente | Versión |
|---|---:|
| Django | 5.2.17 / 6.1.1 |
| lxml | 6.1.3 |
| uv-build | 0.12.9 |
| coverage | 7.16.0 |
| pytest | 9.1.1 |
| pytest-cov | 7.1.0 |
| pytest-django | 4.14.0 |
| Ruff | 0.16.6 |

No se ejecutaron builds, Redis real ni la matriz completa de seis celdas. No se añadió shim runtime ni XML/HXML bajo `src/`.

## Límites y riesgos abiertos

| Riesgo/deuda | Impacto | Tratamiento |
|---|---|---|
| Seis celdas y Redis real pendientes | La matriz está definida, pero su ejecución exhaustiva requiere CI/servicio | Task 13.4 |
| CI completa aún no consolidada | La evidencia local independiente no es todavía un gate de GitHub | Tasks 13.3–13.4 |
| Contrato Hyperview 0.110.0 pendiente | El HXML no tiene aún un gate explícito contra el cliente estable | Task 12 |
| Snapshot por nombre, no global | Dos nombres resueltos inicialmente en instantes distintos pueden observar revisiones distintas | Mantener el contrato documentado; no prometer atomicidad global |
| Multi-name invalidation no atómica | Un fallo puede dejar un subconjunto rotado | Error observable y retry del conjunto |
| Tombstones sujetos a culling/eviction | Un backend mal dimensionado debilita la no reutilización | Guía operativa, namespace aislado y capacidad suficiente |
| Claims permanentes | Crecimiento de pequeñas marcas por candidato | Dimensionamiento y rotación del namespace |
| Bulk/SQL sin hooks | Una mutación externa puede dejar caché stale | Servicios o invalidación manual poscommit; hooks post-MVP |
| No hay release ni Pages | El paquete aún no tiene publicación estable automatizada | Task 13 |

El perfil multi-DB contiene un alias SQLite deliberadamente roto para probar fallos redacted; ejecutar migraciones directamente con ese perfil puede emitir un `RuntimeWarning`. El gate canónico de migraciones está limpio y este diagnóstico no es un defecto del paquete.

## Próxima acción

Implementar **Task 12.1 — fixtures/schema de Hyperview 0.110.0** desde el HEAD verificado, con RED → GREEN → REFACTOR, parent exacto, un commit reversible de hasta 400 líneas y sin builds locales.

El plan completo está en [roadmap.md](roadmap.md) y la trazabilidad detallada en [tasks.md](tasks.md).
