import { Module } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { Prisma } from './prisma.service';
import { AuthService } from './auth.service';
import { Motor } from './engine.service';
import { Auditoria } from './audit.service';
import { GuardiaAuth } from './rbac';
import {
  AuthController, OrgsController, GrafosController, RunsController,
} from './controllers';

@Module({
  controllers: [AuthController, OrgsController, GrafosController, RunsController],
  providers: [
    Prisma, AuthService, Motor, Auditoria,
    // Guardia global: una ruta nueva nace protegida y hay que marcarla
    // `@Publico()` a proposito para abrirla. Al reves —proteger cada ruta a
    // mano— la que se olvida queda abierta, y eso ejecuta agentes con shell.
    { provide: APP_GUARD, useClass: GuardiaAuth },
  ],
})
export class AppModule {}
