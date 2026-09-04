// Verificacion de la UI en un navegador de verdad.
//
// La suite de Python NO toca `ui/index.html`: los cambios de interfaz se
// verifican aca (ARQUITECTURA/ESTADO lo tienen como decision). Hasta hoy eso
// era una intencion sin archivo; este es el archivo.
//
// Lo arranca `tests/test_ui_navegador.py`, que levanta el Studio y lo omite si
// falta playwright. Las respuestas del disenador con IA van MOCKEADAS con
// `p.route`: lo que se prueba es el cableado de la UI, no el modelo, y una
// suite que llama a un agente de verdad cuesta plata en cada corrida.
//
//   node tests/ui_navegador.mjs <url> <png-de-salida>

import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';

// Las respuestas mockeadas viven en un JSON compartido con la suite de Python.
// Antes estaban escritas aca adentro y el comentario decia "con la forma REAL":
// una intencion que nada verificaba. `tests/test_contrato_ui.py` las compara
// ahora contra lo que el Studio devuelve de verdad, asi que un mock que se
// quede viejo falla en vez de hacer pasar a la UI contra una fantasia.
const FIJAS = JSON.parse(readFileSync(
  new URL('./fixtures/respuestas_ui.json', import.meta.url), 'utf-8'));
const servir = cuerpo => r => r.fulfill({
  status: 200, contentType: 'application/json', body: JSON.stringify(cuerpo) });
// `destino` y no `URL`: `URL` es un global de Node y sombrearlo se cobra caro
// el dia que alguien quiera usarlo aca adentro.
const destino = process.argv[2];
const b = await chromium.launch();
try {
const p = await b.newPage();
const errores = [];
p.on('pageerror', e => errores.push(String(e)));
p.on('console', m => { if (m.type() === 'error') errores.push('console: ' + m.text()); });
await p.goto(destino, { waitUntil: 'networkidle' });

const ver = async sel => p.evaluate(s => {
  const e = document.querySelector(s);
  return e ? getComputedStyle(e).display !== 'none' && e.offsetParent !== null : null;
}, sel);

let fallos = 0;
const ok = (c, m) => { console.log((c ? '  OK   ' : '  FALLA ') + m); if (!c) fallos++; };
// El panel de diseño vive en la pestaña "Diseño", y seleccionar un nodo salta a
// la de "Nodo". Volver a Diseño es lo que hace el usuario, asi que el test hace
// lo mismo en vez de asumir que el panel esta siempre a la vista.
const aDiseno = () => p.click('.tab[data-tab="diseno"]');

// La forma en que se ve una clave que el servidor no manda.
//
// Los nueve campos inventados del modo App no rompian nada: la UI leia
// `datos.hechas`, recibia `undefined`, y lo pintaba. Ningun linter mira eso y
// ninguna assertion puntual lo caza salvo que alguien piense de antemano en
// ESA clave. Barrer el texto visible si: es una sola red para las nueve y para
// las que vengan. `sinBasura` recorre lo que se esta mostrando y devuelve el
// primer elemento delator, con su id, para que el mensaje diga donde mirar.
const sinBasura = zona => p.evaluate(sel => {
  const raiz = document.querySelector(sel);
  if (!raiz) return { falta: sel };
  // `\b` solo alrededor de las palabras: `[object Object]` abre y cierra
  // con caracteres que no son de palabra, y con el borde puesto no matcheaba.
  const malo = /\b(?:undefined|NaN)\b|\[object Object\]/;
  const paseo = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
  for (let n = paseo.nextNode(); n; n = paseo.nextNode()) {
    // Solo lo que el usuario ve: un nodo en una rama oculta no engania a nadie,
    // y el <template>/<script> de la pagina no es texto renderizado.
    const padre = n.parentElement;
    if (!padre || !padre.offsetParent || padre.closest('script, style')) continue;
    const t = (n.textContent || '').trim();
    if (malo.test(t)) {
      return { texto: t.slice(0, 80), donde: padre.id || padre.className || padre.tagName };
    }
  }
  return null;
}, zona);

// El overhaul folder-first hizo que el Studio arranque en modo App, y todo lo
// que este guion verifica vive en el modo Studio. Se entra como entra el
// usuario, por el boton de la barra, en vez de asumir cual es la vista inicial.
await p.click('#btnModoStudio');
await p.waitForFunction(() => document.body.classList.contains('modo-studio-activo'),
                        null, { timeout: 5000 });

ok(errores.length === 0, `sin errores de JS al cargar${errores.length ? ': ' + errores[0] : ''}`);

// 1. Con un grafo cargado, Refinar se ofrece; sin nodos, no.
ok((await ver('#btnGenIa')) === true, 'Crear siempre visible');
ok((await ver('#btnRefinarIa')) === true, 'con un grafo cargado, Refinar se ofrece');
await p.evaluate(() => { grafo.nodos = []; grafo.aristas = []; pintar(); });
ok((await ver('#btnRefinarIa')) === false, 'con el lienzo vacio, Refinar se esconde');

// 2. Agrego un nodo como lo haria el usuario (doble clic en el lienzo).
await p.dblclick('#lienzo', { position: { x: 300, y: 200 } });
// `waitForFunction` y no `waitForTimeout`: un timeout fijo es un presupuesto,
// no un estado. En una maquina cargada el guion leia antes de que el handler
// terminara y la falla aparecia como intermitente.
await p.waitForFunction(() => grafo.nodos.length === 1, null, { timeout: 5000 });
const nodos = await p.evaluate(() => grafo.nodos.length);
ok(nodos === 1, `un doble clic crea un nodo (hay ${nodos})`);
await aDiseno();
ok((await ver('#btnRefinarIa')) === true, 'con un nodo, Refinar vuelve a aparecer');
ok((await p.textContent('#pistaRefinar')).includes('en vez de empezar de cero'),
   'la pista explica que Refinar cambia el grafo actual');

// 3. Refinar sin texto avisa y no llama a nadie.
let llamadas = 0;
await p.route('**/api/generar-grafo', r => { llamadas++; r.continue(); });
await p.click('#btnRefinarIa');
await p.waitForFunction(
  () => document.querySelector('#aviso').textContent.includes('qué querés cambiar'),
  null, { timeout: 5000 });
ok(llamadas === 0, 'Refinar sin descripcion no llama al servidor');
ok((await p.textContent('#aviso')).includes('qué querés cambiar'),
   'y avisa que falta la descripcion');

// 4. Refinar manda el grafo ACTUAL y la sesion; crear no.
let cuerpos = [];
await p.unroute('**/api/generar-grafo');
await p.route('**/api/generar-grafo', async r => {
  cuerpos.push(JSON.parse(r.request().postData()));
  await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    ok: true, degradado: false, motivo: '', sesion: 'ses-del-test',
    grafo: { board: 'refinado', reglas: '', aristas: [['n_uno','n_dos']], nodos: [
      { id: 'n_uno', titulo: 'el que ya estaba', runtime: 'opencode', x: 640, y: 420 },
      { id: 'n_dos', titulo: 'el nuevo de tests', runtime: 'opencode', x: 640, y: 570 }]}})});
});
// Renombro el nodo existente para poder seguirlo.
await p.evaluate(() => { grafo.nodos[0].id = 'n_uno'; grafo.nodos[0].x = 640; grafo.nodos[0].y = 420; pintar(); });
await aDiseno();
await p.fill('#promptGenIa', 'agregale un nodo de tests');
await p.click('#btnRefinarIa');
await p.waitForFunction(() => grafo.nodos.some(n => n.id === 'n_dos'),
                        null, { timeout: 5000 });

