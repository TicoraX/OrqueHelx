import { Injectable } from '@nestjs/common';
import { Prisma } from './prisma.service';

// Append-only por diseño: este servicio no expone update ni delete.
// "El pasado no se reescribe".
@Injectable()
export class Auditoria {
  constructor(private readonly db: Prisma) {}

  async registrar(accion: string, opts: {
    orgId?: string; actorId?: string; recurso?: string; detalle?: any;
  } = {}) {
    await this.db.auditLog.create({
      data: {
        accion,
        orgId: opts.orgId ?? null,
        actorId: opts.actorId ?? null,
        recurso: opts.recurso ?? null,
        detalle: opts.detalle ?? undefined,
      },
    }).catch(() => {
      // La auditoria no puede tumbar la operacion que audita. Se pierde el
      // registro, no el trabajo del usuario.
    });
  }

  listar(orgId: string, limite = 100) {
    return this.db.auditLog.findMany({
      where: { orgId }, orderBy: { creadoEn: 'desc' }, take: limite,
      include: { actor: { select: { email: true } } },
    });
  }
}
