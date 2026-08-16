import { Injectable, BadGatewayException } from '@nestjs/common';

// El motor sigue siendo Python (ARQUITECTURA.md §12): compila, despacha y
// exporta MCP. NestJS no reimplementa nada de eso — lo llama.
//
// Por que no reescribirlo en TS: el loop es todo `kanban_db`, y rehacer el
// protocolo de claim contra la misma SQLite es exactamente el tipo de cosa que
// la tabla de §10 muestra que sale mal.
@Injectable()
export class Motor {
  private readonly base = process.env.ORQUESTER_ENGINE_URL || 'http://127.0.0.1:8765';
  private readonly token = process.env.ORQUESTER_TOKEN || '';

  private async pedir(ruta: string, cuerpo?: any) {
    let r: Response;
    try {
      r = await fetch(this.base + ruta, {
        method: cuerpo ? 'POST' : 'GET',
        headers: {
          'X-Orquester-Token': this.token,
          ...(cuerpo ? { 'Content-Type': 'application/json' } : {}),
        },
        body: cuerpo ? JSON.stringify(cuerpo) : undefined,
      });
    } catch (e: any) {
      throw new BadGatewayException('el motor no responde en ' + this.base + ': ' + e.message);
    }
    const datos: any = await r.json().catch(() => ({}));
    if (!r.ok) {
      // El mensaje del motor viaja tal cual: los errores del compilador nombran
      // el nodo culpable y esa precision se pierde si se la reemplaza por
      // "error interno".
      throw new BadGatewayException(datos.error || 'el motor respondio ' + r.status);
    }
    return datos;
  }

  validar(grafo: any) { return this.pedir('/api/validar', grafo); }
  compilar(grafo: any) { return this.pedir('/api/compilar', grafo); }
  ejecutar(board: string) { return this.pedir('/api/correr', { board }); }
  estado(board: string) { return this.pedir('/api/estado?board=' + encodeURIComponent(board)); }
  consumo(board: string) { return this.pedir('/api/consumo?board=' + encodeURIComponent(board)); }
}
