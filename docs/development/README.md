# Plan de desarrollo de dj-hyperview

Esta carpeta explica qué construye el paquete, qué está verificado y qué falta. El estado correcto al 4 de septiembre de 2026 es: **Tasks 1–12 verificadas en el repositorio del paquete; Task 13 pendiente**.

> El proyecto legacy es material de consulta de solo lectura. No es el destino de implementación, no define el contrato público y sus datos no forman parte del paquete.

## Ruta de lectura

1. [Estado actual](status.md): punto exacto, evidencia y riesgos abiertos.
2. [Roadmap](roadmap.md): secuencia corregida de Tasks 1–13.
3. [Especificación](specification.md): comportamiento que debe ofrecer la versión inicial.
4. [Arquitectura](architecture.md): componentes, flujos y límites técnicos.
5. [Tareas](tasks.md): historial detallado de Tasks 1–12 y criterios futuros.
6. [Contrato Hyperview 0.110.0](../hyperview-0.110.0.md): alcance validado y límites.
7. [Decisiones](decisions.md): decisiones de arquitectura y sus tradeoffs.

## Leyenda de estado

| Estado | Significado |
|---|---|
| **Verificado** | Implementado en `dj-hyperview` y aprobado mediante pruebas independientes. |
| **Implementado, no verificado** | Código existente pendiente de un gate independiente; actualmente no hay Tasks 1–12 en este estado. |
| **Pendiente** | Trabajo que todavía no forma parte del entregable válido. |
| **Post-MVP** | Deliberadamente aplazado para una versión posterior. |
| **Histórico no válido** | Experimento fuera del repositorio del paquete; aporta aprendizajes, pero no cuenta como avance. |

## Fuentes de verdad

- Código y Git del paquete: [eamigo86/dj-hyperview](https://github.com/eamigo86/dj-hyperview).
- HEAD verificado del paquete: [`84fc76d`](https://github.com/eamigo86/dj-hyperview/commit/84fc76d).
- Esta documentación: vista mantenible del plan corregido.
- Pruebas del paquete: evidencia ejecutable del contrato.
- Artefactos SDD históricos de Engram: trazabilidad adicional, no sustituyen el estado corregido de esta carpeta.

## Regla de mantenimiento

Al cerrar una Task se debe actualizar, en el mismo work unit, [tasks.md](tasks.md), [status.md](status.md) y cualquier decisión afectada. Una Task sólo cambia a **Verificado** después de una revisión independiente; un experimento en el proyecto legacy nunca cambia ese estado.