const env = cuerpos[0] || {};
ok(!!env.actual && env.actual.nodos.length === 1, 'Refinar manda el grafo actual');
ok(env.descripcion === 'agregale un nodo de tests', 'y la descripcion');

const estado = await p.evaluate(() => ({
  ids: grafo.nodos.map(n => n.id),
  uno: grafo.nodos.find(n => n.id === 'n_uno'),
  dos: grafo.nodos.find(n => n.id === 'n_dos'),
  sesion: SESION_IA,
  aviso: document.querySelector('#aviso').textContent,
  pista: document.querySelector('#pistaRefinar').textContent,
  deshacer: pila.length,
}));
ok(JSON.stringify(estado.ids) === '["n_uno","n_dos"]', `el grafo se mezclo: ${estado.ids}`);
ok(estado.uno.x === 640 && estado.uno.y === 420,
   `la UI respeta la posicion que mando el servidor (${estado.uno.x},${estado.uno.y})`);
ok(typeof estado.dos.x === 'number' && !(Math.abs(estado.dos.x-640) < 196 && Math.abs(estado.dos.y-420) < 60),
   `el nodo nuevo quedo ubicado y sin pisar (${estado.dos.x},${estado.dos.y})`);
ok(estado.sesion === 'ses-del-test', 'la sesion quedo guardada para el proximo refinamiento');
ok(estado.aviso.includes('+1 nodo'), `el aviso cuenta el delta: "${estado.aviso}"`);
ok(estado.pista.includes('continúa la conversación'), 'la pista cambia al haber sesion');
ok(estado.deshacer > 0, 'el refinamiento quedo en la pila de deshacer');

// 5. Deshacer devuelve el grafo anterior.
await aDiseno();
await p.keyboard.press('Control+z');
await p.waitForFunction(() => grafo.nodos.length === 1, null, { timeout: 5000 });
const tras = await p.evaluate(() => grafo.nodos.map(n => n.id));
ok(JSON.stringify(tras) === '["n_uno"]', `deshacer revierte el refinamiento (${tras})`);

// 6. Degradado se pinta en ambar, no en verde.
await p.unroute('**/api/generar-grafo');
await p.route('**/api/generar-grafo', r => r.fulfill({ status: 200, contentType: 'application/json',
  body: JSON.stringify({ ok: true, degradado: true, motivo: 'no hay ningun ejecutor instalado',
    sesion: null, grafo: { board: 'x', aristas: [], nodos: [{ id:'n_uno', titulo:'t', runtime:'opencode' }] }})}));
await aDiseno();
await p.click('#btnRefinarIa');
// Por el TEXTO del aviso, no por su clase: la clase ya venia en 'ok' del
// aviso anterior, asi que esperar 'className !== ""' no esperaba nada y se
// leia el estado viejo.
await p.waitForFunction(
  () => document.querySelector('#aviso').textContent.includes('Sin diseño de un agente'),
  null, { timeout: 5000 });
const cls = await p.getAttribute('#aviso', 'class');
ok(cls === 'tibio', `la degradacion se pinta en ambar, no en verde (class="${cls}")`);

