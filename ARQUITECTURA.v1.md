# ORQUESTER — Arquitectura del Sistema y Estructura Técnica

## 1. Visión General de la Arquitectura

El sistema se compone de una aplicación cliente en React (Studio visual y Dashboard) y un backend en NestJS encargado del motor de orquestación, gestión de adaptadores de sub-agentes y síntesis de contextos.


+--------------------------------------------------------------------+
|                         FRONTEND (React)                           |
|  - Studio (React Flow)             - Observabilidad & Logs SSE     |
|  - Dashboard de Métricas           - Catálogo de Agentes/Tools     |
+--------------------------------------------------------------------+
|
HTTP REST / WebSockets / SSE
v
+--------------------------------------------------------------------+
|                         BACKEND (NestJS)                           |
|                                                                    |
|  +-----------------------+     +--------------------------------+  |
|  | Orchestration Engine  | <-> | Context & Compression Engine   |  |
|  | (Claude Main Agent)   |     | (Diff Extractor / Summarizer)  |  |
|  +-----------------------+     +--------------------------------+  |
|              |                                                     |
|              v                                                     |
|  +--------------------------------------------------------------+  |
|  | Adapter Broker & MCP Bridge                                  |  |
|  +--------------------------------------------------------------+  |
+--------------------------------------------------------------------+
|                            |                            |
v                            v                            v
+----------------+           +----------------+           +----------------+
| Sub-Agente A   |           | Sub-Agente B   |           | Herramientas   |
| (Opencode CLI) |           | (Antigravity)  |           | (APIs / DB)    |
+----------------+           +----------------+           +----------------+




---

## 2. Stack Tecnológico

### Backend Core
* **Framework:** NestJS (Node.js + TypeScript).
* **Protocolo de Interoperabilidad:** `@modelcontextprotocol/sdk` (MCP Server/Client).
* **Gestión de Colas y Tareas Asíncronas:** BullMQ + Redis (para ejecuciones de sub-agentes de larga duración).
* **Persistencia de Datos:** PostgreSQL con Prisma ORM.
* **Comunicación en Tiempo Real:** Server-Sent Events (SSE) y WebSockets (Socket.io).

### Frontend Studio
* **Framework:** React con TypeScript (Vite).
* **Librería de Canvas Visual:** React Flow (`@xyflow/react`).
* **Gestión de Estado:** Zustand (Estado local e interactivo del canvas) + TanStack Query (Estado asíncrono y llamadas API).
* **Sistema de Estilos:** Tailwind CSS con tokens de color configurados en OKLCH.

---

## 3. Estructura del Monorepo / Proyecto

```text
orquester/
├── apps/
│   ├── api/                    # NestJS Application
│   │   ├── src/
│   │   │   ├── modules/
│   │   │   │   ├── orchestrator/ # Motor del agente principal (Claude)
│   │   │   │   ├── agents/       # Registro y configuración de agentes
│   │   │   │   ├── adapters/     # Conectores a sub-agentes (CLI, HTTP, MCP)
│   │   │   │   ├── context/      # Compresión, filtros y resúmenes de contexto
│   │   │   │   ├── telemetry/    # Rastreo con OpenTelemetry y métricas
│   │   │   │   └── workflows/    # Motor de ejecución de grafos
│   │   │   ├── common/           # Interceptores, guardias y utilidades
│   │   │   └── main.ts
│   │   └── test/
│   │
│   └── web/                    # React Frontend
│       ├── src/
│       │   ├── components/
│       │   │   ├── studio/       # Canvas con React Flow
│       │   │   ├── nodes/        # Tipos de nodos (AgentNode, ToolNode, TriggerNode)
│       │   │   ├── dashboard/    # Métricas de rendimiento y tokens
│       │   │   └── ui/           # Componentes base
│       │   ├── stores/           # Zustand state management
│       │   ├── hooks/
│       │   └── pages/
│       └── vite.config.ts
│
└── packages/
    ├── core/                   # Tipos e interfaces compartidas (DTOs, Schemas)
    └── mcp-exporter/           # Lógica para exportar workflows como servidores MCP







    4. Patrón de Diseño para Adaptadores de Sub-Agentes
Cada sub-agente que ORQUESTER ejecuta implementa una interfaz unificada:

TypeScript
export interface AgentAdapterInput {
  prompt: string;
  parameters?: Record<string, any>;
  workingDirectory?: string;
}

export interface AgentAdapterOutput {
  status: 'success' | 'failure';
  summary: string;
  rawLogsRef?: string;
  data?: Record<string, any>;
}

export interface IAgentAdapter {
  id: string;
  name: string;
  execute(input: AgentAdapterInput): Promise<AgentAdapterOutput>;
}


5. Ciclo de Vida de Ejecución de Tareas
Recepción: El usuario envía un objetivo al Orchestration Engine.

Evaluación de Herramientas: El agente principal analiza las herramientas/sub-agentes disponibles registrados en el flujo.

Despacho: Si se decide usar un sub-agente, se emite un evento a la cola de BullMQ.

Ejecución Aisada: El adaptador correspondiente (CLIAdapter, HTTPAdapter, etc.) ejecuta el proceso de forma asíncrona.

Síntesis: El resultado pasa por el Context Engine, que trunca logs masivos, genera resúmenes y emite la respuesta sintetizada.

Retorno: El Orchestration Engine recibe el resultado resumido y continúa la iteración o finaliza el flujo.