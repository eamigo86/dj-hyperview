# Tareas de implementación

Este documento registra el trabajo verificable del paquete. Tasks 1–13 están completas y verificadas hasta [077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b). El proyecto legacy permanece read-only y no participa en la implementación ni en los gates.

## Convenciones de trazabilidad

- **Verificado:** implementación del paquete aprobada por revisión independiente.
- **Pendiente:** work unit aún no integrado.
- Los commits de Tasks 1–10 se trazan por SHA; la cadena local 9–10 deberá publicarse para que sus enlaces remotos sean resolubles.
- Las ramas de Tasks 1–8 son nombres locales históricos; no se enlazan porque no se confirmó que estén publicadas.
- Cuando Engram no conserva una cifra focal exacta, la evidencia lo declara en lugar de inferirla.

## Estado resumido

| Task | Estado | Entregable |
|---:|---|---|
| 1–8 | **Verificado** | Frontera, configuración, HTTP, resolución, seguridad, caché, DB/admin y publicación |
| 9 | **Verificado** | Consumidor Django sintético propiedad del paquete |
| 10 | **Verificado** | Aceptación HTTP end-to-end sobre ese consumidor |
| 11 | **Verificado** | Dependencias, lock y matriz del paquete |
| 12 | **Verificado** | Contrato HXML/HTTP test-only con Hyperview 0.110.0 |
| 13 | **Verificado** | Documentación Zensical, CI, release, PyPI y Pages |

---

## Task 1 — Frontera y scaffold

### 1.1 Scaffold uv/src y AppConfig