// 6b. Los botones del lienzo se pueden apretar de verdad.
// El `top` de la barra flotante vivia en el atributo `style=` del div, y una
// declaracion inline le gana a cualquier selector: las dos reglas que la
// corrigen por modo nunca se aplicaban y la barra quedaba 32 de sus 34px detras
// del header. Un linter de CSS no lo ve y un screenshot casi tampoco; lo que lo
// prueba es preguntar QUIEN recibe el click en ese punto.
await p.click('#btnModoStudio');
await p.waitForFunction(() => document.body.classList.contains('modo-studio-activo'),
                        null, { timeout: 5000 });
const zoom = await p.evaluate(() => {
  const b = document.querySelector('#btnZoomIn').getBoundingClientRect();
  const encima = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
  const cabecera = document.querySelector('#barraSuperiorApp').getBoundingClientRect();
  return { recibe: encima ? encima.id : null, barraTop: Math.round(b.top),
           finCabecera: Math.round(cabecera.bottom) };
});
ok(zoom.recibe === 'btnZoomIn',
   `el boton de zoom recibe su propio click (lo recibe: ${zoom.recibe})`);
ok(zoom.barraTop >= zoom.finCabecera,
   `la barra del lienzo arranca debajo del header (${zoom.barraTop} vs ${zoom.finCabecera})`);

// 7. Modo App: el timeline y la barra leen las claves que el servidor manda.
// Las nueve que leia el overhaul no existian (`hechas`, `total_tareas`,
// `status`, `title`, `result`...), asi que la barra vivia en 0%, todo nodo
// figuraba pendiente para siempre y el entregable no aparecia nunca. Las
// respuestas salen de `fixtures/respuestas_ui.json`, que `test_contrato_ui.py`
// contrasta contra el Studio de verdad: si alguien vuelve a inventar un nombre
// de campo --en la UI o en el mock-- una de las dos suites lo dice.
await p.route('**/api/orquestar-intencion', servir(FIJAS['/api/orquestar-intencion']));
await p.route('**/api/telemetria**', servir(FIJAS['/api/telemetria']));
await p.route('**/api/estado**', servir(FIJAS['/api/estado']));

await p.click('#btnModoApp');
await p.evaluate(() => { WORKSPACE_ACTUAL = 'A:/Proyectos/ORQUESTER'; });
await p.fill('#inputPromptApp', 'revisá el repo');
await p.click('#btnEjecutarPromptApp');
await p.waitForFunction(
  () => document.querySelector('#appTimelineNodos').children.length === 2,
  null, { timeout: 5000 });

const app = await p.evaluate(() => ({
  transform: document.querySelector('#appBarraProgreso').style.transform,
  sub: document.querySelector('#appProgresoSubtxt').textContent,
  linea: document.querySelector('#appTimelineNodos').textContent,
  entregable: document.querySelector('#appEntregableRender').textContent,
}));
ok(app.transform === 'scaleX(0.5)' || app.transform === 'scaleX(0.50)', `la barra usa el progreso real (${app.transform})`);
ok(!/undefined|NaN/.test(app.sub), `el subtitulo no dice undefined ("${app.sub}")`);
ok(app.sub.includes('1/2'), `cuenta los nodos terminados ("${app.sub}")`);
ok(app.sub.includes('0.1234'), `informa el gasto ("${app.sub}")`);
ok(app.linea.includes('Leer el repo') && app.linea.includes('opencode'),
   `el timeline muestra titulo y runtime de verdad ("${app.linea.slice(0, 80)}")`);
ok(app.linea.includes('done') && app.linea.includes('running'),
   `cada nodo muestra SU estado, no todos el mismo ("${app.linea.replace(/\s+/g, ' ').slice(0, 90)}")`);
ok(app.entregable.includes('Salio bien'), 'el entregable del nodo terminado aparece');

const basuraApp = await sinBasura('#vistaApp');
ok(basuraApp === null,
   `el modo App no muestra ninguna clave sin resolver${basuraApp ? `: "${basuraApp.texto}" en ${basuraApp.donde}` : ''}`);

// Y el sondeo se corta cuando el board deja de avanzar, en vez de latir para
// siempre: antes comparaba `undefined === 0` y no paraba nunca.
await p.unroute('**/api/telemetria**');
await p.route('**/api/telemetria**', servir(FIJAS['/api/telemetria/terminado']));
await p.waitForFunction(() => APP_INTERVAL_ID === null, null, { timeout: 8000 })
  .then(() => ok(true, 'el sondeo se detiene cuando el board termina'))
  .catch(() => ok(false, 'el sondeo sigue latiendo con el board terminado'));

// 8. El modal devuelve el foco y no lo deja escapar.
// Se abria desde SEIS lugares con `style.display = "flex"`; el foco quedaba
// donde estuviera, tabular se iba a los controles tapados por el fondo negro, y
// al cerrar volvia al principio del documento.
await p.click('#btnModoStudio');
await p.waitForFunction(() => document.body.classList.contains('modo-studio-activo'),
                        null, { timeout: 5000 });
