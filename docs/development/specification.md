# Especificación funcional de dj-hyperview

La versión inicial debe poder instalarse en un proyecto Django sin aportar ninguna pantalla propia. El host configura fuentes XML, puede activar un modelo/admin y una caché, y obtiene HXML validado mediante una API pública estable.

## Convenciones

- **MUST / Debe:** obligatorio para aceptar la versión inicial.
- **MAY / Puede:** capacidad opcional.
- **Consumidor:** proyecto Django que instala `dj-hyperview`.
- **Plantilla:** XML/HXML propiedad del consumidor.
- **Siguiente petición:** primera resolución iniciada después de confirmar una mutación y completar su invalidación poscommit.

## Requisitos funcionales

### F1. Frontera del paquete

La distribución debe llamarse `dj-hyperview`, importar como `dj_hyperview` y registrarse como app Django. El wheel no debe contener XML/HXML runtime, pantallas ni datos del proyecto de referencia.

**Escenario — inspección del artefacto**

- **Dado** un wheel de release.
- **Cuando** CI inspecciona su contenido y metadata.
- **Entonces** conserva la identidad `dj-hyperview`/`dj_hyperview` y no incluye plantillas runtime.

### F2. Configuración y opcionalidad

`HYPERVIEW` debe configurar directorios, fuentes ordenadas, caché y validación. Los system checks deben rechazar configuraciones inválidas con identificadores y mensajes accionables. DB, admin y Redis son opcionales.

**Escenario — instalación mínima**

- **Dado** un consumidor con la app base y una fuente filesystem.
- **Cuando** Django arranca y ejecuta checks.
- **Entonces** no consulta tablas, Redis ni el admin.

**Escenario — configuración inválida**

- **Dado** un backend, alias, directorio o límite inválido.
- **Cuando** se ejecutan los checks o se materializa la configuración.
- **Entonces** se informa un error estable sin filtrar secretos de opciones.

### F3. Fuentes y precedencia

Las plantillas deben resolverse desde fuentes configurables. V1 incluye `FileSystemSource` y `DatabaseSource`; un consumidor puede aportar otra implementación del protocolo `TemplateSource`. La primera coincidencia gana y un miss continúa a la siguiente fuente.

**Escenario — precedencia**

- **Dado** el mismo nombre en dos fuentes.
- **Cuando** el resolver consulta las fuentes.
- **Entonces** devuelve el contenido de la primera fuente declarada.

**Escenario — contenido vacío**

- **Dado** una plantilla existente cuyo contenido es una cadena vacía.
- **Cuando** se resuelve.
- **Entonces** se trata como hit, distinto de ausencia.

### F4. Nombres seguros

Los nombres deben ser rutas POSIX relativas canónicas. Se deben rechazar nombres vacíos, absolutos, con segmentos punto, backslashes, NUL, controles, surrogates o escapes por symlink.

**Escenario — traversal**

- **Dado** un nombre que intenta salir de las raíces configuradas.
- **Cuando** el filesystem source lo procesa.
- **Entonces** falla antes de leer un archivo externo y no expone la ruta en la excepción pública.

### F5. Engine aislado

El render debe usar un engine Django dedicado y un `ResolverLoader`, sin monkey-patching ni alterar loaders HTML globales. Raíz, `{% include %}` y `{% extends %}` deben compartir las mismas reglas.

**Escenario — herencia homogénea**

- **Dado** una plantilla que incluye o extiende otra.
- **Cuando** se renderiza.
- **Entonces** todas se resuelven con la misma precedencia y validación Hyperview.

### F6. Contrato HTTP

Las respuestas deben usar por defecto `application/vnd.hyperview+xml`, preservar status, charset y headers de Django y mantener el render lazy de `TemplateResponse`. Debe existir detección tipada de cliente Hyperview, middleware sync/async y un tag CSRF XML-safe.

**Escenario — equivalencia sync/async**

- **Dado** requests equivalentes.
- **Cuando** atraviesan middleware síncrono y asíncrono.
- **Entonces** reciben la misma metadata y la respuesta downstream permanece intacta.

**Escenario — mutación protegida**

- **Dado** un formulario Hyperview sin token CSRF válido.
- **Cuando** Django procesa la petición.
- **Entonces** la protección estándar lo rechaza.

### F7. Validación HXML segura

