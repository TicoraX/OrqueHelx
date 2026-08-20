import {
  Body, Controller, Get, Param, Post, Req, Headers, BadRequestException,
} from '@nestjs/common';
import { Rol } from '@prisma/client';
import { AuthService } from './auth.service';
import { Prisma } from './prisma.service';
import { Motor } from './engine.service';
import { Auditoria } from './audit.service';
import { Publico, RequiereRol } from './rbac';

const slugificar = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

@Controller('auth')
export class AuthController {
  constructor(private readonly auth: AuthService, private readonly audit: Auditoria) {}

  // `req.ip` es la clave del freno de fuerza bruta (ver `AuthService.frenar`).
  @Publico() @Post('registro')
  async registro(@Req() req: any, @Body() b: any) {
    const u = await this.auth.registrar(b?.email, b?.password, b?.nombre, req.ip);
    await this.audit.registrar('auth.registro', { actorId: u.id });
    return u;
  }

  @Publico() @Post('login')
  login(@Req() req: any, @Body() b: any) {
    return this.auth.login(b?.email, b?.password, req.ip);
  }

  @Post('logout')
  async logout(@Headers('authorization') cab: string) {
    await this.auth.logout((cab || '').replace(/^Bearer\s+/i, ''));
    return { ok: true };
  }

  @Get('yo')
  yo(@Req() req: any) {
    return { id: req.user.id, email: req.user.email, nombre: req.user.nombre };
  }
}

@Controller('orgs')
export class OrgsController {
  constructor(private readonly db: Prisma, private readonly audit: Auditoria) {}

  // Crear una org no pide rol: el que la crea queda OWNER.
  @Post()
  async crear(@Req() req: any, @Body() b: any) {
    const nombre = (b?.nombre || '').trim();
    if (!nombre) throw new BadRequestException('falta el nombre');
    const slug = slugificar(nombre) + '-' + Math.random().toString(36).slice(2, 7);
    const org = await this.db.org.create({
      data: { nombre, slug, membresias: { create: { userId: req.user.id, rol: Rol.OWNER } } },
    });
    await this.audit.registrar('org.crear', { orgId: org.id, actorId: req.user.id });
    return org;
  }

  @Get()
  async mias(@Req() req: any) {
    const ms = await this.db.membership.findMany({
      where: { userId: req.user.id }, include: { org: true },
    });
    return ms.map(m => ({ ...m.org, rol: m.rol }));
  }

  @RequiereRol(Rol.OWNER) @Post(':orgId/miembros')
  async invitar(@Req() req: any, @Param('orgId') orgId: string, @Body() b: any) {
    const email = (b?.email || '').trim().toLowerCase();
    const rol: Rol = b?.rol || Rol.VIEWER;
    if (!Object.values(Rol).includes(rol)) throw new BadRequestException('rol invalido');
    const user = await this.db.user.findUnique({ where: { email } });
    if (!user) throw new BadRequestException('ese usuario no existe todavia');
    const m = await this.db.membership.upsert({
      where: { userId_orgId: { userId: user.id, orgId } },
      update: { rol }, create: { userId: user.id, orgId, rol },
    });
    await this.audit.registrar('miembro.invitar', {
      orgId, actorId: req.user.id, recurso: user.id, detalle: { rol },
    });
    return m;
  }

  @RequiereRol(Rol.VIEWER) @Get(':orgId/auditoria')
  auditoria(@Param('orgId') orgId: string) { return this.audit.listar(orgId); }
}

@Controller('orgs/:orgId/grafos')
export class GrafosController {
  constructor(
    private readonly db: Prisma, private readonly motor: Motor,
    private readonly audit: Auditoria,
  ) {}

  @RequiereRol(Rol.VIEWER) @Get()
  listar(@Param('orgId') orgId: string) {
    return this.db.graph.findMany({
      where: { orgId },
      include: { versiones: { orderBy: { numero: 'desc' }, take: 1 } },
    });
  }

  @RequiereRol(Rol.EDITOR) @Post()
  async crear(@Req() req: any, @Param('orgId') orgId: string, @Body() b: any) {
    const nombre = (b?.nombre || '').trim();
    const spec = b?.spec;
    if (!nombre || !spec) throw new BadRequestException('faltan nombre o spec');
    await this.motor.validar(spec);           // no se guarda un grafo invalido
    const g = await this.db.graph.create({
      data: {
        orgId, nombre, slug: slugificar(nombre),
        versiones: { create: { numero: 1, spec, autorId: req.user.id, mensaje: b?.mensaje } },
      },
      include: { versiones: true },
    });
    await this.audit.registrar('graph.crear', { orgId, actorId: req.user.id, recurso: g.id });
    return g;
  }