const modal = await p.evaluate(async () => {
  const disparador = document.querySelector('#btnZoomIn');
  disparador.focus();
  const antes = document.activeElement.id;
  ULTIMO_RESULTADO = 'contenido de prueba';
  document.querySelector('#modalEntregableTexto').textContent = 'contenido de prueba';
  abrirModalEntregable();
  const dentro = document.activeElement.id;
  const m = document.querySelector('#modalEntregable');
  const attrs = { rol: m.getAttribute('role'), modal: m.getAttribute('aria-modal'),
                  etiqueta: document.getElementById(m.getAttribute('aria-labelledby'))?.textContent };
  cerrarModalEntregable();
  return { antes, dentro, despues: document.activeElement.id, attrs };
});
ok(modal.attrs.rol === 'dialog' && modal.attrs.modal === 'true',
   `el modal se declara dialogo (${modal.attrs.rol}/${modal.attrs.modal})`);
ok(modal.attrs.etiqueta === 'Entregable Completo',
   `y tiene nombre accesible ("${modal.attrs.etiqueta}")`);
ok(modal.dentro === 'modalEntregable', `al abrir, el foco entra al dialogo (${modal.dentro})`);
ok(modal.despues === modal.antes,
   `al cerrar, el foco vuelve a quien lo abrio (${modal.antes} -> ${modal.despues})`);

// 9. Soltar cosas: las tres salidas que el navegador permite.
const soltar = (tipo, valor, nombre) => p.evaluate(([tipo, valor, nombre]) => {
  const dt = new DataTransfer();
  if (tipo === 'file') dt.items.add(new File([valor], nombre, { type: 'application/json' }));
  else dt.setData(tipo, valor);
  document.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
}, [tipo, valor, nombre]);

// 9a. Un .json de grafo se abre entero: aca alcanza el contenido, no hace falta
// la ruta, asi que es el unico caso que funciona de punta a punta.
await soltar('file', JSON.stringify({
  board: 'soltado', aristas: [],
  nodos: [{ id: 'x', titulo: 'vino por drag', runtime: 'opencode', x: 20, y: 20 }],
}), 'soltado.json');
await p.waitForFunction(() => grafo.nodos.some(n => n.id === 'x'), null, { timeout: 5000 });
ok(true, 'soltar un .json de grafo lo abre en el lienzo');

// 9b. Un .json que no es un grafo se rechaza nombrando el archivo, no con un
// stack trace ni con el lienzo ya vacio.
await soltar('file', '{"otra":"cosa"}', 'ajeno.json');
await p.waitForFunction(
  () => document.querySelector('#aviso').textContent.includes('no parece un grafo'),
  null, { timeout: 5000 });
ok((await p.evaluate(() => grafo.nodos.some(n => n.id === 'x'))),
   'y no se lleva puesto el grafo que ya estaba');

// 9c. Texto con una ruta: es lo que entrega arrastrar desde la barra de
// direcciones. Se normaliza `file:///` y las comillas de "Copiar como ruta".
const normalizada = await p.evaluate(() => [
  normalizarRuta('"C:\\Users\\santi\\proyecto"'),
  normalizarRuta('file:///C:/Users/santi/mi%20proyecto'),
  normalizarRuta('  /home/user/repo\nsegunda-linea  '),
]);
ok(normalizada[0] === 'C:\\Users\\santi\\proyecto',
   `saca las comillas de "Copiar como ruta" (${normalizada[0]})`);
ok(normalizada[1] === 'C:\\Users\\santi\\mi proyecto',
   `decodifica file:/// y los %20 (${normalizada[1]})`);
ok(normalizada[2] === '/home/user/repo',
   `toma solo la primera linea de una lista de URIs (${normalizada[2]})`);

const basuraStudio = await sinBasura('body');
ok(basuraStudio === null,
   `el Studio tampoco muestra claves sin resolver${basuraStudio ? `: "${basuraStudio.texto}" en ${basuraStudio.donde}` : ''}`);

// 10. Modo Zen: la barra flotante no salta a la derecha
await p.click('#btnModoStudio');
await p.click('#btnModoZen');
const posZen = await p.evaluate(() => {
  const r = document.querySelector('#barraCanvasFlotante').getBoundingClientRect();
  return { left: r.left, right: r.right };
});
ok(posZen.left < 50, `la barra canvas queda a la izquierda en modo zen (left=${posZen.left})`);
await p.click('#btnModoZen'); // salir de zen

// 11. Modo App: paginación de plantillas y selección no-intrusiva
await p.click('#btnModoApp');
const cardsP1 = await p.evaluate(() => document.querySelectorAll('#gridIntencionesApp .card-intencion').length);
ok(cardsP1 === 6, `la pagina 1 muestra 6 plantillas (hay ${cardsP1})`);

await p.click('#btnPaginaSigPlantillas');
const cardsP2 = await p.evaluate(() => document.querySelectorAll('#gridIntencionesApp .card-intencion').length);
ok(cardsP2 === 3, `la pagina 2 muestra 3 plantillas (hay ${cardsP2})`);

await p.click('#btnPaginaAntPlantillas');
// Ocultar sección de ejecución previa para verificar que seleccionar no la activa
await p.evaluate(() => { document.querySelector('#seccionEjecucionApp').style.display = 'none'; });
let orquestacionInvocada = false;
await p.route('**/api/orquestar-intencion', route => {
  orquestacionInvocada = true;
  return route.fulfill({ json: FIJAS['/api/orquestar-intencion'] });
});

