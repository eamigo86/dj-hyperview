# Plan de desarrollo de dj-hyperview

**Tasks 1–13 verificadas.** El MVP package-only culmina en
[077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b):
la librería, las guías, CI, preview y el flujo PyPI → Pages están implementados
y pasaron revisión independiente. Antes del primer release sólo queda la
configuración externa de PyPI y GitHub Pages.

> El proyecto legacy es material de consulta de solo lectura. No es el destino
> de implementación, no define el contrato público y sus datos no forman parte
> del paquete.

## Ruta de lectura

1. [Estado actual](status.md): evidencia final, límites y setup externo.
2. [Roadmap](roadmap.md): secuencia cerrada de Tasks 1–13 y trabajo post-MVP.
3. [Especificación](specification.md): contrato funcional de la versión inicial.
4. [Arquitectura](architecture.md): componentes, flujos y límites técnicos.
5. [Tareas](tasks.md): metas, implementación, problemas y cierres por subtask.
6. [Contrato Hyperview 0.110.0](../hyperview-0.110.0.md): protocolo validado.
7. [Decisiones](decisions.md): arquitectura y tradeoffs vigentes.

## Leyenda de estado

| Estado | Significado |
|---|---|
| **Verificado** | Implementado en dj-hyperview y aprobado mediante pruebas independientes. |
| **Pendiente externo** | Requiere configuración del servicio, no más código local. |
| **Post-MVP** | Deliberadamente aplazado para una versión posterior. |
| **Histórico no válido** | Experimento externo que no cuenta como avance del paquete. |

## Fuentes de verdad

- Repositorio: [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview).
- HEAD funcional verificado: [077589b](https://github.com/eamigo86/dj-hyperview/commit/077589b).
- Esta carpeta: vista mantenible del plan y sus decisiones.
- Tests y workflows: evidencia ejecutable y contratos fail-closed.
- Engram: trazabilidad SDD adicional; no reemplaza el estado versionado aquí.

## Regla de mantenimiento

Un cambio sólo queda **Verificado** tras revisión independiente. Debe actualizar
su test, implementación y documentación en el mismo work unit. El legacy nunca
cambia el estado del paquete.
