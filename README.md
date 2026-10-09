# Dromax · Asistente de Compras IA (Odoo 17)

Módulo de Odoo 17 que arma, en un chat, una propuesta de compra semanal por
laboratorio y, con el visto bueno del comprador, la convierte en solicitudes de
presupuesto (`purchase.order`) en borrador.

## Principios

- **Reactivo, no autónomo**: el comprador dispara el análisis con preguntas
  predefinidas; el cron del lunes solo precalcula.
- **Solo lectura** sobre Postgres: SQL validado (una sola sentencia
  `SELECT`/`WITH … SELECT`, lista blanca de tablas y vistas) ejecutado con el
  rol `dromax_ai_ro`.
- **Única escritura**: `purchase.order` en borrador, creada por un botón, nunca
  por el modelo.
- **Acceso restringido** al grupo *Asistente de Compras IA / Usuario*.

## Plan

| Fase | Contenido |
|------|-----------|
| 0 — Preparación | Módulo FTP de droguerías, inventario de farmacias, rol `dromax_ai_ro`, grupo de acceso |
| 1 — Asistente de análisis | Vistas `ai_v_rotacion`, `ai_v_stock`, `ai_v_pendiente`; validador SQL y `run_readonly_sql`; niveles de modelo y `ai.usage.log`; reporte de códigos de barras |
| 2 — Agente de compras | Vistas `ai_v_oferta`, `ai_v_faltantes`; `ai.purchase.proposal` y cron; botones, ajuste en el chat y generación de borrador |
| 3 — Contexto externo | Flujo dinámico con búsqueda web |

## Configuración

Las llaves nunca van en el repositorio. Copia `.env.example` a `.env` o usa
`ir.config_parameter` (Ajustes → Técnico → Parámetros del sistema).