// Seleccionar la primera tarjeta
await p.evaluate(() => document.querySelector('#gridIntencionesApp .card-intencion').click());
const estadoSeleccion = await p.evaluate(() => {
  const card = document.querySelector('#gridIntencionesApp .card-intencion');
  const promptVal = document.querySelector('#inputPromptApp').value;
  const badgeVis = document.querySelector('#badgePlantillaSeleccionada').style.display;
  const ejecVis = document.querySelector('#seccionEjecucionApp').style.display;
  return {
    seleccionada: card.classList.contains('seleccionada'),
    tienePrompt: promptVal.length > 0,
    badgeVisible: badgeVis !== 'none',
    ejecucionOculta: ejecVis === 'none'
  };
});
ok(estadoSeleccion.seleccionada, 'al cliquear plantilla queda marcada como seleccionada');
ok(estadoSeleccion.tienePrompt, 'precarga el prompt recomendado en el input');
ok(estadoSeleccion.badgeVisible, 'muestra el badge de plantilla activa');
ok(estadoSeleccion.ejecucionOculta && !orquestacionInvocada, 'no arranca la ejecucion automaticamente al seleccionar');

await p.click('#btnQuitarPlantilla');
const deseleccionado = await p.evaluate(() => {
  const card = document.querySelector('#gridIntencionesApp .card-intencion');
  const badgeVis = document.querySelector('#badgePlantillaSeleccionada').style.display;
  return !card.classList.contains('seleccionada') && badgeVis === 'none';
});
ok(deseleccionado, 'quitar plantilla limpia la seleccion y oculta el badge');

// 12. Modal Cuotas y Modelos
await p.click('#btnAbrirCuotas');
const modalCuotasAbierto = await p.evaluate(() => document.querySelector('#modalCuotas').style.display === 'flex');
ok(modalCuotasAbierto, 'abrir cuotas despliega el modal');

// Y lo que el modal MUESTRA, que es donde estaba el bug. Abrirlo andaba; adentro
// leia tres claves inventadas: `datosCap.runtimes` (los runtimes van en la raiz)
// con `instalado` en vez de `disponible`, y `datosSec.proveedores` en vez de la
// lista `secretos`. Ninguna de las dos redes que ya existen lo veia: la tabla de
// `test_contrato_ui` prueba lo que el SERVIDOR manda, no lo que la UI lee, y el
// barrido de `undefined` no dispara porque el panel pintaba "No detectado" y
// "No se pudo consultar", que son textos validos. Un bug que se ve prolijo.
//
// Por eso se afirma el SIGNIFICADO y no la presencia: cada badge tiene que
// coincidir con el `disponible` que el servidor mando para ese runtime, sea cual
// sea esta maquina.
await p.waitForFunction(
  () => document.querySelector('#cuotasPorRuntime').children.length > 0,
  null, { timeout: 5000 });
const cuotas = await p.evaluate(() => {
  const fila = rt => [...document.querySelectorAll('#cuotasPorRuntime > div')]
    .find(d => d.querySelector('b')?.textContent.trim() === rt);
  const badges = {};
  for (const rt of Object.keys(CAPS)) {
    const f = fila(rt);
    if (f) badges[rt] = /Instalado/.test(f.textContent);
  }
  return {
    badges,
    esperados: Object.fromEntries(
      Object.entries(CAPS).map(([rt, c]) => [rt, !!c.disponible])),
    secretos: document.querySelector('#cuotasEstadoSecretos').textContent,
    filasSecretos: document.querySelectorAll('#cuotasEstadoSecretos > div').length,
  };
});
const malBadge = Object.keys(cuotas.esperados)
  .filter(rt => cuotas.badges[rt] !== cuotas.esperados[rt]);
ok(malBadge.length === 0,
   `el badge de cada runtime coincide con su \`disponible\`${malBadge.length
     ? `: ${malBadge.map(rt => `${rt} dice ${cuotas.badges[rt]} y es ${cuotas.esperados[rt]}`).join(', ')}` : ''}`);
ok(!cuotas.secretos.includes('No se pudo consultar'),
   'el panel de secretos lista las credenciales en vez del mensaje de error');
ok(cuotas.filasSecretos > 0,
   `y pinta una fila por credencial revisada (${cuotas.filasSecretos})`);

await p.keyboard.press('Escape');
const modalCuotasCerrado = await p.evaluate(() => document.querySelector('#modalCuotas').style.display === 'none');
ok(modalCuotasCerrado, 'tecla Escape cierra el modal de cuotas');

// --- Las features del lienzo, donde se ven y no solo si existen -------------
//
// Todas estas andaban "segun el DOM" y estaban rotas para el mouse: la barra de
// busqueda se pintaba DEBAJO del header fijo, asi que su contador no se veia y
// su boton de cerrar no se podia clickear. Una assertion sobre `display` decia
// que todo bien. Por eso estos chequeos preguntan por geometria y por
// `elementFromPoint`, que es lo que el usuario tiene.
await p.keyboard.press('Escape');
await p.click('#btnModoStudio').catch(() => {});
await p.waitForFunction(() => document.body.classList.contains('modo-studio-activo'),
                        null, { timeout: 5000 }).catch(() => {});

await p.evaluate(() => {
  grafo.nodos = [
    { id: 'alfa', titulo: 'alfa uno', runtime: 'claude-code', x: 100, y: 100 },
    { id: 'beta', titulo: 'beta dos', runtime: 'opencode', x: 400, y: 100 }];
  grafo.aristas = [['alfa', 'beta']];
  sel = null; pintar();
});

