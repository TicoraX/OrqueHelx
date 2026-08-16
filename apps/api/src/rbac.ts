import {
  CanActivate, ExecutionContext, Injectable, SetMetadata,
  UnauthorizedException, ForbiddenException, NotFoundException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { Rol } from '@prisma/client';
import { AuthService } from './auth.service';
import { Prisma } from './prisma.service';

export const ROL_MINIMO = 'rol_minimo';
export const RequiereRol = (r: Rol) => SetMetadata(ROL_MINIMO, r);
export const Publico = () => SetMetadata('publico', true);

// Orden de poder. Un OWNER puede lo de un EDITOR, y un EDITOR lo de un VIEWER.
const PODER: Record<Rol, number> = { VIEWER: 0, EDITOR: 1, OWNER: 2 };

@Injectable()
export class GuardiaAuth implements CanActivate {
  constructor(
    private readonly auth: AuthService,
    private readonly db: Prisma,
    private readonly reflector: Reflector,
  ) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    const req = ctx.switchToHttp().getRequest();
    if (this.reflector.getAllAndOverride<boolean>('publico',
        [ctx.getHandler(), ctx.getClass()])) {
      return true;
    }

    const cab: string = req.headers['authorization'] || '';
    const user = await this.auth.usuarioDe(cab.replace(/^Bearer\s+/i, ''));
    if (!user) throw new UnauthorizedException('sesion invalida');
    req.user = user;

    const minimo = this.reflector.getAllAndOverride<Rol>(ROL_MINIMO,
      [ctx.getHandler(), ctx.getClass()]);
    if (!minimo) return true;               // autenticado alcanza

    const orgId = req.params?.orgId ?? req.body?.orgId;
    if (!orgId) throw new NotFoundException('falta la organizacion');

    const m = await this.db.membership.findUnique({
      where: { userId_orgId: { userId: user.id, orgId } },
    });
    // 404 y no 403 cuando no es miembro: un 403 confirma que esa org existe.
    if (!m) throw new NotFoundException('organizacion no encontrada');
    if (PODER[m.rol] < PODER[minimo]) {
      throw new ForbiddenException(`requiere rol ${minimo}, tenes ${m.rol}`);
    }
    req.membresia = m;
    return true;
  }
}
