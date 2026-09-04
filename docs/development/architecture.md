# Arquitectura de dj-hyperview

`dj-hyperview` separa infraestructura y contenido: el paquete controla cómo se localiza, renderiza, valida, cachea y publica HXML; el proyecto consumidor controla todas las pantallas y sus datos.

## Vista general

```text
Petición móvil → Response/View → HyperviewEngine → ResolverLoader
    → TemplateResolver → cache Django opcional
                       ├→ filesystem del consumidor
                       └→ DB opcional del consumidor

Admin/servicio → mutación validada → transaction.on_commit
    → rotar generación → cache Django
```

## Componentes

| Componente | Responsabilidad | Límite |
|---|---|---|
| [`conf.py`](../../src/dj_hyperview/conf.py), [`checks.py`](../../src/dj_hyperview/checks.py) | Materializar settings tipados y detectar errores | No conectan a DB/cache/red durante el arranque mínimo |
| [`sources/base.py`](../../src/dj_hyperview/sources/base.py) | Nombre canónico, `TemplateSource`, `ResolvedTemplate` | El protocolo es point lookup, no snapshot global |
| [`sources/filesystem.py`](../../src/dj_hyperview/sources/filesystem.py) | Leer UTF-8 bajo raíces del consumidor | Bloquea traversal y escapes por symlink |
| [`contrib/database/sources.py`](../../src/dj_hyperview/contrib/database/sources.py) | Leer filas activas por nombre exacto | App, tabla y alias son opcionales |
| [`resolver.py`](../../src/dj_hyperview/resolver.py) | Precedencia, misses y política de caché | La primera fuente encontrada gana |
| [`loaders.py`](../../src/dj_hyperview/loaders.py) | Integrar el resolver con DjangoTemplates | Snapshot por nombre y por identidad exacta de resolver |
| [`engine.py`](../../src/dj_hyperview/engine.py) | Render dedicado, includes/extends y validación | No modifica el engine HTML global |
| [`validation.py`](../../src/dj_hyperview/validation.py) | Guardas fuente y validación posrender | Sin entidades, XXE, red o XSD externos |
| [`http.py`](../../src/dj_hyperview/http.py), [`views.py`](../../src/dj_hyperview/views.py) | Responses y view con media type Hyperview | Preservan lifecycle, status y headers Django |
| [`middleware.py`](../../src/dj_hyperview/middleware.py) | Detección tipada sync/async | No altera la respuesta downstream |
| [`cache.py`](../../src/dj_hyperview/cache.py) | Envelope crudo, claves, generaciones y tombstones | La caché acelera; nunca es autoridad |
| [`contrib/database/models.py`](../../src/dj_hyperview/contrib/database/models.py) | Modelo opcional y revisión | `save()` no llama `full_clean()` automáticamente |
| [`signals.py`](../../src/dj_hyperview/contrib/database/signals.py), [`querysets.py`](../../src/dj_hyperview/contrib/database/querysets.py) | Capturar nombres mutados e invalidar poscommit | Bulk APIs externas requieren invalidación explícita |
| [`services.py`](../../src/dj_hyperview/contrib/database/services.py) | Publicar, renombrar y borrar de forma validada | Frontera recomendada para mutaciones de consumidor/admin |
| [`admin.py`](../../src/dj_hyperview/contrib/database/admin.py) | UI estándar y conflictos optimistas | Sólo existe si se instala admin + contrib DB |

## Flujo de lectura desde filesystem

1. El consumidor configura `FileSystemSource` y sus directorios.
2. `TemplateResolver` canonicaliza el nombre una vez.
3. Si hay caché, obtiene la generación vigente y busca un envelope ligado a fuente/nombre/generación.
4. En miss real, la fuente resuelve dentro de raíces seguras y calcula metadata/revisión.
5. El loader fija el resultado por nombre durante ese render.
6. El engine renderiza contexto, includes y extends con el mismo resolver.
7. La salida atraviesa validación XML/XSD/límites.
8. La respuesta usa el media type Hyperview.

## Flujo de lectura desde base de datos

1. El consumidor instala `dj_hyperview.contrib.database` y ejecuta migraciones.
2. `DatabaseSource` resuelve el modelo de forma lazy.
3. Un alias explícito se valida y habilita una identidad de caché estable.
4. Sin alias, el router elige la base; esa fuente queda deliberadamente fuera de caché genérica porque puede variar por request/tenant.
5. Una fila activa es hit, incluso con contenido vacío; una fila inactiva o ausente es miss.
6. La siguiente fuente sólo se consulta en miss.

## Flujo de edición y refresh

```text
Usuario admin
  → Admin/Servicio: guardar XML validado + revisión esperada
  → DB: lock, validar, save/delete
  → transaction.on_commit: programar nombres viejo/nuevo

Si hay commit:
  → cache: rotar generación; confirmar barrera o emitir error observable
  → próxima petición: consultar generación nueva y resolver contenido vigente

Si hay rollback:
  → Django descarta el callback; la cache no se invalida
```