await p.keyboard.press('Control+f');
const barra = await p.evaluate(() => {
  const b = document.querySelector('#busquedaCanvas');
  const r = b.getBoundingClientRect();
  const enCentro = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const cerrar = document.querySelector('#btnCerrarBusquedaCanvas').getBoundingClientRect();
  return {
    visible: b.style.display === 'flex',
    // Quien esta ARRIBA en el punto donde el usuario apunta el mouse.
    duenoDelPunto: enCentro ? (enCentro.closest('#busquedaCanvas') ? 'busqueda' : enCentro.id || enCentro.tagName) : 'nadie',
    duenoDelCerrar: (el => el && el.closest('#busquedaCanvas') ? 'busqueda' : (el ? el.id || el.tagName : 'nadie'))(
      document.elementFromPoint(cerrar.left + cerrar.width / 2, cerrar.top + cerrar.height / 2)),
  };
});
ok(barra.visible, 'Ctrl+F abre la busqueda del lienzo');
ok(barra.duenoDelPunto === 'busqueda',
   `y la barra recibe el mouse en vez de quedar tapada (arriba esta: ${barra.duenoDelPunto})`);
ok(barra.duenoDelCerrar === 'busqueda',
   `y su boton de cerrar es clickeable (arriba esta: ${barra.duenoDelCerrar})`);

// El atenuado tiene que sobrevivir a un repintado: `pintar()` recrea los nodos.
await p.fill('#inputBusquedaCanvas', 'alfa');
const filtro = await p.evaluate(() => {
  const leer = () => [...document.querySelectorAll('.nodo')].map(g => g.style.opacity);
  const antes = leer();
  pintar();
  return { antes, despues: leer(),
           contador: document.querySelector('#cantBusquedaCanvas').textContent };
});
ok(filtro.antes.includes('0.25'), `la busqueda atenua lo que no coincide (${filtro.antes})`);
ok(JSON.stringify(filtro.antes) === JSON.stringify(filtro.despues),
   `y el atenuado sobrevive a un pintar() (antes ${filtro.antes}, despues ${filtro.despues})`);
ok(filtro.contador === '1/2', `el contador dice cuantas coinciden (${filtro.contador})`);

// Esc con la busqueda abierta cierra la busqueda y NADA MAS.
await p.evaluate(() => { document.querySelector('#modalCuotas').style.display = 'flex'; });
await p.focus('#inputBusquedaCanvas');
await p.keyboard.press('Escape');
const trasEsc = await p.evaluate(() => ({
  busqueda: document.querySelector('#busquedaCanvas').style.display,
  cuotas: document.querySelector('#modalCuotas').style.display,
}));
ok(trasEsc.busqueda === 'none', 'Escape cierra la busqueda del lienzo');
ok(trasEsc.cuotas === 'flex',
   `y no se lleva puesto el modal que estaba abierto detras (quedo en ${trasEsc.cuotas})`);
await p.evaluate(() => { document.querySelector('#modalCuotas').style.display = 'none'; });

// Escribiendo en un campo, los atajos no son atajos.
await p.evaluate(() => { sel = 'alfa'; refrescarEditor(); });
await p.click('.tab[data-tab="nodo"]').catch(() => {});
await p.focus('#titulo');
await p.keyboard.press('Control+f');
await p.keyboard.press('Control+k');
const enCampo = await p.evaluate(() => ({
  busqueda: document.querySelector('#busquedaCanvas').style.display,
  palette: document.querySelector('#modalPalette').style.display,
  foco: document.activeElement.id,
}));
ok(enCampo.busqueda === 'none' && enCampo.palette === 'none',
   'Ctrl+F y Ctrl+K no disparan con el foco adentro de un campo de texto');
ok(enCampo.foco === 'titulo',
   `y el foco se queda donde el usuario estaba escribiendo (quedo en ${enCampo.foco})`);

// El radar y la busqueda son del lienzo: en modo App no van.
await p.click('#btnModoApp').catch(() => {});
await p.waitForFunction(() => document.body.classList.contains('modo-app-activo'),
                        null, { timeout: 5000 }).catch(() => {});
const enApp = await p.evaluate(() => {
  const vis = sel => getComputedStyle(document.querySelector(sel)).display;
  return { minimapa: vis('#minimapaBox'), busqueda: vis('#busquedaCanvas') };
});
ok(enApp.minimapa === 'none',
   `el radar del minimapa no se pinta sobre la vista de App (quedo ${enApp.minimapa})`);
ok(enApp.busqueda === 'none',
   `ni la barra de busqueda del lienzo (quedo ${enApp.busqueda})`);

await p.click('#btnModoStudio').catch(() => {});
await p.waitForFunction(() => document.body.classList.contains('modo-studio-activo'),
                        null, { timeout: 5000 }).catch(() => {});

