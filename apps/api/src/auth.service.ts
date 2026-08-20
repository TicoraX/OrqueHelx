import {
  Injectable, UnauthorizedException, ConflictException, BadRequestException,
  HttpException, HttpStatus,
} from '@nestjs/common';
import { randomBytes, createHash } from 'node:crypto';
import * as argon2 from 'argon2';
import { Prisma } from './prisma.service';

// El token de sesion se guarda **hasheado**. Si alguien lee la base, no se
// lleva sesiones usables — mismo criterio que con la contraseña.
const hashToken = (t: string) => createHash('sha256').update(t).digest('hex');
const DIAS = 7;

// Freno de fuerza bruta. Vive ACA y no en `GuardiaAuth` por dos motivos:
// el guard hace `return true` por `@Publico()` antes de mirar nada, y login y
// registro son justamente las publicas; y solo el servicio sabe si el intento
// FALLO — un login que anda no tiene por que gastar cuota.
// Sin `@nestjs/throttler`: es una dependencia nueva para veinte lineas.
const VENTANA_MS = 15 * 60 * 1000;
const MAX_FALLOS = 10;
// Techo duro del mapa: con IPv6 el espacio de claves es infinito y un atacante
// lo llena a voluntad. Un rate limiter que se come la RAM ES el DoS que evita.
const MAX_CLAVES = 10_000;

@Injectable()
export class AuthService {
  constructor(private readonly db: Prisma) {}

  private readonly fallos = new Map<string, number[]>();

  /** Rechaza si esta clave ya agoto sus intentos fallidos en la ventana.
   *
   * Ojo con la clave: `req.ip` es el peer del socket salvo que se active
   * `trust proxy`. Detras de un proxy TODOS comparten IP y veinte intentos
   * bloquean a todo el mundo; con `trust proxy` a ciegas, `X-Forwarded-For` es
   * falsificable y el limite se evade con una cabecera. Se deja explicito:
   * esta API escucha en 127.0.0.1 y NO confia en proxies. Si algun dia va
   * detras de uno, hay que decidirlo aca a proposito.
   */
  private frenar(clave: string) {
    const ahora = Date.now();
    const previos = (this.fallos.get(clave) || []).filter(t => ahora - t < VENTANA_MS);
    if (previos.length) this.fallos.set(clave, previos);
    else this.fallos.delete(clave);
    if (previos.length >= MAX_FALLOS) {
      throw new HttpException('demasiados intentos, probá en unos minutos',
                              HttpStatus.TOO_MANY_REQUESTS);
    }
  }

  private anotarFallo(clave: string) {
    // Poda antes de insertar: sin esto el mapa solo crece.
    if (this.fallos.size >= MAX_CLAVES) {
      const corte = Date.now() - VENTANA_MS;
      for (const [k, ts] of this.fallos) {
        if (!ts.some(t => t > corte)) this.fallos.delete(k);
      }
      if (this.fallos.size >= MAX_CLAVES) this.fallos.clear();
    }
    const previos = this.fallos.get(clave) || [];
    this.fallos.set(clave, [...previos, Date.now()]);
  }

  async registrar(email: string, password: string, nombre?: string, ip = 'desconocida') {
    // En registro se cuenta CADA intento, no solo los fallidos: el abuso aca es
    // la creacion masiva de cuentas, que sale bien. Diez por IP cada 15 min.
    this.frenar(`registro:${ip}`);
    this.anotarFallo(`registro:${ip}`);
    email = (email || '').trim().toLowerCase();
    if (!email.includes('@')) throw new BadRequestException('email invalido');
    // 12 y no 8: esto ejecuta agentes con shell en la maquina del servidor.
    if ((password || '').length < 12) {
      throw new BadRequestException('la contraseña necesita al menos 12 caracteres');
    }
    if (await this.db.user.findUnique({ where: { email } })) {
      throw new ConflictException('ese email ya existe');
    }
    const passwordHash = await argon2.hash(password, { type: argon2.argon2id });
    const user = await this.db.user.create({ data: { email, passwordHash, nombre } });
    return { id: user.id, email: user.email };
  }

  async login(email: string, password: string, ip = 'desconocida') {
    const clave = `login:${ip}`;
    this.frenar(clave);
    email = (email || '').trim().toLowerCase();
    const user = await this.db.user.findUnique({ where: { email } });
    // Se verifica igual cuando el usuario no existe, con un hash señuelo, para
    // que "email inexistente" y "clave mala" tarden lo mismo. Sin esto, el
    // tiempo de respuesta enumera usuarios.
    const hash = user?.passwordHash ??
      '$argon2id$v=19$m=65536,t=3,p=4$c2XcnyTPZ4YQyz4bfxvbHA$Ky3kO0GVJYQKqmM6bZ0mYQZ7GxDkFmVQBEbLBs9wYUE';
    const ok = await argon2.verify(hash, password || '').catch(() => false);
    if (!ok || !user) {
      this.anotarFallo(clave);
      throw new UnauthorizedException('credenciales invalidas');
    }

    const token = randomBytes(32).toString('base64url');
    const expiraEn = new Date(Date.now() + DIAS * 864e5);
    await this.db.session.create({
      data: { tokenHash: hashToken(token), userId: user.id, expiraEn },
    });
    return { token, expiraEn };
  }

  async usuarioDe(token: string) {
    if (!token) return null;
    const s = await this.db.session.findUnique({
      where: { tokenHash: hashToken(token) },
      include: { user: true },
    });
    if (!s) return null;
    if (s.expiraEn < new Date()) {
      await this.db.session.delete({ where: { id: s.id } }).catch(() => {});
      return null;
    }
    return s.user;
  }

  async logout(token: string) {
    await this.db.session
      .delete({ where: { tokenHash: hashToken(token) } })
      .catch(() => {});
  }
}
