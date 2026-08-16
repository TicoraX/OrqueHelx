import {
  Injectable, UnauthorizedException, ConflictException, BadRequestException,
} from '@nestjs/common';
import { randomBytes, createHash, timingSafeEqual } from 'node:crypto';
import * as argon2 from 'argon2';
import { Prisma } from './prisma.service';

// El token de sesion se guarda **hasheado**. Si alguien lee la base, no se
// lleva sesiones usables — mismo criterio que con la contraseña.
const hashToken = (t: string) => createHash('sha256').update(t).digest('hex');
const DIAS = 7;

@Injectable()
export class AuthService {
  constructor(private readonly db: Prisma) {}

  async registrar(email: string, password: string, nombre?: string) {
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

  async login(email: string, password: string) {
    email = (email || '').trim().toLowerCase();
    const user = await this.db.user.findUnique({ where: { email } });
    // Se verifica igual cuando el usuario no existe, con un hash señuelo, para
    // que "email inexistente" y "clave mala" tarden lo mismo. Sin esto, el
    // tiempo de respuesta enumera usuarios.
    const hash = user?.passwordHash ??
      '$argon2id$v=19$m=65536,t=3,p=4$c2XcnyTPZ4YQyz4bfxvbHA$Ky3kO0GVJYQKqmM6bZ0mYQZ7GxDkFmVQBEbLBs9wYUE';
    const ok = await argon2.verify(hash, password || '').catch(() => false);
    if (!ok || !user) throw new UnauthorizedException('credenciales invalidas');

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

// Comparacion en tiempo constante para secretos que no pasan por argon2
// (el token interno del motor).
export function igualSeguro(a: string, b: string) {
  const x = Buffer.from(a || '');
  const y = Buffer.from(b || '');
  return x.length === y.length && timingSafeEqual(x, y);
}