// Duplicar tiene que dar un nodo INDEPENDIENTE: con un spread, el duplicado y
// el original compartian el array de `herramientas` y editarle los permisos a
// uno se los cambiaba al otro sin avisar.
await p.evaluate(() => {
  grafo.nodos = [{ id: 'uno', titulo: 'uno', runtime: 'claude-code', x: 100, y: 100,
                   herramientas: ['Read', 'Bash'] }];
  grafo.aristas = []; sel = 'uno'; selGroup.clear(); selGroup.add('uno'); pintar();
});
// Ctrl+D de verdad: el duplicado vive adentro del handler de teclado, y
// llamarlo por dentro probaria una funcion que el usuario no tiene.
await p.evaluate(() => { sel = 'uno'; selGroup.clear(); selGroup.add('uno'); });
await p.keyboard.press('Control+d');
const copia = await p.evaluate(() => {
  const nuevo = grafo.nodos.find(n => n.id !== 'uno');
  if (!nuevo) return { falta: true };
  nuevo.herramientas.push('Write');
  return { original: grafo.nodos.find(n => n.id === 'uno').herramientas.length,
           duplicado: nuevo.herramientas.length };
});
ok(!copia.falta && copia.original === 2 && copia.duplicado === 3,
   `duplicar da un nodo independiente del original (original ${copia.original}, copia ${copia.duplicado})`);

// El panel de diff (F3 de docs/PLAN-2026-08-31-visor-diff-en-vivo.md) carga
// perezoso, recien al abrir el <details>. La logica de git real (los tres
// casos, el bloqueo de textconv) la prueba tests/test_visor_diff.py sobre
// el servidor de verdad; aca solo el cableado de la UI, mockeado.
await p.route('**/api/traza*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ titulo: 'nodo', estado: 'done', intentos: [], eventos: [] }) }));
await p.route('**/api/nodo/diff*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ ok: true, caso: 'ok', stat: ' a.txt | 1 +',
                        diff: '+linea nueva', truncado: false }) }));
await p.evaluate(() => { ids = { n1: 't_diff_test' }; sel = 'n1'; });
await p.click('.tab[data-tab="obs"]');
await p.evaluate(() => cargarTraza());
// El `setInterval(refrescarEstado, 2500)` de la pagina puede re-renderizar
// `#traza` (y con el, un `<details>` NUEVO, cerrado) en cualquier momento,
// compitiendo con el click de este test. En vez de click + wait separados
// (una carrera real: el re-render puede caer justo entre los dos), el
// predicado se auto-cura: si lo encuentra cerrado, lo abre el mismo, y
// reintenta hasta que el contenido aparezca — sobrevive a que lo vuelvan a
// pintar en el medio.
await p.waitForFunction(() => {
  const d = document.querySelector('#detallesDiff');
  if (!d) return false;
  if (!d.open) { d.open = true; d.dispatchEvent(new Event('toggle')); }
  return document.querySelector('#cajaDiff')?.textContent.includes('a.txt');
}, null, { timeout: 8000 });
ok(true, 'el panel de diff pinta el stat al abrirse (mockeado)');

