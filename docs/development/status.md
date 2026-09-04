# Estado del proyecto — 2026-09-04

El repositorio entregable de `dj-hyperview` está limpio y publicado en GitHub en `d2f7355`. Ese commit contiene **Tasks 1–8 completadas y verificadas**. No hay avance válido de Tasks 9–10 dentro del paquete; el trabajo histórico ejecutado en el legacy queda excluido.

## Snapshot

| Campo | Valor |
|---|---|
| Repositorio | [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview) |
| Rama publicada | `main` |
| HEAD | [`d2f7355`](https://github.com/eamigo86/dj-hyperview/commit/d2f7355) — `fix(database): bound admin revision tokens` |
| Worktree del paquete | Limpio al tomar este snapshot |
| Versión declarada | `0.1.0a1` |
| Python declarado | `>=3.12,<3.15` |
| Django declarado | `>=5.2,<6.2` |
| Tags/releases | Ninguno observado |
| Tasks válidas | 1–8 verificadas; 9–13 pendientes |

## Capacidades realmente integradas

| Capacidad | Estado | Evidencia principal |
|---|---|---|
| App y packaging sin UI | **Verificado** | guard de source/wheel, AppConfig, fixture sólo en tests |
| Settings y checks | **Verificado** | defaults, configuración completa e identificadores definidos en [`checks.py`](../../src/dj_hyperview/checks.py) |
| HTTP/middleware/CSRF | **Verificado** | response/template response/view, detección sync/async, CSRF estándar |
| Filesystem + resolver | **Verificado** | precedencia, canonicalización, symlink guard, empty hit |
| Engine/includes/extends | **Verificado** | engine dedicado y snapshots aislados por resolver |
| Seguridad HXML | **Verificado** | XML/XSD, XXE/external access, límites y rutas compiladas |
| Caché | **Verificado con límites documentados** | envelope estricto, failure policy, fingerprints y generaciones |
| DB opcional | **Verificado** | modelo, migraciones, source, multi-DB y app lazy |
| Admin opcional | **Verificado** | CRUD, servicios, revisión y conflictos seguros |
| Publicación poscommit | **Verificado** | save/delete/update/rename, commit/rollback e invalidación |
| Consumidor sintético | **Pendiente** | Task 9, work units definidos |
| Aceptación HTTP E2E | **Pendiente** | Task 10, work units definidos |
| Contrato Hyperview 0.110.0 | **Pendiente, alcance resuelto** | Task 12 package-owned; mobile legacy excluido |
| Release/Pages | **Pendiente** | Task 13 |

## Evidencia de calidad consolidada

La verificación independiente final de Task 8 sobre `d2f7355` registró, por versión de Django:

- 652 pruebas de la suite base.
- 34 pruebas adicionales bajo settings del admin opcional.
- Cobertura agregada de 98.15% en Django 5.2.17.
- Cobertura agregada de 98.26% en Django 6.1.1.
- [`admin.py`](../../src/dj_hyperview/contrib/database/admin.py) al 100% en el gate dedicado.
- Ruff, system checks, migraciones, package guard, lock check, diff y auditoría de la superficie pública tocada aprobados.

Estas cifras son evidencia histórica reproducida durante el cierre de Task 8; Task 13 debe convertirlas en gates de CI reproducibles para todos los checkouts.

## Corrección de alcance

### Qué ocurrió

Después de `d2f7355`, se implementaron pruebas y wiring de adopción dentro del backend legacy. Aunque produjeron aprendizajes sobre contexto, rutas y retiro de `django_hv`, modificaron el target equivocado.

### Cómo se contabiliza

- Tasks 9–10 permanecen **pendientes** para el entregable.
- Ningún commit legacy se incluye en la historia o estado del paquete.
- Ese código no debe copiarse mecánicamente.
- Sólo se reutilizan requisitos aprendidos y escenarios sintéticos independientes.
- El legacy permanece únicamente como referencia read-only; no se ejecuta ni participa en gates.

## Riesgos y deuda abiertos

| Riesgo/deuda | Impacto | Tratamiento |
|---|---|---|
| Tasks 9–10 todavía no implementadas | No existe todavía una aceptación E2E mantenible | Ejecutar los work units definidos en [tasks.md](tasks.md) |
| `uv.lock` local e ignorado por [`.gitignore`](../../.gitignore) | Un checkout limpio no reproduce exactamente el entorno | Resolver y versionar política en Tasks 11/13 |
| CI completa aún no consolidada | La evidencia existe, pero no toda está automatizada en GitHub | Task 13.3 |
| Snapshot por nombre, no global | Dos nombres resueltos por primera vez en instantes distintos pueden observar revisiones distintas | Documentar contrato; no prometer atomicidad global |
| Multi-name invalidation no atómica | Un fallo puede dejar un subconjunto rotado | Error observable y retry del conjunto |
| Tombstones sujetos a culling/eviction | Un backend mal dimensionado debilita la prueba de no reutilización | Guía operativa, namespace aislado y capacidad suficiente |
| Claims permanentes | Crecimiento de pequeñas marcas por candidato | Dimensionamiento/rotación del namespace |
| Bulk/SQL sin hooks | Una mutación externa puede dejar caché stale | Servicios o invalidación manual poscommit; hooks post-MVP |
| Auditoría global de docstrings pendiente | APIs no tocadas podrían no cumplir la convención final | Primer gate de Task 13 |
| No hay release ni Pages | El paquete aún no está publicado como versión estable | Task 13.5 |

## Próxima acción

1. Implementar Task 9.1 creando `tests/consumer_project/` dentro de [`tests/`](../../tests/) en el repo del paquete.
2. Completar los tres children de Task 9 con paths portables y fixtures sólo de tests.
3. Ejecutar Task 10 sobre ese consumidor: HTTP, filesystem, DB/admin, cache, commit/rollback y multi-DB.
4. Verificar independientemente cada Task antes de avanzar a dependencias/compatibilidad.

El plan completo está en [roadmap.md](roadmap.md) y el historial de implementación en [tasks.md](tasks.md).
