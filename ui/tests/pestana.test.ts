import { describe, expect, it, vi } from "vitest";
import { activa, marcar, suscribir } from "../src/pestana";

describe("pestana activa", () => {
	it("empieza inactiva y avisa a los suscriptores al cambiar", () => {
		expect(activa()).toBe(false);
		const aviso = vi.fn();
		const desuscribir = suscribir(aviso);
		marcar(true);
		expect(activa()).toBe(true);
		expect(aviso).toHaveBeenCalledTimes(1);
		desuscribir();
		marcar(false);
		expect(activa()).toBe(false);
		expect(aviso).toHaveBeenCalledTimes(1);
	});

	it("marcar el mismo valor no avisa", () => {
		const aviso = vi.fn();
		suscribir(aviso);
		marcar(false);
		expect(aviso).not.toHaveBeenCalled();
	});
});
