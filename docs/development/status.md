# Estado del proyecto — 2026-09-04

El estado funcional verificado de `dj-hyperview` culmina en [`1c17ec1`](https://github.com/eamigo86/dj-hyperview/commit/1c17ec1): **Tasks 1–10 completadas y verificadas** exclusivamente dentro del repositorio del paquete. Esta actualización documental parte de ese commit; Tasks 11–13 siguen pendientes.

## Snapshot

| Campo | Valor |
|---|---|
| Repositorio | [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview) |
| Rama funcional verificada | `integration/08-multidb-clean-process` |
| HEAD funcional | [`1c17ec1`](https://github.com/eamigo86/dj-hyperview/commit/1c17ec1) — `test(consumer): verify multidatabase isolation` |
| Rama documental | `docs/status-through-task-10`, parent exacto `1c17ec1` |
| Versión declarada | `0.1.0a1` |
| Python declarado | `>=3.12,<3.15` |
| Django declarado | `>=5.2,<6.2` |
| Tasks válidas | 1–10 verificadas; 11–13 pendientes |
| Próxima acción | Task 11.1 — política de dependencias y lock |

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

## Evidencia final de calidad

El cierre independiente de Task 10 obtuvo **PASS: 0 CRITICAL, 0 WARNING, 0 SUGGESTION**.

| Matriz | Suite base | Admin adicional | Cobertura agregada | Cobertura branches |
|---|---:|---:|---:|---:|
| Django 5.2.17 | 716 passed, 34 skipped | 37 passed | 98.15% | 96.70% |
| Django 6.1.1 | 716 passed, 34 skipped | 37 passed | 98.26% | 96.93% |

También pasaron los siete perfiles con `django check`, migraciones canónicas sin cambios, Ruff, lock check, auditoría pública tocada, package boundary y `git diff --check`. No se ejecutaron builds ni se incorporaron XML/HXML dentro de `src/`; los procesos sintéticos no usaron Redis, red ni datos externos.

## Límites y riesgos abiertos

| Riesgo/deuda | Impacto | Tratamiento |
|---|---|---|
| `uv.lock` local e ignorado por [`.gitignore`](../../.gitignore) | Un checkout limpio no fija todavía el entorno de desarrollo/CI | Resolver primero en Task 11.1 |
| Matriz de dependencias aún no congelada | Las versiones declaradas no sustituyen una política reproducible | Completar Tasks 11.1–11.3 |
| CI completa aún no consolidada | La evidencia local independiente no es todavía un gate de GitHub | Task 13.3 |
| Contrato Hyperview 0.110.0 pendiente | El HXML no tiene aún un gate explícito contra el cliente estable | Task 12 |
| Snapshot por nombre, no global | Dos nombres resueltos inicialmente en instantes distintos pueden observar revisiones distintas | Mantener el contrato documentado; no prometer atomicidad global |
| Multi-name invalidation no atómica | Un fallo puede dejar un subconjunto rotado | Error observable y retry del conjunto |
| Tombstones sujetos a culling/eviction | Un backend mal dimensionado debilita la no reutilización | Guía operativa, namespace aislado y capacidad suficiente |
| Claims permanentes | Crecimiento de pequeñas marcas por candidato | Dimensionamiento y rotación del namespace |
| Bulk/SQL sin hooks | Una mutación externa puede dejar caché stale | Servicios o invalidación manual poscommit; hooks post-MVP |
| No hay release ni Pages | El paquete aún no tiene publicación estable automatizada | Task 13 |

El perfil multi-DB contiene un alias SQLite deliberadamente roto para probar fallos redacted; ejecutar migraciones directamente con ese perfil puede emitir un `RuntimeWarning`. El gate canónico de migraciones está limpio y este diagnóstico no es un defecto del paquete.

## Próxima acción

Implementar **Task 11.1 — política de dependencias y lock** desde el HEAD verificado, con RED → GREEN → REFACTOR, parent exacto, un commit reversible de hasta 400 líneas y sin builds locales.

El plan completo está en [roadmap.md](roadmap.md) y la trazabilidad detallada en [tasks.md](tasks.md).