- **Estado:** Verificado.
- **Meta:** crear la distribución `dj-hyperview`, el namespace `dj_hyperview` y una app Django mínima.
- **Resultado:** layout `src`, metadata, AppConfig, pytest-django, cobertura y Ruff configurados.
- **Problema encontrado:** `uv run` intentaba una instalación editable al existir un build backend.
- **Causa raíz:** uv considera el proyecto empaquetable por defecto cuando hay backend de build.
- **Solución:** `[tool.uv] package = false` y carga de [`src`](../../src/) mediante `pythonpath`; builds reservados para CI.
- **Evidencia TDD/verificación:** AppConfig cargado por el registry real; 7 pruebas iniciales y runtime importable al 100%. El número exacto de branches de esta subtask no se consolidó por separado.
- **Trazabilidad:** rama local `slice/01-foundation`; commit [`12c5d61`](https://github.com/eamigo86/dj-hyperview/commit/12c5d61).
- **Componentes/rutas:** [`pyproject.toml`](../../pyproject.toml), [`src/dj_hyperview/apps.py`](../../src/dj_hyperview/apps.py), [`tests/settings.py`](../../tests/settings.py), [`tests/test_app.py`](../../tests/test_app.py).

### 1.2 Guard de contenido y artefacto

- **Estado:** Verificado.
- **Meta:** impedir que el paquete publique pantallas o XML/HXML runtime.
- **Resultado:** guard para proyecto/wheel y fixture de markup confinada a tests.
- **Problema encontrado:** una fixture podía confundirse con contenido runtime y entrar al artefacto.
- **Causa raíz:** sin una frontera ejecutable, la separación entre infraestructura y UI dependía de disciplina manual.
- **Solución:** inspección explícita de source y wheel; markup permitido únicamente bajo [`tests/fixtures`](../../tests/fixtures/).
- **Evidencia TDD/verificación:** casos source/wheel válidos e inválidos; no existe XML/HXML bajo [`src/dj_hyperview`](../../src/dj_hyperview/). El guard suplementario quedó históricamente en 92.16% lineal/75% branches, sin bajar el gate de la librería importable.
- **Trazabilidad:** rama local `slice/01-foundation`; commit [`12c5d61`](https://github.com/eamigo86/dj-hyperview/commit/12c5d61).
- **Componentes/rutas:** [`tools/package_guard.py`](../../tools/package_guard.py), [`tests/test_package_boundary.py`](../../tests/test_package_boundary.py), [`tests/fixtures/screen.xml`](../../tests/fixtures/screen.xml).

### 1.3 Inspección de wheel sólo en CI

- **Estado:** Verificado.
- **Meta:** comprobar el artefacto sin violar la regla de no hacer builds locales.
- **Resultado:** workflow dedicado para construir e inspeccionar el wheel en CI.
- **Problema encontrado:** el lock local no estaba versionado y un checkout limpio no podía reproducir exactamente el entorno.
- **Causa raíz:** `uv.lock` se excluyó del primer child para mantenerlo debajo del presupuesto de revisión.
- **Solución:** aceptar la deuda temporal y asignar la política del lock a Tasks 11/13.
- **Evidencia TDD/verificación:** contrato del workflow y package guard revisados; no se ejecutó build local. No existe aún evidencia de una corrida pública del workflow.
- **Trazabilidad:** rama local `slice/01-foundation`; commit [`12c5d61`](https://github.com/eamigo86/dj-hyperview/commit/12c5d61).
- **Componentes/rutas:** [`.github/workflows/package-boundary.yml`](../../.github/workflows/package-boundary.yml), [`.gitignore`](../../.gitignore), [`tools/package_guard.py`](../../tools/package_guard.py).

---

## Task 2 — Configuración tipada y checks

### 2.0 Contrato `HYPERVIEW`

- **Estado:** Verificado.
- **Meta:** configurar fuentes, caché y validación sin exigir DB/admin/cache en la instalación mínima.
- **Resultado:** dataclasses inmutables, defaults seguros, errores estables y system checks registrados.
- **Problema encontrado:** settings anidados inválidos podían fallar tarde o tocar infraestructura opcional durante startup.
- **Causa raíz:** faltaba una frontera de normalización y validación previa común.
- **Solución:** centralizar configuración y checks; materializar backends/schema de forma lazy.
- **Evidencia TDD/verificación:** defaults, configuración completa, agregación de errores, registry y todos los identificadores definidos en [`checks.py`](../../src/dj_hyperview/checks.py) pasaron en Django 5.2.17/6.1.1.
- **Trazabilidad:** rama local `slice/02-configuration`; commit [`af3f7c3`](https://github.com/eamigo86/dj-hyperview/commit/af3f7c3).
- **Componentes/rutas:** [`src/dj_hyperview/conf.py`](../../src/dj_hyperview/conf.py), [`src/dj_hyperview/checks.py`](../../src/dj_hyperview/checks.py), [`src/dj_hyperview/exceptions.py`](../../src/dj_hyperview/exceptions.py), [`tests/test_conf.py`](../../tests/test_conf.py), [`tests/test_checks.py`](../../tests/test_checks.py), [`tests/test_startup.py`](../../tests/test_startup.py).

### 2.1 Remediación de cobertura

- **Estado:** Verificado.
- **Meta:** elevar la cobertura de ramas al umbral obligatorio sin exclusiones.
- **Resultado:** escenarios observables para directorios, sources, options, mappings, schema y callables inválidos.
- **Problema encontrado:** la primera verificación produjo 90.55%.
- **Causa raíz:** 11 líneas y 8 branches de error carecían de pruebas conductuales.
- **Solución:** parametrizar entradas inválidas y simplificar el control de schema equivalente; no se redujo `fail_under`.
- **Evidencia TDD/verificación:** RED 90.55%; GREEN 31 pruebas, 147 statements/46 branches al 100% en Django 5.2.17/6.1.1.
- **Trazabilidad:** remediación incluida en la rama local `slice/02-configuration`; commit final [`af3f7c3`](https://github.com/eamigo86/dj-hyperview/commit/af3f7c3).
- **Componentes/rutas:** [`tests/test_checks.py`](../../tests/test_checks.py), [`src/dj_hyperview/checks.py`](../../src/dj_hyperview/checks.py), [`pyproject.toml`](../../pyproject.toml).

---

## Task 3 — Integración HTTP

### 3.1 Responses y view

- **Estado:** Verificado.
- **Meta:** ofrecer respuestas Hyperview con semántica Django nativa.
- **Resultado:** `HyperviewResponse`, `HyperviewTemplateResponse` lazy, `HyperviewTemplateView` y media type vendor.
- **Problema encontrado:** no existía un contrato único para content type, charset, status, headers y render diferido.
- **Causa raíz:** el prototipo mezclaba responsabilidades de respuesta y template render.
- **Solución:** wrappers delgados sobre `HttpResponse`, `TemplateResponse` y `TemplateView`.
- **Evidencia TDD/verificación:** RED por módulos/APIs ausentes; GREEN 8 focales/39 acumuladas con raw, lazy, missing template, contexto, status y headers. No se conservó cifra focal de branches independiente.
- **Trazabilidad:** rama local `slice/03-http-responses`; commit [`dff879e`](https://github.com/eamigo86/dj-hyperview/commit/dff879e).
- **Componentes/rutas:** [`src/dj_hyperview/http.py`](../../src/dj_hyperview/http.py), [`src/dj_hyperview/views.py`](../../src/dj_hyperview/views.py), [`tests/test_http.py`](../../tests/test_http.py), [`src/dj_hyperview/__init__.py`](../../src/dj_hyperview/__init__.py).

### 3.2 Detección, middleware sync/async y CSRF

- **Estado:** Verificado.
- **Meta:** detectar requests Hyperview y preservar la seguridad/ciclo nativo en ambos modos de ejecución.
- **Resultado:** marker inmutable, negociación por header/media type, middleware sync/async real y tag CSRF XML-safe.
- **Problema encontrado:** generic XML, wildcard y `q=0` causaban falsos positivos; adapters sync/async podían cambiar semántica.
- **Causa raíz:** detección basada en presencia parcial y clasificación incorrecta del callable downstream.
- **Solución:** parser estricto del contrato, caminos nativos separados y token emitido con APIs Django.
- **Evidencia TDD/verificación:** 52 pruebas y 100% líneas/branches en Django 5.2.17/6.1.1; token ausente/inválido dio 403 y probes sync/async conservaron response.
- **Trazabilidad:** rama local `slice/04-http-middleware`; commit [`b3db2bf`](https://github.com/eamigo86/dj-hyperview/commit/b3db2bf).
- **Componentes/rutas:** [`src/dj_hyperview/middleware.py`](../../src/dj_hyperview/middleware.py), [`src/dj_hyperview/templatetags/dj_hyperview.py`](../../src/dj_hyperview/templatetags/dj_hyperview.py), [`tests/test_middleware.py`](../../tests/test_middleware.py), [`tests/test_csrf.py`](../../tests/test_csrf.py).

---

## Task 4 — Resolución y engine

### 4.1 Sources y resolver

- **Estado:** Verificado.
- **Meta:** resolver nombres seguros con precedencia determinista.
- **Resultado:** contratos `TemplateSource`/`ResolvedTemplate`, `FileSystemSource` y `TemplateResolver` ordenado.
- **Problema encontrado:** traversal/symlink escape y confusión entre contenido vacío y miss.
- **Causa raíz:** paths sin canonicalización compartida y checks de truthiness para contenido.
- **Solución:** nombres POSIX canónicos, raíces resueltas, UTF-8 y `None` como único miss.
- **Evidencia TDD/verificación:** 23 focales/75 acumuladas al 100% líneas/branches en ambas versiones; empty file en primera raíz ganó.
- **Trazabilidad:** rama local `slice/05-template-sources`; commit [`63225e9`](https://github.com/eamigo86/dj-hyperview/commit/63225e9).
- **Componentes/rutas:** [`src/dj_hyperview/sources/base.py`](../../src/dj_hyperview/sources/base.py), [`src/dj_hyperview/sources/filesystem.py`](../../src/dj_hyperview/sources/filesystem.py), [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_resolution.py`](../../tests/test_resolution.py).

### 4.2 Loader y engine dedicado

- **Estado:** Verificado.
- **Meta:** aplicar la misma resolución a raíz/include/extends sin alterar templates HTML globales.
- **Resultado:** `ResolverLoader`, `HyperviewEngine`, selección ordenada y snapshots per-render.
- **Problema encontrado:** sin snapshot, una mutación concurrente podía mezclar revisiones del mismo nombre; cached loaders servirían compilados stale.
- **Causa raíz:** loader global/compilado no representa contenido server-driven mutable.
- **Solución:** engine dedicado sin compiled cache y `ContextVar` que fija cada nombre al primer lookup del render.
- **Evidencia TDD/verificación:** 85 pruebas, 361 statements/76 branches al 100% en Django 5.2.17/6.1.1.
- **Trazabilidad:** rama local `slice/06-template-engine`; commit [`a2ea502`](https://github.com/eamigo86/dj-hyperview/commit/a2ea502).
- **Componentes/rutas:** [`src/dj_hyperview/loaders.py`](../../src/dj_hyperview/loaders.py), [`src/dj_hyperview/engine.py`](../../src/dj_hyperview/engine.py), [`src/dj_hyperview/http.py`](../../src/dj_hyperview/http.py), [`tests/test_engine.py`](../../tests/test_engine.py).

---

## Task 5 — Validación HXML

### 5.0 Pipeline seguro inicial

- **Estado:** Verificado.
- **Meta:** validar source y salida renderizada frente a XML/XSD hostil y límites de recursos.
- **Resultado:** parser cerrado, schema opcional, guard pre-compile y validación posrender.
- **Problema encontrado:** source válido podía producir XML roto después de interpolar contexto; XSD/entidades podían acceder a archivo o red.
- **Causa raíz:** validar una sola fase y usar resolvers XML permisivos.
- **Solución:** pipeline pre/post, sin entidades/red y límites de bytes/profundidad/nodos.
- **Evidencia TDD/verificación:** RED por módulo y casos hostiles ausentes; GREEN 25 focales/110 acumuladas al 100% global. No se conserva cifra exacta de branches sólo de `validation.py` en este child.
- **Trazabilidad:** rama local `slice/07-hxml-validation`; commit [`452d010`](https://github.com/eamigo86/dj-hyperview/commit/452d010).
- **Componentes/rutas:** [`src/dj_hyperview/validation.py`](../../src/dj_hyperview/validation.py), [`src/dj_hyperview/engine.py`](../../src/dj_hyperview/engine.py), [`tests/test_validation.py`](../../tests/test_validation.py).

### 5.1 Remediación de bypasses/scanner

- **Estado:** Verificado.
- **Meta:** hacer universal la validación y evitar falsos positivos léxicos.
- **Resultado:** engine/response `using`/fallback atraviesan validación; scanner contextual y errores surrogate estables.
- **Problema encontrado:** algunos render paths omitían posvalidación; substring search rechazaba DTD-like text en comentarios/CDATA.
- **Causa raíz:** fronteras duplicadas e inspección sin contexto XML/Django.
- **Solución:** una frontera configurada y scanner que distingue declaración activa de texto/comentario.
- **Evidencia TDD/verificación:** 4 bypasses y 5 contextos reprodujeron RED; 126 acumuladas, 503 statements/124 branches al 100%.
- **Trazabilidad:** rama local `slice/08-hxml-validation-hardening`; commit [`b66e8b8`](https://github.com/eamigo86/dj-hyperview/commit/b66e8b8).
- **Componentes/rutas:** [`src/dj_hyperview/validation.py`](../../src/dj_hyperview/validation.py), [`src/dj_hyperview/engine.py`](../../src/dj_hyperview/engine.py), [`src/dj_hyperview/http.py`](../../src/dj_hyperview/http.py), [`tests/test_validation_hardening.py`](../../tests/test_validation_hardening.py).

### 5.2 Remediación de templates compilados

- **Estado:** Verificado.
- **Meta:** aplicar el mismo contrato a `get_template()`/`select_template()`.
- **Resultado:** wrapper transparente con metadata/firma Django, snapshot reentrante y validación única.
- **Problema encontrado:** templates compilados podían omitir schema/límites; processing instructions se clasificaban mal.
- **Causa raíz:** el contrato sólo cerraba el helper render directo.
- **Solución:** envolver todo template público del engine y hacer el scanner PI-aware.
- **Evidencia TDD/verificación:** RED 9 failed/1 passed; GREEN 18 casos nuevos/144 acumuladas con conteo exacto de una validación.
- **Trazabilidad:** rama local `slice/09-hxml-compiled-template-hardening`; commit [`f385cd1`](https://github.com/eamigo86/dj-hyperview/commit/f385cd1).
- **Componentes/rutas:** [`src/dj_hyperview/engine.py`](../../src/dj_hyperview/engine.py), [`src/dj_hyperview/loaders.py`](../../src/dj_hyperview/loaders.py), [`tests/test_compiled_template.py`](../../tests/test_compiled_template.py).

### 5.3 Remediación de aislamiento anidado

- **Estado:** Verificado.
- **Meta:** aislar snapshots entre resolvers anidados/concurrentes.
- **Resultado:** namespace por identidad exacta del resolver, reuse sólo del mismo objeto y limpieza selectiva.
- **Problema encontrado:** inner engine recibía el template homónimo fijado por el outer.
- **Causa raíz:** snapshot keyed por nombre sin ownership de resolver.
- **Solución:** entries inmutables ligadas por identidad `is` y scope cleanup reentrante.
- **Evidencia TDD/verificación:** reproducción exacta RED; 6 casos nuevos, 150 tests y 526 statements/130 branches al 100%; 100 resolvers efímeros sin retención.
- **Trazabilidad:** rama local `slice/10-hxml-nested-render-isolation`; commit [`9ae187d`](https://github.com/eamigo86/dj-hyperview/commit/9ae187d).
- **Componentes/rutas:** [`src/dj_hyperview/loaders.py`](../../src/dj_hyperview/loaders.py), [`src/dj_hyperview/engine.py`](../../src/dj_hyperview/engine.py), [`tests/test_nested_render_isolation.py`](../../tests/test_nested_render_isolation.py).

---

## Task 6 — Caché e invalidación

### 6.1 Contrato de caché cruda

- **Estado:** Verificado.
- **Meta:** distinguir absent/miss/empty y producir claves acotadas.
- **Resultado:** `TemplateCache`, `CacheEntry`, `CACHE_MISS`, TTLs, alias, namespace y envelope JSON versionado.
- **Problema encontrado:** truthiness confundía contenido vacío con miss y keys concatenadas podían colisionar.
- **Causa raíz:** no existía un protocolo tipado ni identidad completa.
- **Solución:** estados explícitos y SHA-256 sobre namespace/source/name/revision.
- **Evidencia TDD/verificación:** RED por módulo ausente; GREEN 17 focales y 100% de 64 statements/10 branches en el módulo inicial.
- **Trazabilidad:** rama local `slice/11-cache-contract`; commit [`1558d16`](https://github.com/eamigo86/dj-hyperview/commit/1558d16).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache.py`](../../tests/test_cache.py).

### 6.1.1 Remediación de envelope

- **Estado:** Verificado.
- **Meta:** tratar payload cacheado como input no confiable ligado al lookup.
- **Resultado:** shape/campos/tipos exactos, duplicate-key rejection e identity binding.
- **Problema encontrado:** bool/float version, campos extra y source/name/revision sustituidos eran aceptados.
- **Causa raíz:** igualdad flexible de Python/JSON y validación parcial.
- **Solución:** `type(version) is int`, field sets exactos y parser con duplicate hook.
- **Evidencia TDD/verificación:** RED 8 failed/2 passed; GREEN 37 casos; 69 cache cases y 83 statements/24 branches al 100%.
- **Trazabilidad:** rama local `slice/12-cache-contract-hardening`; commit [`1809807`](https://github.com/eamigo86/dj-hyperview/commit/1809807).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_hardening.py`](../../tests/test_cache_hardening.py).

### 6.1.2 Remediación fail-closed del backend

- **Estado:** Verificado.
- **Meta:** normalizar todo fallo de cache sin filtrar raw HXML.
- **Resultado:** alias/calls/decode fallan como `SourceUnavailable`, sin exception chain sensible.
- **Problema encontrado:** get/set/init podían propagar excepciones del backend con contenido; misses copiados no estaban identity-bound.
- **Causa raíz:** el adapter confiaba en tipos/mensajes del backend.
- **Solución:** boundary catch/redaction, alias antes de handler y binding de miss.
- **Evidencia TDD/verificación:** 9 fallos RED documentados y suite cache acumulada GREEN. La cifra final exacta de tests de esta remediación no se preservó separadamente.
- **Trazabilidad:** rama local `slice/13-cache-contract-fail-closed`; commit [`ac087de`](https://github.com/eamigo86/dj-hyperview/commit/ac087de).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_fail_closed.py`](../../tests/test_cache_fail_closed.py), [`tests/test_cache.py`](../../tests/test_cache.py).

### 6.1.3 Remediación de alias checks/runtime

- **Estado:** Verificado.
- **Meta:** usar exactamente la misma semántica de alias en checks y runtime.
- **Resultado:** membership y resolución real a `BaseCache`; `_private` configurado es válido.
- **Problema encontrado:** checks y runtime discrepaban frente a prefijos privados y handlers dinámicos.
- **Causa raíz:** dos validadores independientes con criterios distintos.
- **Solución:** helper/política compartida y rechazo de colisiones no configuradas.
- **Evidencia TDD/verificación:** casos de alias válido, inexistente, backend caído y handler dinámico GREEN. No hay conteo focal consolidado en la memoria consultada.
- **Trazabilidad:** rama local `slice/14-cache-alias-consistency`; commit [`3763c39`](https://github.com/eamigo86/dj-hyperview/commit/3763c39).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`src/dj_hyperview/checks.py`](../../src/dj_hyperview/checks.py), [`tests/test_cache_alias_consistency.py`](../../tests/test_cache_alias_consistency.py).

### 6.2 Integración cache/resolver

- **Estado:** Verificado.
- **Meta:** aplicar cache opcional por source con políticas `bypass`/`raise`.
- **Resultado:** raw hit/miss/empty, namespace/TTLs y control de fallos de init/get/decode/set.
- **Problema encontrado:** CACHE vacío podía inicializar backends; un error ordinario no debía volver autoritativa la caché.
- **Causa raíz:** presencia de sección y activación efectiva se trataban igual.
- **Solución:** ausencia/`{}` hace cero accesos; bypass consulta source y raise mantiene error estable.
- **Evidencia TDD/verificación:** escenarios de todas las operaciones y ambas políticas GREEN. No se preservó cifra focal exacta tras todas las remediaciones.
- **Trazabilidad:** rama local `slice/15-cache-resolver-integration`; commit [`fea152f`](https://github.com/eamigo86/dj-hyperview/commit/fea152f).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_resolver.py`](../../tests/test_cache_resolver.py).

### 6.2.1 Remediación de identidad efectiva

- **Estado:** Verificado.
- **Meta:** aislar cache por configuración realmente observable sin exponer secretos.
- **Resultado:** fingerprint de posición/backend/opciones efectivas; source no representable queda uncached.
- **Problema encontrado:** opciones declaradas omitían raíces resueltas/orden efectivo.
- **Causa raíz:** identidad basada en config parcial, no en construcción del source.
- **Solución:** normalización tipada y hash; filesystem incorpora roots efectivas.
- **Evidencia TDD/verificación:** roots/order/options distintas aisladas y secrets ausentes de keys. Conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/16-cache-source-identity`; commit [`ed53a70`](https://github.com/eamigo86/dj-hyperview/commit/ed53a70).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_cache_source_identity.py`](../../tests/test_cache_source_identity.py).

### 6.2.2 Remediación de paths/mappings

- **Estado:** Verificado.
- **Meta:** evitar fingerprints equivalentes para configuraciones no equivalentes.
- **Resultado:** dialecto POSIX/Windows, numeric Mapping-equal y orden total del par normalizado.
- **Problema encontrado:** paths de dialectos distintos stringify igual; sort parcial era inestable.
- **Causa raíz:** canonicalización sin tag de tipo/dialecto y sin valor en el criterio total.
- **Solución:** encoding tipado explícito y orden por representación completa.
- **Evidencia TDD/verificación:** regresiones PureWindowsPath/POSIX y mappings equivalentes GREEN. Cifra focal exacta no conservada.
- **Trazabilidad:** rama local `slice/17-cache-path-identity`; commit [`121392c`](https://github.com/eamigo86/dj-hyperview/commit/121392c).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_cache_source_identity.py`](../../tests/test_cache_source_identity.py).

### 6.2.3 Remediación de buffers

- **Estado:** Verificado.
- **Meta:** cerrar valores con identidad mutable/ambigua.
- **Resultado:** `memoryview` siempre uncacheable; sólo tipos representables permanecen.
- **Problema encontrado:** un memoryview read-only/hashable podía parecer estable sin representar toda su semántica.
- **Causa raíz:** hashability no equivale a identidad de configuración completa.
- **Solución:** exclusión fail-closed del tipo completo.
- **Evidencia TDD/verificación:** variantes de memoryview y bytes/bytearray trianguladas; 120 líneas de test en el commit, sin conteo focal preservado.
- **Trazabilidad:** rama local `slice/18-cache-buffer-identity`; commit [`bcc0b20`](https://github.com/eamigo86/dj-hyperview/commit/bcc0b20).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_cache_buffer_identity.py`](../../tests/test_cache_buffer_identity.py).

### 6.2.4 Remediación de dominio cerrado

- **Estado:** Verificado.
- **Meta:** garantizar que todo fingerprint admitido represente semántica completa.
- **Resultado:** whitelist de built-ins exactos, paths estándar y callables importables verificados.
- **Problema encontrado:** containers/protocolos/subclasses genéricos podían omitir estado observable.
- **Causa raíz:** aceptación por interfaz demasiado amplia.
- **Solución:** dominio cerrado; lo desconocido deshabilita caché sólo para esa source.
- **Evidencia TDD/verificación:** unknown buffers/containers/subclasses/numerics rechazados y valores admitidos estables; conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/19-cache-fingerprint-domain`; commit [`2670301`](https://github.com/eamigo86/dj-hyperview/commit/2670301).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_cache_fingerprint_domain.py`](../../tests/test_cache_fingerprint_domain.py).

### 6.3 Invalidación generacional

- **Estado:** Verificado.
- **Meta:** volver invisibles hits/misses anteriores sin permitir repoblación stale.
- **Resultado:** token namespaced por nombre, API pública `invalidate_templates()` y recheck tras raw write.
- **Problema encontrado:** delete directo de keys conocidas no cubría sources/revisiones ni readers en vuelo.
- **Causa raíz:** invalidación enumerativa sin barrera compartida.
- **Solución:** generación vigente incluida en todas las raw keys.
- **Evidencia TDD/verificación:** carreras content/miss y rotación por nombre GREEN; número focal exacto no conservado por separado.
- **Trazabilidad:** rama local `slice/20-cache-generation-race`; commit [`4a8aa35`](https://github.com/eamigo86/dj-hyperview/commit/4a8aa35).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_cache_generation.py`](../../tests/test_cache_generation.py).

### 6.3.1 Remediación shared fail-closed

- **Estado:** Verificado.
- **Meta:** probar la barrera entre workers y hacer todo fallo observable.
- **Resultado:** claims compartidos, candidatos ABA rechazados y resultado exacto de add/delete validado.
- **Problema encontrado:** locks locales no protegen procesos; `False`/`None` ambiguos y `bypass` ocultaban fallos.
- **Causa raíz:** se confundía disponibilidad de cache con confirmación de consistencia.
- **Solución:** protocolo de claim compartido independiente de failure mode ordinario.
- **Evidencia TDD/verificación:** colisiones/fallos backend/bypass triangulados; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/21-cache-generation-fail-closed`; commit [`40fc798`](https://github.com/eamigo86/dj-hyperview/commit/40fc798).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_generation_fail_closed.py`](../../tests/test_cache_generation_fail_closed.py).

### 6.3.2 Remediación de publicación en vuelo

- **Estado:** Verificado.
- **Meta:** impedir que un claim expire mientras un reader publica.
- **Resultado:** current no expirable, successor derivado y confirmación/cleanup de cada raw write.
- **Problema encontrado:** expiración durante I/O reabría token obsoleto.
- **Causa raíz:** lifecycle del claim ligado a TTL temporal, no al reemplazo confirmado.
- **Solución:** sólo replacement confirmado mueve la generación; successor domain-separated.
- **Evidencia TDD/verificación:** slow readers, misses y reemplazos concurrentes GREEN; 246 líneas de test añadidas, sin conteo focal consolidado.
- **Trazabilidad:** rama local `slice/22-cache-inflight-publication`; commit [`59b3b6c`](https://github.com/eamigo86/dj-hyperview/commit/59b3b6c).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_inflight_publication.py`](../../tests/test_cache_inflight_publication.py).

### 6.3.3 Remediación de root claims

- **Estado:** Verificado.
- **Meta:** demostrar ownership/kind de cada token inicial.
- **Resultado:** metadata tipada secret-safe, root tombstones y confirmación exacta ante `add(False)`.
- **Problema encontrado:** una colisión no indicaba si el claim pertenecía al nombre/tipo esperado.
- **Causa raíz:** value de claim sin identidad verificable.
- **Solución:** payload hash ligado a name/generation/kind.
- **Evidencia TDD/verificación:** root collision y metadata hostil GREEN; cifra focal exacta no conservada.
- **Trazabilidad:** rama local `slice/23-cache-root-claims`; commit [`5e92be7`](https://github.com/eamigo86/dj-hyperview/commit/5e92be7).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_root_claims.py`](../../tests/test_cache_root_claims.py).

### 6.3.4 Remediación de lifecycle

- **Estado:** Verificado.
- **Meta:** preservar current correcto bajo resultados inciertos/stale invalidators.
- **Resultado:** kind ligado al token y retiro sólo del successor realmente desplazado.
- **Problema encontrado:** podía retirarse el claim vigente o un successor distinto.
- **Causa raíz:** cleanup basado en intención del invalidator, no en estado observado.
- **Solución:** gate replacement+deletion confirmado y best-effort dirigido al valor desplazado.
- **Evidencia TDD/verificación:** incertidumbre, stale rotation y cleanup ambiguo GREEN; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/24-cache-claim-lifecycle`; commit [`f3bd37b`](https://github.com/eamigo86/dj-hyperview/commit/f3bd37b).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_claim_lifecycle.py`](../../tests/test_cache_claim_lifecycle.py).

### 6.3.5 Remediación de tombstones permanentes

- **Estado:** Verificado con límite operativo documentado.
- **Meta:** cerrar ABA aun después de expirar raw TTLs.
- **Resultado:** claims root/successor exitosos no expiran; sólo delete exacto `True` prueba cleanup.
- **Problema encontrado:** cualquier TTL finito permitía repetir predecessor+entropy tras expirar successor.
- **Causa raíz:** tiempo no demuestra que no exista una publicación lenta.
- **Solución:** tombstones permanentes por vida de namespace/backend.
- **Evidencia TDD/verificación:** 7 tests RED en parent y GREEN en child; 411 tests, cobertura global 97.91% en Django 5.2.17/6.1.1.
- **Trazabilidad:** rama local `slice/25-cache-permanent-claims`; commit [`f1e743e`](https://github.com/eamigo86/dj-hyperview/commit/f1e743e).
- **Componentes/rutas:** [`src/dj_hyperview/cache.py`](../../src/dj_hyperview/cache.py), [`tests/test_cache_permanent_claims.py`](../../tests/test_cache_permanent_claims.py), [`README.md`](https://github.com/eamigo86/dj-hyperview/blob/main/README.md).

---

## Task 7 — DB y admin opcionales

### 7.1 App, modelo y migración

- **Estado:** Verificado.
- **Meta:** almacenar templates en DB sin imponer ORM al modo base.
- **Resultado:** contrib app lazy, `HyperviewTemplate`, manager y migración reversible.
- **Problema encontrado:** invariantes sólo en model clean no quedaban serializadas/reutilizables.
- **Causa raíz:** validación acoplada al modelo runtime.
- **Solución:** campos/constraint y posterior extracción de validators migration-safe.
- **Evidencia TDD/verificación:** startup base sin model import, schema/migration y modelo en settings aislados GREEN. Conteo focal exacto no preservado.
- **Trazabilidad:** rama local `slice/26-database-contrib-model`; commit [`df6d116`](https://github.com/eamigo86/dj-hyperview/commit/df6d116).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/apps.py`](../../src/dj_hyperview/contrib/database/apps.py), [`src/dj_hyperview/contrib/database/models.py`](../../src/dj_hyperview/contrib/database/models.py), [`src/dj_hyperview/contrib/database/migrations/0001_initial.py`](../../src/dj_hyperview/contrib/database/migrations/0001_initial.py), [`tests/test_database_app.py`](../../tests/test_database_app.py).

### 7.1.1 Remediación de field validation

- **Estado:** Verificado.
- **Meta:** aplicar nombre/source/revisión en forms, fields y migrations.
- **Resultado:** validators compartidos, `MinValueValidator(1)` y migración `0002`.
- **Problema encontrado:** caminos parciales podían omitir invariantes de nombre/revisión.
- **Causa raíz:** reglas no estaban declaradas en cada field.
- **Solución:** validators deconstructibles y constraint DB.
- **Evidencia TDD/verificación:** field/full_clean/migration states GREEN en ambas versiones; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/27-database-model-validation`; commit [`9bb80f5`](https://github.com/eamigo86/dj-hyperview/commit/9bb80f5).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/validators.py`](../../src/dj_hyperview/contrib/database/validators.py), [`src/dj_hyperview/contrib/database/models.py`](../../src/dj_hyperview/contrib/database/models.py), [`src/dj_hyperview/contrib/database/migrations/0002_field_validators.py`](../../src/dj_hyperview/contrib/database/migrations/0002_field_validators.py).

### 7.1.2 Remediación de redacción

- **Estado:** Verificado.
- **Meta:** evitar que errores públicos filtren nombres/paths sensibles.
- **Resultado:** validators/filesystem errors sin cadena inspeccionable, preservando categorías públicas.
- **Problema encontrado:** exception chaining de pathlib/validator exponía la entrada rechazada.
- **Causa raíz:** `raise ... from error` mantenía contexto interno en frontera pública.
- **Solución:** traducción redactada sin cause sensible.
- **Evidencia TDD/verificación:** probes inspeccionaron `__cause__` y mensajes para names/paths hostiles; cifra focal exacta no conservada.
- **Trazabilidad:** rama local `slice/28-template-name-redaction`; commit [`398265b`](https://github.com/eamigo86/dj-hyperview/commit/398265b).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/validators.py`](../../src/dj_hyperview/contrib/database/validators.py), [`src/dj_hyperview/sources/filesystem.py`](../../src/dj_hyperview/sources/filesystem.py), [`tests/test_resolution.py`](../../tests/test_resolution.py).

### 7.2 DatabaseSource

- **Estado:** Verificado.
- **Meta:** resolver filas activas con el mismo protocolo que filesystem.
- **Resultado:** model lookup lazy, default manager/router, exact name, empty hit y metadata/revisión.
- **Problema encontrado:** app/alias/DB unavailable debían diferenciarse de miss sin filtrar SQL/nombre.
- **Causa raíz:** el acceso ORM no estaba encapsulado como source.
- **Solución:** traducción a `SourceUnavailable`; inactive/absent como `None` únicamente.
- **Evidencia TDD/verificación:** active/inactive/absent/empty, precedence/fallthrough y fallos de infraestructura GREEN. Conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/29-database-source`; commit [`b25313e`](https://github.com/eamigo86/dj-hyperview/commit/b25313e).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/sources.py`](../../src/dj_hyperview/contrib/database/sources.py), [`tests/test_database_source.py`](../../tests/test_database_source.py).

### 7.2.1 Remediación multi-DB/cache

- **Estado:** Verificado.
- **Meta:** impedir cache cross-tenant/alias.
- **Resultado:** router-selected source queda uncached; alias explícito validado tiene identidad separada.
- **Problema encontrado:** routing dinámico podía compartir una identidad fija.
- **Causa raíz:** el fingerprint no puede representar el resultado futuro de un router por request.
- **Solución:** opt-out genérico del source dinámico y alias config explícita para caching.
- **Evidencia TDD/verificación:** dos aliases/router y cache isolation GREEN; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/30-database-source-isolation`; commit [`7df4e46`](https://github.com/eamigo86/dj-hyperview/commit/7df4e46).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/_config.py`](../../src/dj_hyperview/contrib/database/_config.py), [`src/dj_hyperview/contrib/database/sources.py`](../../src/dj_hyperview/contrib/database/sources.py), [`tests/test_database_source_isolation.py`](../../tests/test_database_source_isolation.py).

### 7.2.2 Remediación del marker opt-out

- **Estado:** Verificado.
- **Meta:** fallar cerrado ante descriptors/slots ilegibles.
- **Resultado:** detección estática + lookup protegido; un marker explícito sólo se acepta con bool exacto.
- **Problema encontrado:** unreadable descriptor podía parecer marker ausente y habilitar caché.
- **Causa raíz:** `getattr` con default no distingue todos los errores de descriptor.
- **Solución:** `getattr_static` y acceso normal controlado.
- **Evidencia TDD/verificación:** absent/True/False/descriptors/exceptions GREEN; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/31-source-cache-optout-hardening`; commit [`72f5a72`](https://github.com/eamigo86/dj-hyperview/commit/72f5a72).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_source_cache_optout.py`](../../tests/test_source_cache_optout.py).

### 7.2.3 Remediación de proxies dinámicos

- **Estado:** Verificado.
- **Meta:** honrar markers sintetizados/forwarded sin evaluación permisiva.
- **Resultado:** presencia estática combinada con un único lookup normal protegido.
- **Problema encontrado:** proxies dinámicos escapaban una inspección sólo estática.
- **Causa raíz:** `__getattr__`/forwarding no existe en `getattr_static`.
- **Solución:** combinar ambas vistas y aceptar únicamente `True` bool exacto.
- **Evidencia TDD/verificación:** proxies forwarded/synthetic/errors GREEN; el reporte histórico la marca PASS WITH WARNINGS sin cifra focal exacta.
- **Trazabilidad:** rama local `slice/32-source-cache-proxy-optout`; commit [`7885314`](https://github.com/eamigo86/dj-hyperview/commit/7885314).
- **Componentes/rutas:** [`src/dj_hyperview/resolver.py`](../../src/dj_hyperview/resolver.py), [`tests/test_source_cache_proxy_optout.py`](../../tests/test_source_cache_proxy_optout.py).

### 7.3 Admin CRUD estándar

- **Estado:** Verificado.
- **Meta:** ofrecer administración opcional sin importarla en instalaciones base/DB-only.
- **Resultado:** ModelForm/ModelAdmin por autodiscovery; revision/timestamps read-only; permisos, CSRF y validación.
- **Problema encontrado:** ninguno específico dentro de esta subtask; la invalidación/revisión avanzada estaba conscientemente reservada a Task 8.
- **Causa raíz:** No aplica.
- **Solución:** integración estándar de Django y settings dedicados de prueba; no hooks prematuros.
- **Evidencia TDD/verificación:** parent RED 15 failed/1 passed; 16 tests oficiales + 6 adversariales por versión; suite 562 passed/16 skipped y 95.97% global.
- **Trazabilidad:** rama local `slice/33-database-admin`; commit [`fbab02e`](https://github.com/eamigo86/dj-hyperview/commit/fbab02e).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/admin.py`](../../src/dj_hyperview/contrib/database/admin.py), [`tests/settings_database_admin.py`](../../tests/settings_database_admin.py), [`tests/test_database_admin.py`](../../tests/test_database_admin.py), [`tests/urls_admin.py`](../../tests/urls_admin.py).

---

## Task 8 — Publicación poscommit

### 8.1 Scheduler transaccional

- **Estado:** Verificado.
- **Meta:** invalidar sólo después del commit sobre el alias de mutación.
- **Resultado:** callback canónico, ordenado e inmutable con `robust=False`.
- **Problema encontrado:** invalidar antes del commit expone una vista que puede revertirse.
- **Causa raíz:** cache y DB no comparten transacción.
- **Solución:** única frontera privada `transaction.on_commit(using=alias)`.
- **Evidencia TDD/verificación:** commit/rollback/savepoint/order/dedup/alias GREEN; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/34-transactional-invalidation`; commit [`adcdf60`](https://github.com/eamigo86/dj-hyperview/commit/adcdf60).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/_invalidation.py`](../../src/dj_hyperview/contrib/database/_invalidation.py), [`tests/test_database_transactions.py`](../../tests/test_database_transactions.py).

### 8.2 Señales de modelo

- **Estado:** Verificado.
- **Meta:** cubrir create/save/rename/delete directos.
- **Resultado:** pre/post signals capturan identidad y registran invalidación; conexión idempotente.
- **Problema encontrado:** después de rename, `instance.name` ya no contiene el nombre viejo.
- **Causa raíz:** post-save sólo observa estado nuevo.
- **Solución:** snapshot persistido en pre-save/pre-delete y unión viejo/nuevo en post.
- **Evidencia TDD/verificación:** create/update/rename/delete, alias y rollback GREEN; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/35-database-model-signals`; commit [`af45265`](https://github.com/eamigo86/dj-hyperview/commit/af45265).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/signals.py`](../../src/dj_hyperview/contrib/database/signals.py), [`src/dj_hyperview/contrib/database/apps.py`](../../src/dj_hyperview/contrib/database/apps.py), [`tests/test_database_signals.py`](../../tests/test_database_signals.py).

### 8.2.1 Remediación de nombres persistidos

- **Estado:** Verificado.
- **Meta:** resolver detached/explicit-PK/force-update/delete edge cases.
- **Resultado:** lectura de identidad anterior por PK y alias exactos; fallback sólo sin fila.
- **Problema encontrado:** señales podían invalidar únicamente el nombre de la instancia, no el almacenado.
- **Causa raíz:** una instancia detached no demuestra el estado previo de DB.
- **Solución:** query autoritativa antes de mutation.
- **Evidencia TDD/verificación:** ordinary/forced updates, explicit PK y no-row delete GREEN; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/35a-model-signal-edge-cases`; commit [`5cab0db`](https://github.com/eamigo86/dj-hyperview/commit/5cab0db).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/signals.py`](../../src/dj_hyperview/contrib/database/signals.py), [`tests/test_database_signals.py`](../../tests/test_database_signals.py).

### 8.3 `QuerySet.update()` controlado

- **Estado:** Verificado.
- **Meta:** cubrir el camino ORM que no emite signals.
- **Resultado:** PK/names capturados, update ejecutado y old/new names invalidados una vez.
- **Problema encontrado:** expresiones y renames sólo se conocen con valores realmente persistidos.
- **Causa raíz:** `QuerySet.update()` opera directamente en SQL.
- **Solución:** manager/queryset propio con captura/lectura posterior dentro de una pipeline.
- **Evidencia TDD/verificación:** literal/expression/rename/row count/rollback/multi-DB GREEN; conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/36-database-queryset-update`; commit [`c790ee4`](https://github.com/eamigo86/dj-hyperview/commit/c790ee4).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py), [`src/dj_hyperview/contrib/database/models.py`](../../src/dj_hyperview/contrib/database/models.py), [`tests/test_database_queryset_update.py`](../../tests/test_database_queryset_update.py).

### 8.3.1 Remediación routing/PK

- **Estado:** Verificado.
- **Meta:** mutar exactamente las filas capturadas en el write alias.
- **Resultado:** conjunto autoritativo de PK antes del cambio.
- **Problema encontrado:** re-evaluar filtros o usar read routing podía seleccionar otro conjunto.
- **Causa raíz:** queryset mutable y routers con decisiones distintas.
- **Solución:** fijar write alias y PKs dentro del atomic boundary.
- **Evidencia TDD/verificación:** routed/captured-PK edge cases GREEN; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/36a-queryset-update-boundary`; commit [`6624fb1`](https://github.com/eamigo86/dj-hyperview/commit/6624fb1).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py), [`tests/test_database_queryset_update.py`](../../tests/test_database_queryset_update.py).

### 8.3.2 Remediación del estado QuerySet

- **Estado:** Verificado.
- **Meta:** preservar límites nativos de sliced/combined/distinct/order/annotations.
- **Resultado:** query preparada clonada con reemplazo sólo del WHERE por PKs.
- **Problema encontrado:** reconstruir desde filtros omitía estado semántico de Django.
- **Causa raíz:** una QuerySet no se reduce a su predicado.
- **Solución:** conservar el query object validado.
- **Evidencia TDD/verificación:** estados soportados/rechazados equivalentes a Django GREEN; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/36b-queryset-state-semantics`; commit [`d43efcb`](https://github.com/eamigo86/dj-hyperview/commit/d43efcb).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py), [`tests/test_database_queryset_update.py`](../../tests/test_database_queryset_update.py).

### 8.3.3 Remediación de prevalidación

- **Estado:** Verificado.
- **Meta:** fallar igual que Django antes de hacer reads/locks.
- **Resultado:** kwargs/campos/expresiones validados antes de SQL auxiliar.
- **Problema encontrado:** inputs inválidos podían producir queries antes de lanzar error.
- **Causa raíz:** validación propia posterior a la captura.
- **Solución:** usar la preparación nativa primero y respetar diferencias por versión.
- **Evidencia TDD/verificación:** invalid fields/expressions con cero query y distinct version-specific GREEN; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/36c-queryset-update-prevalidation`; commit [`ecc2dc6`](https://github.com/eamigo86/dj-hyperview/commit/ecc2dc6).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py), [`tests/test_database_queryset_update.py`](../../tests/test_database_queryset_update.py).

### 8.3.4 Remediación de pipeline única

- **Estado:** Verificado.
- **Meta:** conservar el número/orden nativo de `resolve_expression()`.
- **Resultado:** una sola `UpdateQuery` preparada y ejecutada sobre PKs; `none()` sin SQL.
- **Problema encontrado:** doble preparación alteraba expresiones con estado.
- **Causa raíz:** dos queries independientes resolvían los mismos valores.
- **Solución:** preparar/resolver una vez; recrear sólo tras compiler failure.
- **Evidencia TDD/verificación:** dos llamadas nativas verificadas, `none().update()` con zero SQL y módulo focal al 100%.
- **Trazabilidad:** rama local `slice/36d-queryset-update-single-pipeline`; commit [`2c8107c`](https://github.com/eamigo86/dj-hyperview/commit/2c8107c).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py), [`tests/test_database_queryset_update.py`](../../tests/test_database_queryset_update.py).

### 8.4 Servicio publish

- **Estado:** Verificado.
- **Meta:** crear/actualizar contenido validado con revisión y lock.
- **Resultado:** `publish_template()`, `PublicationResult` y `PublicationConflict`; revision 1/+1.
- **Problema encontrado:** create race y fallo DB genuino comparten `IntegrityError`.
- **Causa raíz:** excepción SQL por sí sola no prueba conflicto de nombre.
- **Solución:** rollback interno y prueba posterior de fila homónima.
- **Evidencia TDD/verificación:** create/update/expected revision/routing/races/save-once GREEN; conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/37-database-publication-service`; commit [`2bb1ecd`](https://github.com/eamigo86/dj-hyperview/commit/2bb1ecd).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/services.py`](../../src/dj_hyperview/contrib/database/services.py), [`tests/test_database_publication.py`](../../tests/test_database_publication.py).

### 8.4.1 Remediación del error publish

- **Estado:** Verificado.
- **Meta:** no ocultar fallos DB que no sean conflicto demostrado.
- **Resultado:** sólo create race verificado se traduce; update/unverified create preserva error nativo.
- **Problema encontrado:** la versión inicial reclasificaba demasiados `IntegrityError`.
- **Causa raíz:** traducción por clase de excepción, no por prueba del estado resultante.
- **Solución:** proof query tras rollback y conservación del error si no demuestra conflicto.
- **Evidencia TDD/verificación:** verificación independiente de cause/identity y carreras; cifra focal exacta no preservada.
- **Trazabilidad:** rama local `slice/37a-publication-error-contract`; commit [`a4349cc`](https://github.com/eamigo86/dj-hyperview/commit/a4349cc).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/services.py`](../../src/dj_hyperview/contrib/database/services.py), [`tests/test_database_publication.py`](../../tests/test_database_publication.py).

### 8.5 Servicios rename/delete

- **Estado:** Verificado.
- **Meta:** mutar/borrar con lock, revisión optimista y un único signal path.
- **Resultado:** `rename_template()`/`delete_template()`, campos opcionales y conflictos redactados.
- **Problema encontrado:** target race, source disappearance y delete cero son ambiguos frente a infraestructura caída.
- **Causa raíz:** el resultado de una excepción/delete count no identifica por sí solo la carrera.
- **Solución:** proofs acotadas dentro de atomic y traducción sólo cuando el conflicto se demuestra.
- **Evidencia TDD/verificación:** rename/delete, revisions, target races, multi-DB y postcommit GREEN; conteo focal exacto no consolidado.
- **Trazabilidad:** rama local `slice/38-database-rename-delete`; commit [`21a1747`](https://github.com/eamigo86/dj-hyperview/commit/21a1747).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/services.py`](../../src/dj_hyperview/contrib/database/services.py), [`tests/test_database_service_mutations.py`](../../tests/test_database_service_mutations.py).

### 8.5.1 Remediación del error rename/delete

- **Estado:** Verificado.
- **Meta:** preservar primary DB errors y descartar callbacks de delete no efectuado.
- **Resultado:** target proof excluye source PK; proof failure conserva error; zero-row delete aborta atomic.
- **Problema encontrado:** collations podían hacer que la propia fila “probara” conflicto; callback ya encolado podía confirmar.
- **Causa raíz:** proof demasiado amplia y excepción lanzada fuera del boundary correcto.
- **Solución:** exclusión de PK y aborto dentro de la transacción.
- **Evidencia TDD/verificación:** case-insensitive target, proof-query failure y cero callbacks tras delete cero verificados independientemente.
- **Trazabilidad:** rama local `slice/38a-database-mutation-error-contract`; commit [`bf4bf47`](https://github.com/eamigo86/dj-hyperview/commit/bf4bf47).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/services.py`](../../src/dj_hyperview/contrib/database/services.py), [`tests/test_database_service_mutations.py`](../../tests/test_database_service_mutations.py).

### 8.6 Admin a través de servicios

- **Estado:** Verificado.
- **Meta:** hacer que create/edit/rename/delete del admin usen la frontera validada.
- **Resultado:** ModelAdmin delega en servicios y evita invalidación duplicada fuera de signals.
- **Problema encontrado:** admin CRUD estándar saltaba revision/locking del servicio.
- **Causa raíz:** `ModelAdmin.save_model()` persiste directamente por defecto.
- **Solución:** overrides acotados, queryset en alias correcto y respuesta de conflicto.
- **Evidencia TDD/verificación:** publicación/admin/locking/conflict y signal-only invalidation GREEN; cifra focal exacta de esta subtask no preservada.
- **Trazabilidad:** rama local `slice/39-database-admin-publication`; commit [`8f8dc36`](https://github.com/eamigo86/dj-hyperview/commit/8f8dc36).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/admin.py`](../../src/dj_hyperview/contrib/database/admin.py), [`tests/test_database_admin_publication.py`](../../tests/test_database_admin_publication.py).

### 8.6.1 Remediación de cardinalidad/formato

- **Estado:** Verificado.
- **Meta:** aceptar exactamente un token de revisión no ambiguo para filas persistidas.
- **Resultado:** missing/duplicate/noncanonical token toma la ruta de conflicto; add no expone token.
- **Problema encontrado:** POST podía elegir un valor duplicado o pasar forma ambigua al servicio.
- **Causa raíz:** lectura de mapping normal pierde cardinalidad de valores.
- **Solución:** inspeccionar lista raw y validar decimal ASCII canónico.
- **Evidencia TDD/verificación:** missing/duplicate/stale/hostile/add/change/delete GREEN; cifra focal exacta no consolidada.
- **Trazabilidad:** rama local `slice/39a-admin-revision-token-contract`; commit [`a15592e`](https://github.com/eamigo86/dj-hyperview/commit/a15592e).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/admin.py`](../../src/dj_hyperview/contrib/database/admin.py), [`tests/test_database_admin_publication.py`](../../tests/test_database_admin_publication.py).

### 8.6.2 Remediación de bounds por alias

- **Estado:** Verificado.
- **Meta:** rechazar tokens gigantes/fuera del rango DB antes de `int()`/servicio.
- **Resultado:** length/range según `PositiveIntegerField` de la conexión seleccionada.
- **Problema encontrado:** strings oversized lanzaban `ValueError`; max+1 llegaba al servicio y el rango puede variar por alias.
- **Causa raíz:** conversión antes de validar y uso de límite no ligado a connection.
- **Solución:** cardinalidad, ASCII, forma, longitud y rango del field antes de conversión.
- **Evidencia TDD/verificación:** parent reprodujo 5 RED; 72 casos admin/adversariales, 37 probes parser y 2 multi-DB por versión; 652 base +34 admin, 98.15% Django 5.2.17/98.26% Django 6.1.1.
- **Trazabilidad:** rama local `slice/39b-admin-revision-token-bounds`; commit [`d2f7355`](https://github.com/eamigo86/dj-hyperview/commit/d2f7355).
- **Componentes/rutas:** [`src/dj_hyperview/contrib/database/admin.py`](../../src/dj_hyperview/contrib/database/admin.py), [`tests/test_database_admin_publication.py`](../../tests/test_database_admin_publication.py).

---

## Task 9 — Consumidor Django sintético

**Estado:** Verificado.

**Meta:** demostrar que un proyecto nuevo instala y configura el paquete sin depender de corpus real, servicios externos ni paths absolutos.

### 9.1 Scaffold mínimo

- **Estado:** Verificado.
- **Meta:** crear un host Django portable, mínimo y filesystem-free bajo tests.
- **Resultado:** el consumidor base instala sólo `DjHyperviewConfig`, usa URLConf vacío y no configura DB/cache.
- **Problema encontrado:** el RED del parent no podía importar `tests.consumer_project`.
- **Causa raíz:** el repositorio aún no tenía un proyecto consumidor package-owned.
- **Solución:** añadir settings, URLs y un probe de startup en proceso limpio.
- **Evidencia TDD/verificación:** RED por módulo ausente; 3 focales y 655 base + 34 admin por versión; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/01-consumer-scaffold`; parent [`7be11a6`](https://github.com/eamigo86/dj-hyperview/commit/7be11a6); commit [`36ed0c9`](https://github.com/eamigo86/dj-hyperview/commit/36ed0c9); 143 líneas.
- **Componentes/rutas:** [`tests/consumer_project/settings_base.py`](../../tests/consumer_project/settings_base.py), [`tests/consumer_project/urls.py`](../../tests/consumer_project/urls.py), [`tests/test_consumer_project_startup.py`](../../tests/test_consumer_project_startup.py).

### 9.2 Modo filesystem aislado

- **Estado:** Verificado.
- **Meta:** probar resolución/render desde archivos sin DB, caché ni red.
- **Resultado:** full, fragment, include, extends, precedencia, contenido vacío y nombres inseguros se ejercen con fixtures sintéticas.
- **Problema encontrado:** el RED carecía de `settings_filesystem`; además, un guard inicial de tres archivos bloqueaba la extensión legítima del consumidor.
- **Causa raíz:** no existía el perfil filesystem y el guard del scaffold asumía una cardinalidad fija.
- **Solución:** añadir dos roots portables, fixtures sólo de tests y convertir el guard en un subconjunto mínimo extensible.
- **Evidencia TDD/verificación:** RED por settings ausentes; 11 focales y 663 base + 34 admin por versión; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/02-consumer-filesystem`; parent [`36ed0c9`](https://github.com/eamigo86/dj-hyperview/commit/36ed0c9); commit [`43f1829`](https://github.com/eamigo86/dj-hyperview/commit/43f1829); 214 líneas.
- **Componentes/rutas:** [`tests/consumer_project/settings_filesystem.py`](../../tests/consumer_project/settings_filesystem.py), [`tests/test_consumer_filesystem.py`](../../tests/test_consumer_filesystem.py), [`tests/fixtures/consumer_project/`](../../tests/fixtures/consumer_project/).

### 9.3 Capacidades opcionales

- **Estado:** Verificado; cierra Task 9.
- **Meta:** demostrar que DB, admin y caché son opt-in y permanecen aislados.
- **Resultado:** cinco perfiles base/filesystem/database/cache/admin arrancan por separado; SQLite temporal y LocMem prueban source, revision, hit/miss/empty e invalidación.
- **Problema encontrado:** el RED registró 9 fallos porque los perfiles opcionales no existían.
- **Causa raíz:** el scaffold inicial no modelaba combinaciones opcionales sin contaminar el modo mínimo.
- **Solución:** añadir settings separados, runner de proceso portable y pruebas de frontera/capacidades.
- **Evidencia TDD/verificación:** RED 9 fallos/5 pases; 31 focales, 677 base + 34 admin por versión y cinco system checks; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/03-consumer-optional-stack`; parent [`43f1829`](https://github.com/eamigo86/dj-hyperview/commit/43f1829); commit [`01d9940`](https://github.com/eamigo86/dj-hyperview/commit/01d9940); 373 líneas.
- **Componentes/rutas:** [`tests/consumer_project/process.py`](../../tests/consumer_project/process.py), [`tests/consumer_project/settings_database.py`](../../tests/consumer_project/settings_database.py), [`tests/consumer_project/settings_cache.py`](../../tests/consumer_project/settings_cache.py), [`tests/consumer_project/settings_admin.py`](../../tests/consumer_project/settings_admin.py), [`tests/test_consumer_optional_stacks.py`](../../tests/test_consumer_optional_stacks.py), [`tests/test_consumer_installation_boundary.py`](../../tests/test_consumer_installation_boundary.py).

### Cierre de Task 9

- [x] RED reproducible sobre el parent exacto de cada child.
- [x] Todos los children son commits convencionales, reversibles y de hasta 400 líneas.
- [x] Django 5.2/6.1 y cobertura agregada/branches superiores al 95%.
- [x] Filesystem-only funciona sin tabla/admin/cache; capacidades opcionales sólo se activan en settings dedicados.
- [x] Fixtures permanecen bajo tests y fuera del runtime.
- [x] Verificación independiente cerró Task 9.

---

## Task 10 — Aceptación HTTP end-to-end

**Estado:** Verificado.

**Meta:** verificar request → response → engine → source/cache y admin/service → commit → refresh exclusivamente sobre el consumidor sintético.

### 10.1 Documentos HTTP full y fragment

- **Estado:** Verificado.
- **Meta:** probar respuestas, views y render lazy reales con metadata HTTP correcta.
- **Resultado:** endpoints full/fragment cubren status, headers, media type, UTF-8, escaping, 405 y miss redacted.
- **Problema encontrado:** el RED devolvía dos 404 porque las rutas aún no existían.
- **Causa raíz:** Task 9 probaba integración del resolver, pero no exponía HTTP.
- **Solución:** añadir views/URLs genéricas mediante APIs públicas y siete casos de aceptación.
- **Evidencia TDD/verificación:** 2 RED esperados y 7 GREEN; cierre independiente en Django 5.2.17/6.1.1; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/04-http-full-fragment`; parent [`01d9940`](https://github.com/eamigo86/dj-hyperview/commit/01d9940); commit [`e57344f`](https://github.com/eamigo86/dj-hyperview/commit/e57344f); 227 líneas.
- **Componentes/rutas:** [`tests/consumer_project/views.py`](../../tests/consumer_project/views.py), [`tests/consumer_project/urls.py`](../../tests/consumer_project/urls.py), [`tests/test_consumer_http_documents.py`](../../tests/test_consumer_http_documents.py).

### 10.2 Middleware y CSRF

- **Estado:** Verificado.
- **Meta:** probar detección header/Accept, middleware sync/async y un POST protegido por CSRF.
- **Resultado:** el middleware se ejecuta una vez, preserva full/fragment y el formulario rechaza tokens ausentes/inválidos.
- **Problema encontrado:** el RED devolvía tres 404 porque no existía la ruta de formulario.
- **Causa raíz:** el contrato HTTP previo no ejercía el pipeline real de middleware y CSRF.
- **Solución:** configurar el middleware antes de `CsrfViewMiddleware` y añadir form view/fixtures sintéticas.
- **Evidencia TDD/verificación:** 3 RED esperados y 10 focales GREEN; 694 base + 34 admin por versión; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/05-middleware-csrf`; parent [`e57344f`](https://github.com/eamigo86/dj-hyperview/commit/e57344f); commit [`1fc99fc`](https://github.com/eamigo86/dj-hyperview/commit/1fc99fc); 206 líneas.
- **Componentes/rutas:** [`tests/test_consumer_middleware_csrf.py`](../../tests/test_consumer_middleware_csrf.py), [`tests/consumer_project/settings_filesystem.py`](../../tests/consumer_project/settings_filesystem.py), [`tests/fixtures/consumer_project/primary/forms/submission.xml`](../../tests/fixtures/consumer_project/primary/forms/submission.xml).

### 10.3 Fuentes, caché y errores HTTP

- **Estado:** Verificado.
- **Meta:** cubrir filesystem/DB/LocMem, precedencia, invalidación y errores estables a través de HTTP.
- **Resultado:** los fallos se mapean a 400/404/422/503 con cuerpo HXML constante; origins filesystem no filtran paths.
- **Problema encontrado:** el RED carecía del endpoint de source y una aserción heredada de redacción dependía del path temporal.
- **Causa raíz:** los perfiles opcionales no tenían frontera HTTP y el test inspeccionaba filenames arbitrarios del traceback.
- **Solución:** añadir un endpoint genérico, escenarios hit/miss/empty/bypass/raise y una aserción de redacción basada sólo en campos controlados.
- **Evidencia TDD/verificación:** 3 RED; 28 focales, 706 base + 34 admin por versión; cobertura agregada 98.15%/98.26%.
- **Trazabilidad:** rama `integration/06-sources-errors`; parent [`1fc99fc`](https://github.com/eamigo86/dj-hyperview/commit/1fc99fc); commit [`12ba223`](https://github.com/eamigo86/dj-hyperview/commit/12ba223); 395 líneas.
- **Componentes/rutas:** [`tests/test_consumer_source_errors.py`](../../tests/test_consumer_source_errors.py), [`tests/test_database_source.py`](../../tests/test_database_source.py), [`tests/consumer_project/views.py`](../../tests/consumer_project/views.py).

### 10.4 Admin y publicación poscommit

- **Estado:** Verificado.
- **Meta:** aceptar create/edit/rename/delete reales del admin y visibilidad sólo después del commit.
- **Resultado:** caché permanece estable durante la transacción/rollback y la siguiente petición postcommit observa edit, rename o delete.
- **Problema encontrado:** el RED falló en tres casos porque no existía un perfil combinado admin+cache.
- **Causa raíz:** los perfiles opcionales aislados de Task 9 no podían demostrar juntos admin, servicios, transacción y caché.
- **Solución:** añadir settings/URLs dedicados y aceptación con admin estándar y servicios públicos.
- **Evidencia TDD/verificación:** 3 RED, 19 focales y 34 admin GREEN; verificación independiente incluida en el cierre acumulado de Task 10.
- **Trazabilidad:** rama `integration/07-admin-postcommit`; parent [`12ba223`](https://github.com/eamigo86/dj-hyperview/commit/12ba223); commit [`936f8d4`](https://github.com/eamigo86/dj-hyperview/commit/936f8d4); 269 líneas.
- **Componentes/rutas:** [`tests/consumer_project/settings_admin_postcommit.py`](../../tests/consumer_project/settings_admin_postcommit.py), [`tests/consumer_project/urls_admin_postcommit.py`](../../tests/consumer_project/urls_admin_postcommit.py), [`tests/test_consumer_admin_postcommit.py`](../../tests/test_consumer_admin_postcommit.py).

### 10.5 Multi-DB y proceso limpio

- **Estado:** Verificado; cierra Task 10.
- **Meta:** probar routing/alias, callbacks ligados a la conexión, aislamiento transaccional y frontera package-only en proceso limpio.
- **Resultado:** default/replica/broken verifican identidad de caché, read/write routing, commit/rollback independientes y errores redacted sin red/Redis/datos externos.
- **Problema encontrado:** el RED registró tres `ModuleNotFoundError` porque `settings_multidb` no existía; la primera iteración usó `using()` donde el manager especial requería `db_manager()`.
- **Causa raíz:** faltaba el perfil multi-DB y el test inicial confundió APIs de QuerySet y Manager.
- **Solución:** añadir router/perfil sintéticos, usar `db_manager(alias)` y ejecutar un probe blocker-first con APIs públicas.
- **Evidencia TDD/verificación:** 3 RED; 18 focales de 10.5 y 58 acumulados de Task 10; 716 base + 37 admin por versión; agregado 98.15%/98.26%, branches 96.70%/96.93%; PASS independiente 0/0/0.
- **Trazabilidad:** rama `integration/08-multidb-clean-process`; parent [`936f8d4`](https://github.com/eamigo86/dj-hyperview/commit/936f8d4); commit [`1c17ec1`](https://github.com/eamigo86/dj-hyperview/commit/1c17ec1); 340 líneas.
- **Componentes/rutas:** [`tests/consumer_project/routing.py`](../../tests/consumer_project/routing.py), [`tests/consumer_project/settings_multidb.py`](../../tests/consumer_project/settings_multidb.py), [`tests/test_consumer_multidb_isolation.py`](../../tests/test_consumer_multidb_isolation.py), [`tests/test_consumer_installation_boundary.py`](../../tests/test_consumer_installation_boundary.py).

### Cierre de Task 10

- [x] Full/fragment, contexto hostil, status/headers/media type/UTF-8 y errores estables.
- [x] Middleware sync/async y CSRF ausente/inválido/válido.
- [x] Filesystem, DB, cache y admin sólo cuando están configurados.
- [x] Commit refresca; rollback conserva; rename invalida old/new.
- [x] Multi-DB usa el alias correcto y `on_commit` queda ligado a su conexión.
- [x] Los ocho children de Tasks 9–10 son exact-parent, convencionales, reversibles y de hasta 400 líneas.
- [x] Verificación final en Django 5.2.17/6.1.1: 716 base + 37 admin, agregado 98.15%/98.26% y branches 96.70%/96.93%.
- [x] Cero builds, acceso al legacy, Redis/red, datos externos o XML/HXML dentro de `src/`.

---

## Task 11 — Dependencias y compatibilidad

**Estado:** Verificado.

**Meta:** auditar dependencias, versionar una resolución exacta y verificar la matriz Python/Django sin añadir shims artificiales.

### 11.1a Política de metadata

- **Estado:** Verificado.
- **Meta:** declarar rangos compatibles que incluyan las versiones estables auditadas el 2026-09-04.
- **Resultado:** metadata acotada para Django 5.2.17/6.1.1, lxml 6.1.3, uv-build 0.12.9, coverage 7.16.0, pytest 9.1.1, pytest-cov 7.1.0, pytest-django 4.14.0 y ruff 0.16.6.
- **Problema encontrado:** metadata, guard y el lock de 366 líneas sumaban 438 líneas, por encima del presupuesto.
- **Causa raíz:** el archivo de lock domina el tamaño aunque su cambio sea mecánico.
- **Solución:** dividir 11.1 en dos children exact-parent: política compatible y lock exacto.
- **Evidencia TDD/verificación:** RED por floor obsoleto de uv-build; guard focal y suites Django 5.2.17/6.1.1 GREEN; cierre independiente sin hallazgos.
- **Trazabilidad:** rama `deps/01-package-metadata-policy`; parent [`5eb04c4`](https://github.com/eamigo86/dj-hyperview/commit/5eb04c4); commit [`40950bc`](https://github.com/eamigo86/dj-hyperview/commit/40950bc); 50 líneas.
- **Componentes/rutas:** [`pyproject.toml`](../../pyproject.toml), [`tests/test_dependency_policy.py`](../../tests/test_dependency_policy.py).

### 11.1b Lock exacto

- **Estado:** Verificado; cierra Task 11.1.
- **Meta:** hacer reproducible el entorno de build/desarrollo/tests sin imponer pins exactos a consumidores.
- **Resultado:** `uv.lock` está versionado, contiene 17 paquetes y fija las dependencias directas auditadas.
- **Problema encontrado:** el RED confirmó que `uv.lock` seguía ignorado y no trackeado.
- **Causa raíz:** el scaffold inicial había diferido la política del lock.
- **Solución:** retirar la regla de ignore, versionar el lock y proteger metadata/resolución con un guard offline.
- **Evidencia TDD/verificación:** guard RED en el parent; `uv lock --check` y suites agregadas GREEN. La advertencia intermedia de coverage quedó resuelta en 11.3.
- **Trazabilidad:** rama `deps/01b-package-exact-lock`; parent [`40950bc`](https://github.com/eamigo86/dj-hyperview/commit/40950bc); commit [`e33f567`](https://github.com/eamigo86/dj-hyperview/commit/e33f567); 390 líneas.
- **Componentes/rutas:** [`uv.lock`](../../uv.lock), [`.gitignore`](../../.gitignore), [`tests/test_dependency_policy.py`](../../tests/test_dependency_policy.py).

### 11.2 Compatibilidad Django 5.2

- **Estado:** Verificado.
- **Meta:** convertir el soporte de Django 5.2.17 en un contrato ejecutable y fail-closed ante deprecations.
- **Resultado:** el consumidor público renderiza correctamente en 5.2.17; no se añadió shim runtime.
- **Problema encontrado:** la conducta ya era compatible, pero pytest no promovía deprecations a errores.
- **Causa raíz:** declarar un rango Django no detecta APIs obsoletas durante tests.
- **Solución:** añadir caracterización HTTP pública exacta y política permanente `filterwarnings = error`.
- **Evidencia TDD/verificación:** RED por ausencia de la política; aceptación pública, checks, migraciones y suites duales GREEN; cierre independiente sin hallazgos.
- **Trazabilidad:** rama `deps/02-django-52-compat`; parent [`e33f567`](https://github.com/eamigo86/dj-hyperview/commit/e33f567); commit [`27cbefc`](https://github.com/eamigo86/dj-hyperview/commit/27cbefc); 46 líneas.
- **Componentes/rutas:** [`tests/compat/test_django_52.py`](../../tests/compat/test_django_52.py), [`pyproject.toml`](../../pyproject.toml).

### 11.3 Django 6.1 y matriz Python

- **Estado:** Verificado; cierra Task 11.
- **Meta:** probar Django 6.1.1, Python 3.12–3.14, coverage agregado y Redis opt-in mediante un runner reproducible.
- **Resultado:** `tools/test_matrix.py` genera seis comandos portables, ejecuta base+admin con cobertura honesta y mantiene Redis desactivado salvo opt-in explícito.
- **Problema encontrado:** el gate default-only advertía 92% porque medía código admin mientras sus pruebas estaban omitidas.
- **Causa raíz:** el umbral se aplicaba antes de anexar el perfil admin real a los datos de coverage.
- **Solución:** ejecutar base con umbral intermedio 0, anexar admin y aplicar ≥95% sólo al agregado; fallar cerrado si Redis se pide sin URL.
- **Evidencia TDD/verificación:** RED por runner ausente; focal offline GREEN en Python 3.12.11, 3.13.9 y 3.14.0; Django 5.2.17/6.1.1 con 727 base + 34 admin, agregado 98.15%/98.26% y branches 96.70%/96.93%; PASS 0/0/0.
- **Trazabilidad:** rama `deps/03-django-61-python-matrix`; parent [`27cbefc`](https://github.com/eamigo86/dj-hyperview/commit/27cbefc); commit [`4d0c502`](https://github.com/eamigo86/dj-hyperview/commit/4d0c502); 329 líneas.
- **Componentes/rutas:** [`tools/test_matrix.py`](../../tools/test_matrix.py), [`tests/compat/test_django_61.py`](../../tests/compat/test_django_61.py), [`tests/compat/test_supported_python.py`](../../tests/compat/test_supported_python.py), [`tests/compat/test_redis_profile.py`](../../tests/compat/test_redis_profile.py).

### Cierre de Task 11

- [x] Rangos compatibles y versiones estables auditadas al 2026-09-04.
- [x] Lock exacto versionado y validable offline.
- [x] Django 5.2.17/6.1.1 sin shim runtime y con deprecations fail-closed.
- [x] Python 3.12–3.14 caracterizado en intérpretes instalados.
- [x] Runner canónico agrega base+admin antes de aplicar ≥95%.
- [x] Redis permanece opt-in; seis celdas y servicio real quedan para CI Task 13.4.
- [x] Cuatro children exact-parent, convencionales, reversibles y de hasta 400 líneas.
- [x] Verificación independiente cerró Task 11 sin hallazgos.

---

## Task 12 — Contrato package-owned con Hyperview 0.110.0

**Estado:** Verificado.

**Meta:** demostrar que el HXML y las respuestas HTTP producidas cumplen un contrato enfocado, reproducible y offline para Hyperview 0.110.0, sin ejecutar el cliente.

### 12.1 Fixtures, schema y provenance

- **Estado:** Verificado tras la remediación 12.1.1.
- **Meta:** fijar un contrato sintético para documento, fragmento, formulario, behaviors y referencias.
- **Resultado:** manifest con release npm, tag `v0.110.0` y commit `f715ae5cdf07733a4b846d7744518e42dff40407`; XSD enfocado y tres fixtures exclusivamente bajo tests.
- **Problema encontrado:** no existía una evidencia offline versionada y el XSD upstream completo excedía el presupuesto del child.
- **Causa raíz:** un pin de versión por sí solo no prueba la forma del HXML producido.
- **Solución:** conservar hashes/provenance y un schema explícitamente incompleto limitado a fixtures sintéticas.
- **Evidencia TDD/verificación:** 16 pruebas iniciales; Django 5.2.17/6.1.1 con 743 base + 34 admin y cobertura agregada 98.15%/98.26%; la revisión detectó los gaps corregidos en 12.1.1.
- **Trazabilidad:** rama `compat/01-hyperview-0110-fixtures`; parent [`b46d95f`](https://github.com/eamigo86/dj-hyperview/commit/b46d95f); commit [`df078b`](https://github.com/eamigo86/dj-hyperview/commit/df078b); 373 líneas.
- **Componentes/rutas:** [`tests/contracts/hyperview/0.110.0/`](../../tests/contracts/hyperview/0.110.0/), [`tests/test_hyperview_0110_fixtures.py`](../../tests/test_hyperview_0110_fixtures.py), [`tools/test_matrix.py`](../../tools/test_matrix.py).

### 12.1.1 Integridad de referencias

- **Estado:** Verificado; cierra 12.1.
- **Meta:** rechazar `xs:IDREF` colgantes y recuperar el gate de coverage del runner.
- **Resultado:** XSD valida forma/IDs y el helper test-only exige cardinalidad exacta target→id; el runner vuelve a 100% de líneas/branches.
- **Problema encontrado:** el XSD aceptaba un target inexistente y `tools/test_matrix.py` quedaba en 82.56% de coverage.
- **Causa raíz:** XSD 1.0 no impone por sí solo la integridad referencial esperada y el selector nuevo no estaba triangulado.
- **Solución:** añadir validación pos-schema con conteo de IDs/referencias y cubrir todos los modos/fallos del runner.
- **Evidencia TDD/verificación:** RED reprodujo referencia colgante/helper ausente; focal 42 passed/1 skipped por versión; 754 base + 34 admin, branches 96.70%/96.93%; PASS independiente.
- **Trazabilidad:** rama `compat/01a-hyperview-reference-integrity`; parent [`df078b`](https://github.com/eamigo86/dj-hyperview/commit/df078b); commit [`c229a54`](https://github.com/eamigo86/dj-hyperview/commit/c229a54); 175 líneas.
- **Componentes/rutas:** [`tests/hyperview_contract.py`](../../tests/hyperview_contract.py), [`tests/test_hyperview_0110_fixtures.py`](../../tests/test_hyperview_0110_fixtures.py), [`tests/compat/test_supported_python.py`](../../tests/compat/test_supported_python.py), [`tests/contracts/hyperview/0.110.0/manifest.json`](../../tests/contracts/hyperview/0.110.0/manifest.json).

### 12.2 Contrato HXML/HTTP

- **Estado:** Verificado tras la remediación 12.2.1.
- **Meta:** validar full/fragment/form, media type, encoding, status, escaping y CSRF por APIs públicas.
- **Resultado:** un helper test-only compone respuesta HTTP, parse seguro, schema, referencias y root esperado; [la guía de compatibilidad](../hyperview-0.110.0.md) limita explícitamente la promesa.
- **Problema encontrado:** la revisión halló que se confiaba en `response.charset` sin decodificar bytes y que DTDs no usados podían atravesar el parser.
- **Causa raíz:** `resolve_entities=False` y `no_network=True` impiden expansión/acceso, pero no prohíben declarar DTD/entities.
- **Solución:** conservar el contrato público/docs de 12.2 y endurecer su validador en 12.2.1.
- **Evidencia TDD/verificación:** RED original por helper ausente; 10 escenarios iniciales y canónicos duales GREEN; la revisión bloqueó el cierre hasta remediar UTF-8/DTD/entities.
- **Trazabilidad:** rama `compat/02-hyperview-protocol-doc`; parent [`c229a54`](https://github.com/eamigo86/dj-hyperview/commit/c229a54); commit [`670bb2b`](https://github.com/eamigo86/dj-hyperview/commit/670bb2b); 228 líneas.
- **Componentes/rutas:** [`tests/hyperview_contract.py`](../../tests/hyperview_contract.py), [`tests/test_hyperview_0110_http_contract.py`](../../tests/test_hyperview_0110_http_contract.py), [`docs/hyperview-0.110.0.md`](../hyperview-0.110.0.md).

### 12.2.1 Endurecimiento HTTP/XML

- **Estado:** Verificado; cierra Task 12.
- **Meta:** fallar cerrado ante bytes/declaraciones no UTF-8 y cualquier DTD/entity.
- **Resultado:** decode UTF-8 estricto, encoding XML coherente, DTD/entities rechazados antes del schema y errores estables/redacted sin chaining.
- **Problema encontrado:** el exact-parent RED produjo 9 fallos: 2 de encoding, 6 de DTD/entities y 1 de normalización del schema.
- **Causa raíz:** el validador comprobaba metadata HTTP, no bytes reales, y delegaba declaraciones peligrosas a flags del parser.
- **Solución:** decodificar `response.content` estrictamente, inspeccionar metadata del documento y normalizar excepciones lxml en la frontera test-only.
- **Evidencia TDD/verificación:** RED exacto 9 fallos/12 pases; focal 39 passed por Django; 775 base + 34 admin por versión, agregado 98.15%/98.26%, branches 96.70%/96.93%; PASS con 0 CRITICAL/0 WARNING.
- **Trazabilidad:** rama `compat/02a-hyperview-http-hardening`; parent [`670bb2b`](https://github.com/eamigo86/dj-hyperview/commit/670bb2b); commit [`84fc76d`](https://github.com/eamigo86/dj-hyperview/commit/84fc76d); 174 líneas.
- **Componentes/rutas:** [`tests/hyperview_contract.py`](../../tests/hyperview_contract.py), [`tests/test_hyperview_0110_http_contract.py`](../../tests/test_hyperview_0110_http_contract.py).

### Cierre de Task 12

- [x] Documento, fragmento, formulario, behaviors, IDs y referencias validados.
- [x] Media type, status, UTF-8, escaping y CSRF ejercidos por HTTP real.
- [x] DTD/entities y encoding contradictorio fallan cerrado con errores redacted.
- [x] Provenance npm/tag/commit y hashes preservados en manifest.
- [x] Fixtures/schema/helper permanecen bajo tests; cero plantillas runtime.
- [x] No se ejecutó Node, React Native, Expo ni cliente mobile.
- [x] Cuatro children exact-parent, convencionales, reversibles y de hasta 400 líneas.
- [x] Verificación independiente cerró Task 12 con 0 CRITICAL/0 WARNING.

---

## Task 13 — Documentación, CI y release

**Estado:** Verificado en
[077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b), con
0 CRITICAL en la revisión independiente final.

**Meta:** cerrar la superficie pública, documentación, compatibilidad alojada y
entrega PyPI → Pages sin incorporar pantallas ni plantillas runtime.

**Repositorios y publicación:** [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview)
y [eamigo86.github.io/dj-hyperview](https://eamigo86.github.io/dj-hyperview/).
Los workflows están implementados y verificados estáticamente; el primer run
hosted requiere el setup externo descrito en
[Release and rollback](../release-rollback.md).

### Resumen de la cadena

| Subtask | Resultado | Cierre principal |
|---:|---|---|
| 13.1 | API pública auditada y reparada | [35e7252](https://github.com/eamigo86/dj-hyperview/commit/35e7252) |
| 13.2 | Zensical y documentación base | [5396477](https://github.com/eamigo86/dj-hyperview/commit/5396477) |
| 13.3 | Guías de fuentes y operación | [70833ba](https://github.com/eamigo86/dj-hyperview/commit/70833ba) |
| 13.4 | CI, Redis, artifacts y contratos fail-closed | [9843e0f](https://github.com/eamigo86/dj-hyperview/commit/9843e0f) |
| 13.5 | Preview manual sin deploy | [40ee52b](https://github.com/eamigo86/dj-hyperview/commit/40ee52b) |
| 13.6 | Tag, artifact, PyPI OIDC y Pages | [077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b) |

### 13.1 Auditoría pública

- **Meta:** exigir docstrings Google completas, type hints válidos y cero
  backticks en toda la superficie pública.
- **Hecho:** se creó un auditor AST fail-closed para módulos, clases, funciones,
  métodos, exports, Args, Returns y Raises; se reparó la API sin cambiar su
  comportamiento.
- **Problemas:** exports dinámicos/reflexivos, annotations malformadas, métodos
  magic y límites de secciones Google escapaban a validadores parciales. Se
  cerraron mediante 19 children exact-parent y probes adversariales.
- **Evidencia:** [35e7252](https://github.com/eamigo86/dj-hyperview/commit/35e7252);
  843 tests base + 34 admin por Django, 141 docstrings públicas sin backticks y
  revisión acumulativa con 0 hallazgos.

### 13.2 Fundación Zensical

- **Meta:** incorporar tooling documental reproducible y un quick path de
  instalación/configuración sin UI bundled.
- **Hecho:** Zensical 0.0.59 quedó fijado en desarrollo, con lock exacto,
  zensical.yml y guías base de instalación y configuración.
- **Problemas:** el lock consumía 235 líneas y Zensical no autodetecta el YAML;
  se separó dependencia/documentación y todos los comandos usan
  -f zensical.yml.
- **Evidencia:** [8e21e8e](https://github.com/eamigo86/dj-hyperview/commit/8e21e8e)
  → [5396477](https://github.com/eamigo86/dj-hyperview/commit/5396477);
  846 tests base + 34 admin por Django y config parseada sin build.

### 13.3 Guías del paquete

- **Meta:** documentar filesystem, DB/admin, caché, seguridad, testing,
  release y rollback contra APIs reales.
- **Hecho:** se agregaron seis guías navegables con snippets verificados,
  invalidación poscommit y operación bulk/SQL explícita.
- **Problemas:** el volumen exigió separar fuentes y operación; además se
  corrigieron la promesa Render→Resolve y la terminología include/extends.
- **Evidencia:** [777f8f5](https://github.com/eamigo86/dj-hyperview/commit/777f8f5)
  → [70833ba](https://github.com/eamigo86/dj-hyperview/commit/70833ba)
  → [a8220c3](https://github.com/eamigo86/dj-hyperview/commit/a8220c3);
  853 tests base + 34 admin por Django.

### 13.4 CI y artifacts sin deploy

- **Meta:** ejecutar matriz Python 3.12–3.14 × Django 5.2/6.1, Redis opt-in,
  coverage, calidad, wheel smoke y Zensical hosted sin privilegios de deploy.
- **Hecho:** ci.yml reutilizable ejecuta los gates, construye una sola vez,
  prueba el wheel fuera del checkout y sube un candidate inmutable; la
  instalación normal no incluye Redis.
- **Problemas:** validadores iniciales de substrings permitían wrappers,
  variables, continue-on-error, overrides globales y pérdida de tipos YAML.
  Se reemplazaron por fixtures semánticas completas y firmas kind+tag+value.
- **Evidencia:** [0828bfc](https://github.com/eamigo86/dj-hyperview/commit/0828bfc)
  → [78b7e78](https://github.com/eamigo86/dj-hyperview/commit/78b7e78)
  → [11c54d4](https://github.com/eamigo86/dj-hyperview/commit/11c54d4)
  → [d951906](https://github.com/eamigo86/dj-hyperview/commit/d951906)
  → [9d44371](https://github.com/eamigo86/dj-hyperview/commit/9d44371)
  → [7bef254](https://github.com/eamigo86/dj-hyperview/commit/7bef254)
  → [469ce02](https://github.com/eamigo86/dj-hyperview/commit/469ce02)
  → [9843e0f](https://github.com/eamigo86/dj-hyperview/commit/9843e0f);
  907 tests base + 34 admin por Django.

### 13.5 Preview manual

- **Meta:** permitir revisar el sitio generado sin publicar ni tocar Pages.
- **Hecho:** docs-preview.yml sólo responde a workflow_dispatch, conserva
  permisos read-only, construye Zensical en hosted CI y sube site como artifact
  ordinario.
- **Problemas:** el contrato debía distinguir la clave on sin borrar tipos
  escalares; se reutilizó el patrón de firma de nodos typed.
- **Evidencia:** [40ee52b](https://github.com/eamigo86/dj-hyperview/commit/40ee52b);
  931 tests base + 34 admin por Django, 0 CRITICAL y ningún build local.

### 13.6 PyPI y después Pages

- **Meta:** aceptar sólo tags válidos, publicar exactamente el candidate
  verificado y desplegar Pages únicamente después de PyPI.
- **Hecho:** release.yml valida PEP 440 canónico antes de CI, reutiliza el build,
  separa checksums de dist, publica con Trusted Publishing y entrega el Pages
  artifact sin reconstruir. packaging 26.3 es dependencia directa locked.
- **Problemas:** el workflow protegido requirió autorización explícita; el gate
  inicial aceptaba versiones inválidas y el runbook describía OIDC de forma
  ambigua. Tests RED cerraron ambos defectos.
- **Evidencia:** [e0744ab](https://github.com/eamigo86/dj-hyperview/commit/e0744ab)
  → [1affd94](https://github.com/eamigo86/dj-hyperview/commit/1affd94)
  → [956790a](https://github.com/eamigo86/dj-hyperview/commit/956790a)
  → [077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b);
  987 tests base + 34 admin por Django y 217 gates contractuales acumulados.

### Criterios de cierre

- [x] Tasks 1–13 verificadas en cadena exact-parent.
- [x] Cobertura total y branches por encima de 95% en Django 5.2/6.1.
- [x] Wheel guard impide UI/XML/HXML runtime.
- [x] Zensical, wheel y Redis están declarados sólo para ejecución hosted.
- [x] PyPI debe finalizar antes de Pages y OIDC es job-local.
- [x] Estado, roadmap y decisiones reflejan el cierre.
- [x] Warning residual documentado: quoted "on" produce un falso positivo
  conservador de YAML 1.1, nunca un bypass.
- [ ] Pendiente externo: configurar Trusted Publisher, environments y Pages
  antes del primer tag; esto no reabre Task 13.

---

## Post-MVP

### P1 — `bulk_create()`

Invalidación automática para batches, PK explícita, rollback y conflict modes; nueva especificación y work unit independiente.

### P2 — `bulk_update()`

Invalidación automática para nombres viejos/nuevos, expresiones, multi-DB y guard de reentrancia; nueva especificación y work unit independiente.

Hasta entonces el consumidor usa servicios o agenda `invalidate_templates()` con `transaction.on_commit()`.

## Referencias

- [Roadmap](roadmap.md)
- [Especificación](specification.md)
- [Arquitectura](architecture.md)
- [Decisiones](decisions.md)
- [Estado](status.md)