await p.unroute('**/api/nodo/diff*');
await p.route('**/api/nodo/diff*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ ok: true, caso: 'sin_workspace', stat: '', diff: '', truncado: false }) }));
// `cargarTraza()` re-arma el `<details>` desde cero (innerHTML entero), asi
// que el `cargado` de la carga perezosa es un closure nuevo por llamada.
await p.evaluate(() => cargarTraza());
await p.waitForFunction(() => {
  const d = document.querySelector('#detallesDiff');
  if (!d) return false;
  if (!d.open) { d.open = true; d.dispatchEvent(new Event('toggle')); }
  return document.querySelector('#cajaDiff')?.textContent.includes('no tiene workspace propio');
}, null, { timeout: 8000 });
ok(true, 'el caso sin_workspace muestra su propio mensaje, no uno generico');
await p.unroute('**/api/traza*');
await p.unroute('**/api/nodo/diff*');

// El panel de lecciones (L2 de docs/PLAN-2026-09-01-lecciones-por-
// repositorio.md), mismo cableado perezoso que el de diff: solo la UI,
// mockeada -- la lectura/escritura real del archivo la prueba
// tests/test_compilador.py y el endpoint via test_contrato_ui.py.
// `/api/traza` tambien tiene que re-mockearse: se desmockeo arriba, y
// `cargarTraza()` lo llama primero -- sin esto pega contra el servidor de
// verdad por una tarea ('t_diff_test') que no existe.
await p.route('**/api/traza*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ titulo: 'nodo', estado: 'done', intentos: [], eventos: [] }) }));
await p.route('**/api/nodo/diff*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ ok: true, caso: 'sin_workspace', stat: '', diff: '', truncado: false }) }));
await p.route('**/api/nodo/lecciones*', r => {
  if (r.request().method() === 'POST') {
    return r.fulfill({ status: 200, contentType: 'application/json',
                       body: JSON.stringify({ ok: true }) });
  }
  return r.fulfill({ status: 200, contentType: 'application/json',
                     body: JSON.stringify({ ok: true, workspace: true, texto: 'no uses tabs' }) });
});
await p.evaluate(() => cargarTraza());
await p.waitForFunction(() => {
  const d = document.querySelector('#detallesLecciones');
  if (!d) return false;
  if (!d.open) { d.open = true; d.dispatchEvent(new Event('toggle')); }
  return document.querySelector('#txtLecciones')?.value.includes('no uses tabs');
}, null, { timeout: 8000 });
ok(true, 'el panel de lecciones carga el texto existente al abrirse (mockeado)');

await p.click('#btnGuardarLecciones');
await p.waitForFunction(() =>
  document.querySelector('#estadoLecciones')?.textContent === 'guardado',
  null, { timeout: 8000 });
ok(true, 'guardar lecciones confirma con "guardado"');

await p.unroute('**/api/nodo/lecciones*');
await p.route('**/api/nodo/lecciones*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ ok: true, workspace: false, texto: '' }) }));
await p.evaluate(() => cargarTraza());
await p.waitForFunction(() => {
  const d = document.querySelector('#detallesLecciones');
  if (!d) return false;
  if (!d.open) { d.open = true; d.dispatchEvent(new Event('toggle')); }
  return document.querySelector('#cajaLecciones')?.textContent.includes('no tiene workspace propio');
}, null, { timeout: 8000 });
ok(true, 'sin workspace, el panel de lecciones avisa en vez de mostrar un editor vacio');

// Terminal en vivo (T1.4 de docs/PLAN-2026-09-01-terminal-en-vivo.md): arranca
// el polling con el nodo 'running', lo mantiene mientras sigue asi, y lo
// frena solo (con un ultimo poll `final=1`) al pasar a 'done'.
let pollTerminal = 0;
await p.unroute('**/api/nodo/lecciones*');
await p.unroute('**/api/traza*');
await p.route('**/api/traza*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ titulo: 'nodo', estado: 'running', intentos: [], eventos: [] }) }));
await p.route('**/api/nodo/terminal*', r => {
  pollTerminal++;
  const offset = Number(new URL(r.request().url()).searchParams.get('offset') || 0);
  const texto = `linea ${pollTerminal}\n`;
  return r.fulfill({ status: 200, contentType: 'application/json',
                     body: JSON.stringify({ texto, offset_nuevo: offset + texto.length }) });
});
await p.evaluate(() => cargarTraza());
await p.waitForFunction(() => document.querySelector('#cajaTerminal')?.textContent.includes('linea 1'),
                        null, { timeout: 8000 });
ok(true, 'la terminal en vivo arranca el polling apenas el nodo esta running (mockeado)');

const antesDeSeguir = pollTerminal;
await p.waitForTimeout(1200);       // mas de un tick del setInterval(1000ms)
ok(pollTerminal > antesDeSeguir,
   `el polling sigue mientras el nodo sigue running (${antesDeSeguir} -> ${pollTerminal})`);

const antesDeDone = pollTerminal;
await p.unroute('**/api/traza*');
await p.route('**/api/traza*', r => r.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ titulo: 'nodo', estado: 'done', intentos: [], eventos: [] }) }));
await p.evaluate(() => cargarTraza());
// La caja NO desaparece en esta misma pasada: le debemos el poll final
// (revision de ingenieria via CodeRabbit -- si se sacara antes, el ultimo
// pedazo retenido en el servidor no tendria donde pintarse).
await p.waitForFunction(() => document.querySelector('#cajaTerminal') != null,
                        null, { timeout: 8000 });
const trasFlush = pollTerminal;
ok(trasFlush - antesDeDone <= 1,
   `al pasar a done deberia haber a lo sumo un poll final, hubo ${trasFlush - antesDeDone}`);
await p.waitForTimeout(300);        // que el poll final (fire-and-forget) asiente TERMINAL_TID
ok(!!(await p.evaluate(() => document.querySelector('#cajaTerminal')?.textContent.includes('linea'))),
   'la caja sigue mostrando lo acumulado durante el poll final, no se vacia');

// Recien en el PROXIMO refresco (el `TERMINAL_TID` ya se limpio solo cuando
// el poll final resolvio) la caja desaparece.
await p.evaluate(() => cargarTraza());
await p.waitForFunction(() => !document.querySelector('#cajaTerminal'), null, { timeout: 8000 });
ok(true, 'en el refresco siguiente al poll final, la caja ya desaparece');
await p.waitForTimeout(1500);       // mas de un tick si el intervalo siguiera vivo
ok(pollTerminal === trasFlush,
   `el polling no debe seguir en segundo plano despues de done (${trasFlush} -> ${pollTerminal})`);

await p.unroute('**/api/nodo/terminal*');

// `sel` sigue apuntando a 'n1' (mockeado): el `setInterval(refrescarEstado,
// 2500)` de la pagina llama `cargarTraza()` sola si `sel` esta puesto, y
// desde aca al final del guion hay margen real para que dispare DESPUES de
// los `unroute` de abajo -- contra el servidor de verdad, por una tarea que
// no existe. Limpiar `sel` apaga ese llamado antes de soltar los mocks.
await p.evaluate(() => { sel = null; });
await p.unroute('**/api/traza*');
await p.unroute('**/api/nodo/diff*');
await p.unroute('**/api/nodo/lecciones*');

ok(errores.length === 0, `sin errores de JS en toda la corrida${errores.length ? ': ' + errores[0] : ''}`);
if (process.argv[3]) await p.screenshot({ path: process.argv[3], fullPage: false });
console.log(fallos ? `\n${fallos} FALLA(S)` : '\nTODO OK');
// `exitCode` y no `exit()`: `exit()` corta el proceso con escrituras de stdout
// pendientes, y el lado Python lee stdout por un pipe. Y el `finally` cierra
// Chromium aunque un selector ausente aborte el guion a la mitad: sin eso el
// navegador quedaba vivo y solo lo mataba el timeout de 180s de afuera.
process.exitCode = fallos ? 1 : 0;
} finally {
  await b.close();
}