Toda ruta pública de render debe aplicar guardas antes de compilar y validar el XML renderizado. Debe rechazar DTD, entidades, XXE, recursos externos, XML malformado, schema inválido y excesos de bytes, profundidad o nodos. El contexto debe escapar contenido hostil.

**Escenario — documento hostil**

- **Dado** un documento con una entidad externa o un XSD con include/import.
- **Cuando** se valida.
- **Entonces** falla sin acceso a archivo o red y devuelve un error público determinista.

**Escenario — template compilado**

- **Dado** una plantilla obtenida por `get_template()` o `select_template()`.
- **Cuando** se renderiza.
- **Entonces** conserva la API/metadata de Django y atraviesa una sola frontera de validación configurada.

### F8. Caché opcional

La caché debe usar la API pública de caché de Django, almacenar sólo contenido crudo serializado y nunca ser autoridad. Debe distinguir no-entry, miss cacheado y contenido vacío. Las claves deben aislar namespace, fuente, nombre y generación.

**Escenario — caché omitida**

- **Dado** `CACHE` ausente o vacío.
- **Cuando** se crea el resolver.
- **Entonces** no materializa ningún backend de caché.

**Escenario — fallo de backend**

- **Dado** un fallo de get/set/decode.
- **Cuando** la política es `bypass` o `raise`.
- **Entonces** se respeta esa política, sin filtrar contenido cacheado ni debilitar una invalidación explícita.

### F9. Invalidación y carreras

`invalidate_templates()` debe rotar una generación compartida por nombre. Una lectura iniciada antes de la rotación no debe repoblar la generación nueva. Todo fallo de barrera debe ser observable. Claims y tombstones deben prevenir reutilización ABA dentro del límite documentado del backend de caché.

**Escenario — lectura en vuelo**

- **Dado** una lectura lenta sobre una generación anterior.
- **Cuando** otra transacción invalida y publica una generación nueva.
- **Entonces** la lectura anterior puede terminar con su snapshot, pero no publica su resultado como vigente.

### F10. Contribución de base de datos

`dj_hyperview.contrib.database` debe ser una app opcional con `HyperviewTemplate`, migraciones reversibles, `DatabaseSource`, señales, manager/queryset, servicios y admin. Omitirla no debe importar modelos ni exigir tabla.

**Escenario — resolución DB**

- **Dado** una fila activa con nombre exacto.
- **Cuando** `DatabaseSource` la consulta.
- **Entonces** devuelve contenido, origen y revisión; filas inactivas o ausentes son miss.

### F11. Publicación y consistencia poscommit

Create, update, rename, delete y el `QuerySet.update()` controlado deben programar invalidación sólo mediante `transaction.on_commit()` y usando el alias de escritura. Los servicios deben validar, bloquear, incrementar revisión y ofrecer conflicto optimista sin filtrar detalles.

**Escenario — commit**

- **Dado** contenido cacheado y una edición válida en admin o servicio.
- **Cuando** la transacción confirma.
- **Entonces** se invalida el nombre afectado y la siguiente petición obtiene el contenido nuevo.

**Escenario — rollback**

- **Dado** una mutación dentro de una transacción que revierte.
- **Cuando** finaliza la transacción.
- **Entonces** no se ejecuta la invalidación programada.

**Escenario — rename**

- **Dado** una fila renombrada.
- **Cuando** confirma.
- **Entonces** se invalidan de forma canónica el nombre anterior y el nuevo.

### F12. Admin opcional

El admin debe integrarse por autodiscovery estándar cuando `django.contrib.admin` esté instalado. Revisión y timestamps son read-only. Edit/delete de filas persistidas requieren un único token de revisión válido y acotado por el tipo entero del alias seleccionado.

**Escenario — conflicto de edición**

- **Dado** un formulario con revisión ausente, duplicada, stale o fuera de rango.
- **Cuando** se intenta guardar o borrar.
- **Entonces** no llama al servicio, no muta y presenta un conflicto seguro.

### F13. Consumidor sintético package-owned

El repositorio del paquete debe crear `tests/consumer_project/` (planificado dentro de [`tests/`](../../tests/)) y fixtures exclusivamente dentro de [`tests/fixtures/`](../../tests/fixtures/). Debe probar instalación, settings, URLs, filesystem y configuración opcional DB/admin/cache usando sólo APIs públicas, paths portables y dominios genéricos. No debe importar, ejecutar ni copiar el proyecto legacy.

