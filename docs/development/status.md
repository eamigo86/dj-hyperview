# Estado del proyecto — 2026-09-05

**MVP package-only cerrado: Tasks 1–13 completadas y verificadas.** El cierre
funcional base permanece en
[077589b813f20947d2ff350a0af42a6ea1d39307](https://github.com/eamigo86/dj-hyperview/commit/077589b).
El primer run alojado `v0.1.0a1` pasó calidad, toda la matriz Django, Redis,
build y smoke test del wheel, pero el build estricto de Zensical detectó un
enlace al README fuera de `docs/`. PyPI y Pages no llegaron a ejecutarse. El
enlace quedó corregido con una regresión permanente en 338d5f0.
No se ejecutaron builds locales.
No se publicó ningún paquete ni se desplegó la documentación.

## Snapshot

| Campo | Valor |
|---|---|
| Repositorio | [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview) |
| Rama verificada | fix/01-release-docs-link |
| HEAD verificado | 338d5f0 — enlaces Markdown publicables por Zensical |
| Versión declarada | 0.1.0a2 |
| Python | 3.12–3.14 |
| Django | 5.2.17 y 6.1.1 |
| Tasks válidas | 1–13 verificadas |
| Próxima acción | Publicar el tag nuevo e inmutable v0.1.0a2 |

## Resultado entregado

| Área | Estado verificado |
|---|---|
| Paquete sin UI bundled | AppConfig, settings, checks y wheel guard; cero XML/HXML runtime |
| Resolución y render | Filesystem, DB/admin opcional, precedencia, includes/extends y engine aislado |
| HTTP y seguridad | Full/fragment, sync/async, CSRF, UTF-8 estricto, XSD, DTD/entities fail-closed |
| Caché y consistencia | Backends Django, Redis opt-in, generaciones e invalidación poscommit |
| Integración | Consumidor sintético, HTTP E2E, multi-DB y fallos redacted |
| Compatibilidad | Python 3.12–3.14, Django 5.2/6.1 y Hyperview 0.110.0 test-only |
| Calidad pública | Docstrings Google completas, type hints y cero backticks |
| Documentación | Zensical 0.0.59, guías y preview manual sin deploy |
| Entrega | CI build-once, artifact inmutable, PyPI OIDC y Pages sólo después de PyPI |

## Evidencia final

| Matriz | Suite base | Admin adicional | Cobertura total | Cobertura branches |
|---|---:|---:|---:|---:|
| Django 5.2.17 | 987 passed | 34 passed | 98.17% | 96.70% |
| Django 6.1.1 | 987 passed | 34 passed | 98.28% | 96.93% |

La revisión final obtuvo **PASS WITH WARNINGS: 0 CRITICAL**. También pasaron
217 gates contractuales acumulados, Ruff, lock offline, system checks,
migraciones, API pública, documentación, package boundary y ausencia de
plantillas runtime. Cada child conserva parent exacto, commit convencional y
hasta 400 líneas de churn.

El warning restante es conservador: PyYAML SafeLoader interpreta YAML 1.1 y el
contrato typed rechaza la variante style-equivalent quoted "on". No permite un
bypass ni cambia el workflow aprobado.

## Configuración externa completada

El **PyPI Trusted Publisher**, el environment `pypi`, GitHub Pages mediante
Actions y el environment `github-pages` ya existen. El tag `v0.1.0a1` permanece
como evidencia inmutable del run fallido y no se reutilizará. La corrección se
publicará con `v0.1.0a2`.

OIDC existe sólo en los jobs PyPI y Pages; únicamente Pages recibe
pages: write. CI, metadata y staging permanecen read-only.

## Límites conocidos

- Real-time significa visible en la siguiente petición posterior al commit; no
  hay WebSockets ni push.
- Snapshots e invalidaciones multi-name no son transacciones globales.
- Tombstones y claims requieren sizing y namespace de caché adecuados.
- Bulk create/update y SQL externo requieren servicios o invalidación manual;
  los hooks automáticos permanecen post-MVP.
- El cliente mobile no se ejecutó ni se extendió; el contrato es HXML/HTTP
  test-only contra Hyperview 0.110.0.
- Build, smoke test y Redis ya pasaron en GitHub Actions; Trusted Publishing y
  Pages siguen pendientes porque el run `v0.1.0a1` se detuvo antes de esos jobs.

## Próximo paso

Seguir [Release and rollback](../release-rollback.md), publicar el tag nuevo
`v0.1.0a2` y observar Trusted Publishing y Pages. El historial por subtask está
en [tasks.md](tasks.md); el trabajo posterior en [roadmap.md](roadmap.md).
