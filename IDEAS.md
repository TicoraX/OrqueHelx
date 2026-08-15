# ORQUESTER — Documento de Visión y Especificaciones de Producto

ORQUESTER es el entorno de diseño, gobierno y distribución de sistemas multi-agente jerárquicos. Diseñas el flujo en un canvas visual, lo ejecutas sobre un runtime de agentes probado, ves las trazas en vivo y lo publicas como servidor MCP para que cualquier cliente lo consuma.

La ejecución la presta **Hermes Agent** (Nous Research, MIT). Ver `ARQUITECTURA.md` §1 para el reparto de responsabilidades y §10 para el estado de verificación.

---

## 1. Núcleo Diferenciador: del Motor al Control

El aislamiento y la compresión de contexto **dejaron de ser diferenciador**. Hermes los publicó bajo MIT y los tiene en producción: subagentes con conversación fresca por tarea, filtro que devuelve al padre solo la llamada y el resumen, `output_schema` validado con jsonschema y presupuesto de contexto repartido entre los hijos de un fan-out. Reconstruir eso es competir contra código gratuito y probado.

Lo que nadie construyó sobre ese runtime, y es lo que ORQUESTER aporta:

* **Diseño visual del flujo (Studio):** Canvas drag-and-drop con dependencias explícitas entre nodos. Hermes tiene un scheduler de DAG durable, pero se opera por CLI y SQLite: no hay forma de dibujarlo.
* **Distribución como MCP:** Un flujo diseñado se publica como servidor MCP parametrizable. El MCP que trae Hermes expone conversaciones de mensajería, no ejecución de flujos: quien quiera invocar un pipeline desde Claude Desktop o Cursor hoy no tiene cómo.
* **Gobierno de equipo:** Catálogo versionado de agentes, RBAC y SSO. Hermes es un agente personal; asume un solo dueño.
* **Observabilidad como grafo:** Las trazas de ACP y los eventos del board pintados sobre el mismo canvas donde diseñaste el flujo. Ves qué nodo se atascó en el dibujo que ya conoces, sin traducir un log a un grafo mental.

**Control de granularidad** sigue en pie como función de ORQUESTER, pero se implementa configurando lo que Hermes ya expone (`delegation.max_spawn_depth`, `max_concurrent_children`, `output_schema` por nodo), no escribiendo un compresor propio.

**Los sub-agentes especializados siguen siendo el corazón del producto.** Cada nodo del flujo elige su ejecutor: Claude Code, OpenCode, Antigravity, Codex, un modelo local o el propio Hermes. Hermes trae las skills para todos ellos y homogeneiza sus salidas contra el mismo contrato, así que un flujo mixto se diseña igual que uno homogéneo. Ver `ARQUITECTURA.md` §4.1.

---

## 2. Módulos y Funcionalidades del Producto

> Varios de estos módulos ya existen en el runtime y ORQUESTER solo los configura y los expone: cron y triggers, guardrails de validación, human-in-the-loop, gestión multi-proveedor con fallback, y métricas de tokens y costo. La lista de quién construye qué está en `ARQUITECTURA.md` §1 y §6.

### A. Build & Integrate (Construcción e Integración)
* **Studio (Visual Node Editor):** Interfaz gráfica drag-and-drop para diseñar flujos de agentes, definir dependencias, herramientas disponibles y condiciones de bifurcación.
* **Export como Servidor MCP:** Cualquier flujo o jerarquía definida en ORQUESTER se puede exportar e invocar como un servidor MCP (Model Context Protocol) listo para usarse en Claude Desktop, Cursor o cualquier cliente compatible.
* **Export como Componente UI:** Generación de widgets interactivos (React Component) embebibles en aplicaciones externas para interactuar con los flujos.
* **Private Agent & Tools Repository:** Catálogo interno para registrar, versiónar y reutilizar sub-agentes preconfigurados y herramientas personalizadas.
* **Plantillas de Workflow Agentic:** Plantillas preconstruidas para casos de uso comunes (ej. Refactorización de código, QA automatizado, investigación web multi-fuente).
* **Integración con GitHub:** Sincronización directa con repositorios para leer/escribir código y gestionar Pull Requests.

### B. Observe & Optimize (Observabilidad y Optimización)
* **Trazabilidad en Tiempo Real (Tracing & OpenTelemetry):** Grafo interactivo que muestra las invocaciones entre agentes, tiempos de respuesta y flujo de mensajes.
* **Métricas de Consumo:** Panel con desglose de tokens consumidos por nivel de la jerarquía, latencia acumulada y costos aproximados.
* **Hallucination & Error Guardrails:** Capa de validación intermedia que analiza las respuestas de los sub-agentes mediante reglas de validación (JSON Schema, análisis estático) antes de retornar al orquestador.
* **Human-in-the-Loop:** Capa de aprobación humana que pausará el flujo en nodos críticos antes de ejecutar acciones destructivas (ej. modificar producción o ejecutar comandos de terminal).

### C. Manage & Scale (Gestión y Escalabilidad)
* **Programación Cron / Disparadores (Triggers):** Ejecución programada de flujos de agentes o activación mediante Webhooks.
* **Gestión de Proveedores LLM:** Soporte multi-proveedor (Anthropic, OpenAI, Ollama, vLLM, OpenRouter) con fallback automático si una API falla.
* **Control de Acceso (RBAC y SSO):** Gestión de permisos por roles para entornos de equipo enterprise.