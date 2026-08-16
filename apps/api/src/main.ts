import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function main() {
  const app = await NestFactory.create(AppModule, { logger: ['error', 'warn'] });
  app.setGlobalPrefix('api');

  // CORS solo para el origen del Studio. `*` no sirve igual porque el token va
  // en una cabecera y no en una cookie, pero acotarlo evita que una pagina
  // cualquiera hable con esta API desde el navegador de un usuario logueado.
  app.enableCors({
    origin: process.env.ORQUESTER_WEB_ORIGIN || 'http://127.0.0.1:8765',
    allowedHeaders: ['Content-Type', 'Authorization'],
  });

  const puerto = Number(process.env.PORT || 3000);
  const host = process.env.ORQUESTER_API_HOST || '127.0.0.1';
  await app.listen(puerto, host);
  console.log(`API de ORQUESTER en http://${host}:${puerto}/api`);
  if (!process.env.DATABASE_URL) {
    console.log('  AVISO: sin DATABASE_URL; Prisma va a fallar al primer query.');
  }
}

main();