“Tiempo real” en v1 significa consistencia en la siguiente petición después del commit. No implica push, WebSockets ni interrupción de una petición que ya fijó su snapshot.

## Semántica transaccional

- Cada mutación usa el alias real de escritura.
- Los nombres afectados se canonicalizan, ordenan y deduplican antes de registrar el callback.
- Save/delete y `QuerySet.update()` controlado programan exactamente una frontera poscommit.
- Rename invalida el nombre persistido anterior y el nuevo.
- Rollback y savepoints revertidos descartan callbacks.
- Si la invalidación falla después del commit, el dato permanece confirmado y el error es observable; no se afirma falsamente que la escritura revirtió.
- Invalidar varios nombres no es una operación atómica del backend de caché. El caller puede reintentar el conjunto completo.

## Caché e invalidación

### Estados distinguibles

| Estado | Significado |
|---|---|
| `None` | No existe entrada cacheada. |
| `CACHE_MISS` | La fuente no encontró el nombre y ese miss fue cacheado. |
| `CacheEntry` | Existe contenido crudo; puede ser una cadena vacía. |

### Identidad

Las claves de tamaño fijo derivan de namespace, fuente, nombre y revisión/generación. La identidad de fuente se calcula desde posición, backend y opciones efectivas mediante una representación tipada. Si una configuración arbitraria no puede representarse sin ambigüedad, sólo esa fuente opera sin caché.

### Barrera generacional

La invalidación rota un token compartido por nombre. Claims y tombstones no expirables evitan que una publicación lenta recupere una generación obsoleta. La corrección depende de que el backend no expulse selectivamente los tombstones mientras conserva raw entries; por eso se requiere dimensionar y aislar apropiadamente el namespace/backend. La unicidad criptográfica es defensa residual, no una transacción durable.

## Seguridad

| Riesgo | Control |
|---|---|
| Path traversal | Nombres POSIX canónicos, raíces resueltas y control de symlinks |
| Filtrado de paths/secrets | Excepciones públicas redactadas y fingerprints hash |
| XXE/entidades/DTD | Scanner contextual antes de compile y parser seguro |
| XSD externo | Resolver sin red y rechazo de include/import |
| Bomba de recursos | Límites de bytes, profundidad y nodos |
| XSS/XML roto | Escape de contexto Django y validación posrender |
| CSRF | Token/tag estándar y `CsrfViewMiddleware` del host |
| Caché hostil | Envelope JSON exacto, tipos estrictos, identidad ligada al lookup |
| Stale write | Revisión optimista, lock de fila y conflicto público redactado |
| Multi-DB | Alias configurado/router y callback sobre alias de mutación |

## Opcionalidad

La app base no importa el modelo. La instalación mínima necesita únicamente:

```python
INSTALLED_APPS = [
    "dj_hyperview",
]

HYPERVIEW = {
    "TEMPLATE_DIRS": [BASE_DIR / "mobile_screens"],
    "SOURCES": [
        {"BACKEND": "dj_hyperview.sources.FileSystemSource"},
    ],
}
```

El modo DB añade `dj_hyperview.contrib.database`; el admin sólo aparece si el proyecto también instala `django.contrib.admin`. Redis no se importa directamente: se selecciona mediante un backend de caché Django configurado por el consumidor.

## Extensibilidad

Un source de terceros implementa un lookup de nombre canónico y devuelve `ResolvedTemplate` o `None`. Puede participar en caché si su configuración tiene una identidad cerrada y estable. Una fuente dinámica por tenant/request debe deshabilitar caché o aportar una identidad que represente todas sus entradas observables.

Las APIs de mutación recomendadas son `publish_template()`, `rename_template()` y `delete_template()`. Para bulk/SQL externo, el consumidor debe registrar `invalidate_templates()` después del commit hasta que los hooks post-MVP estén diseñados.

## Límites conocidos

- El snapshot es coherente por nombre ya resuelto, no una transacción global entre nombres consultados por primera vez en momentos distintos.
- Una petición iniciada antes de publicar puede completar con su snapshot anterior.
- Multi-name invalidation puede fallar parcialmente y requiere retry.
- Tombstones consumen una marca pequeña por candidato durante la vida del namespace/backend.
- `bulk_create()`, `bulk_update()` y SQL crudo no se interceptan en v1.
- El legacy sólo se consulta como referencia read-only; el contrato aceptable se ejerce en `tests/consumer_project/` (planificado dentro de [`tests/`](../../tests/)) del paquete.

## Lectura relacionada

- [Especificación](specification.md)
- [Decisiones](decisions.md)
- [Tareas](tasks.md)
- [Estado](status.md)