  // Editar NO pisa: crea una version nueva. Una corrida vieja tiene que poder
  // explicarse con el grafo que de verdad se ejecuto.
  @RequiereRol(Rol.EDITOR) @Post(':graphId/versiones')
  async versionar(@Req() req: any, @Param('orgId') orgId: string,
                  @Param('graphId') graphId: string, @Body() b: any) {
    if (!b?.spec) throw new BadRequestException('falta el spec');
    await this.motor.validar(b.spec);
    const ultima = await this.db.graphVersion.findFirst({
      where: { graphId }, orderBy: { numero: 'desc' },
    });
    const v = await this.db.graphVersion.create({
      data: {
        graphId, numero: (ultima?.numero ?? 0) + 1, spec: b.spec,
        autorId: req.user.id, mensaje: b?.mensaje,
      },
    });
    await this.audit.registrar('graph.versionar', {
      orgId, actorId: req.user.id, recurso: graphId, detalle: { numero: v.numero },
    });
    return v;
  }

  @RequiereRol(Rol.VIEWER) @Get(':graphId/versiones')
  versiones(@Param('graphId') graphId: string) {
    return this.db.graphVersion.findMany({
      where: { graphId }, orderBy: { numero: 'desc' },
      include: { autor: { select: { email: true } } },
    });
  }

  // Ejecutar exige EDITOR: un VIEWER mira, no gasta cuota ni corre shell.
  @RequiereRol(Rol.EDITOR) @Post(':graphId/ejecutar')
  async ejecutar(@Req() req: any, @Param('orgId') orgId: string,
                 @Param('graphId') graphId: string, @Body() b: any) {
    const v = b?.versionId
      ? await this.db.graphVersion.findUnique({ where: { id: b.versionId } })
      : await this.db.graphVersion.findFirst({
          where: { graphId }, orderBy: { numero: 'desc' } });
    if (!v || v.graphId !== graphId) throw new BadRequestException('version invalida');

    const spec: any = { ...(v.spec as any) };
    // Board propio por corrida: dos ejecuciones del mismo flujo no se pisan.
    spec.board = 'org-' + orgId.slice(0, 8) + '-' + graphId.slice(0, 8) + '-' + (Date.now() % 1e7);
    await this.motor.compilar(spec);
    await this.motor.ejecutar(spec.board);

    const run = await this.db.run.create({
      data: {
        graphId, versionId: v.id, board: spec.board,
        lanzadaPor: req.user.id, parametros: b?.parametros ?? undefined,
      },
    });
    await this.audit.registrar('run.iniciar', {
      orgId, actorId: req.user.id, recurso: run.id, detalle: { board: spec.board },
    });
    return run;
  }

  @RequiereRol(Rol.VIEWER) @Get(':graphId/runs')
  runs(@Param('graphId') graphId: string) {
    return this.db.run.findMany({
      where: { graphId }, orderBy: { creadoEn: 'desc' }, take: 50,
      include: { usuario: { select: { email: true } }, version: { select: { numero: true } } },
    });
  }
}

@Controller('runs')
export class RunsController {
  constructor(private readonly db: Prisma, private readonly motor: Motor) {}

  // El estado vivo lo tiene el kanban; Postgres solo sabe de quien es la
  // corrida. Se juntan las dos cosas aca, tras verificar la pertenencia.
  @Get(':runId')
  async ver(@Req() req: any, @Param('runId') runId: string) {
    const run = await this.db.run.findUnique({
      where: { id: runId }, include: { graph: true },
    });
    // Mismo error para "no existe" y "no sos miembro": si fueran distintos, el
    // que prueba ids aprende cuales existen.
    const noExiste = new BadRequestException('no existe');
    if (!run) throw noExiste;
    const m = await this.db.membership.findUnique({
      where: { userId_orgId: { userId: req.user.id, orgId: run.graph.orgId } },
    });
    if (!m) throw noExiste;
    const [estado, consumo] = await Promise.all([
      this.motor.estado(run.board).catch(() => ({ tareas: {} })),
      this.motor.consumo(run.board).catch(() => ({})),
    ]);
    return { run, estado, consumo };
  }
}