**Escenario — instalación aislada**

- **Dado** un checkout limpio sin `django_hv` ni datos externos.
- **Cuando** arranca el consumidor sintético en modo filesystem-only.
- **Entonces** sus checks/URLs funcionan sin tabla, admin, cache ni paths absolutos.

### F14. Aceptación HTTP end-to-end

El consumidor sintético debe ejercer views/responses/middleware/CSRF, documentos full/fragment, filesystem/DB/cache, admin poscommit, multi-DB y fallos controlados. No debe incorporar users, contacts, icon cache ni código de negocio del legacy.

**Escenario — refresh después de admin**

- **Dado** una pantalla resuelta y cacheada por el consumidor sintético.
- **Cuando** un usuario autorizado la edita en admin y la transacción confirma.
- **Entonces** la siguiente petición Hyperview devuelve el XML actualizado.

**Escenario — proceso limpio**

- **Dado** un proceso donde `django_hv` no está instalado y su import está bloqueado.
- **Cuando** se ejercitan todos los modos del consumidor sintético.
- **Entonces** ningún import transitivo ni path de runtime depende del paquete abandonado.

### F15. Contrato Hyperview 0.110.0

El paquete debe validar mediante fixtures/tests/docs propios que su media type, encoding y HXML full/fragment son compatibles con [`hyperview` 0.110.0](https://www.npmjs.com/package/hyperview/v/0.110.0), versión estable comprobada el 2026-09-04. No se modifica la app mobile legacy; modernizar una app real es otro proyecto y no bloquea este release.

**Escenario — fixture contractual**

- **Dado** un documento sintético full o fragment propiedad de tests.
- **Cuando** atraviesa el HTTP/engine del paquete.
- **Entonces** cumple XML/schema/media type/encoding declarados y permanece fuera del wheel.

## Requisitos no funcionales

| Área | Requisito |
|---|---|
| Compatibilidad | Python 3.12–3.14; Django 5.2 y 6.1. |
| Calidad | TDD estricto en el paquete Python y consumidor sintético dentro de su repositorio; cobertura de ramas global ≥95%. |
| Seguridad | Fail-closed en nombres, XML, schema, caché no confiable y conflictos; CSRF estándar. |
| Opcionalidad | Filesystem-only funciona sin DB, admin, Redis o red. |
| Concurrencia | Semántica determinista para snapshots, publicación e invalidación en vuelos concurrentes. |
| Multi-DB | Lecturas/escrituras respetan alias explícito o router; invalidación usa el alias de mutación. |
| Observabilidad | Un fallo poscommit se propaga como fallo de publicación; no se presenta como rollback del dato persistido. |
| API pública | Type hints obligatorios y docstrings Google-style completas, sin repetir tipos ni usar backticks dentro de docstrings. |
| Artefactos | Wheel sin pantallas; metadata, links y nombre de PyPI verificables. |
| Documentación | Zensical; PR/main sólo construyen; Pages se despliega después de publicar un release versionado. |

## Fuera de alcance de v1

- Plantillas o pantallas XML/HXML dentro del paquete.
- Migración, copia o paridad obligatoria de datos del legacy.
- Extender Hyperview, React Native o Expo, o modernizar la app mobile legacy.
- WebSockets, push o sincronización activa del dispositivo.
- Invalidación automática de `bulk_create()`/`bulk_update()` o SQL crudo.
- Garantía transaccional global sobre varios nombres o varias claves de caché.
- Compatibilidad drop-in con la distribución o namespace abandonados `django-hyperview`/`django_hv`.
- Redis como dependencia obligatoria; se soporta mediante un backend Django compatible configurado por el consumidor.

## Aceptación de release

- [ ] Tasks 9–13 completadas y verificadas en el repositorio del paquete.
- [ ] Matriz Python/Django y celda Redis verdes.
- [ ] Cobertura de ramas ≥95%.
- [ ] Wheel inspeccionado sin XML/HXML runtime.
- [ ] Documentación Zensical construida en CI.
- [ ] Paquete publicado antes de desplegar Pages.
- [ ] Ningún gate depende del proyecto o de las 40 filas experimentales legacy.
