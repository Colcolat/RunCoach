# F5: la interfaz

Commits `98cc25b` (el endpoint), `e8f8a3a` (la interfaz) y `7334357` (el hueco).
Tres archivos reescritos, uno nuevo, 17 pruebas añadidas.

La interfaz anterior existía para poder demostrar la voz, y lo decía en su propia
hoja de estilos:

```css
/* Functional styling for F2. The designed interface arrives in F5; this exists
   so the voice path can be used and demonstrated. */
```

Este fragmento es esa interfaz. Y buena parte del trabajo no fue escribirla, sino
decidir qué parte del diseño generado era de este producto.

---

## El diseño vino de Stitch; el código, no

El diseño se generó en Google Stitch con los prompts que quedaron en
[`docs/f5-prompts-stitch.md`](../f5-prompts-stitch.md). Lo que devolvió es un
documento HTML con Tailwind por CDN, dos fuentes de Google y cuatro lienzos
WebGL.

Se conservó **la paleta, la escala tipográfica, las formas y el tratamiento de
los cuatro estados de voz**, que es lo que se le pidió y lo que resolvió bien.

Lo demás se rehízo. Vale la pena decir por qué caso por caso, porque "lo
reescribí" sin razones es indistinguible de no querer usar lo ajeno.

### Tailwind desde un CDN

```html
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
```

La documentación de Tailwind califica ese build como solo para desarrollo. Además
es una dependencia de *runtime* sobre el servidor de otro: si ese CDN falla, la
página se sirve sin estilos. F7 despliega esto, y un despliegue no tiene por qué
cargar con eso.

### Dos fuentes de Google

Inter para el texto y Material Symbols entera para dos iconos. Una fuente externa
bloquea el primer pintado, falla sin conexión y le cuenta a un tercero quién
visita la página. La escala tipográfica sobrevive perfectamente sobre la pila del
sistema, y los dos iconos son ahora SVG en línea.

### Cuatro lienzos WebGL

Los anillos del micrófono venían como shaders GLSL, cada uno con su contexto
WebGL y su bucle de `requestAnimationFrame` corriendo para siempre.

Un anillo que late es una animación CSS. En un teléfono, cuatro contextos WebGL
animando sin parar son batería a cambio de la misma imagen. Y con esta versión
solo anima el anillo del estado activo, así que una página en reposo no cuesta
nada:

```css
.ring { opacity: 0; }              /* ninguno anima por defecto */

.voice[data-state="listening"] .ring-pulse {
  opacity: 1;
  animation: pulse 1.8s ease-out infinite;
}
```

Y se detienen del todo cuando el sistema lo pide:

```css
@media (prefers-reduced-motion: reduce) {
  .ring { animation: none !important; }
}
```

### Una capa entera de producto que no existe

Esto es lo que más tiempo llevó separar. El diseño traía:

| lo que traía | por qué no existe |
|---|---|
| Nav a Progreso, Rutas, Historial, Ajustes | No hay esas pantallas |
| Botón "Cerrar Sesión" | No hay cuentas; la identidad es un id en localStorage |
| Foto de perfil de archivo | No se piden ni se guardan fotos |
| Insignia "Nivel Pro" | No existe tal cosa |
| "Espacio para gráficos de progreso" | Se guarda el volumen actual, no la serie |
| Conversación de ejemplo sobre series de 1km a 4:30 | No es lo que dice este coach |

Stitch diseña asumiendo una aplicación de fitness genérica, y lo hace bien. Pero
una interfaz que ofrece cuatro secciones inexistentes y un botón de cerrar sesión
sin sesión que cerrar es peor que una fea: promete cosas que no hay.

Hay una prueba que vigila lo primero de la lista:

```python
def test_the_client_pulls_nothing_from_the_internet(asset):
    assert "https://" not in source
```

Porque un CDN es exactamente el tipo de cosa que vuelve a colarse.

---

## `GET /api/profile/{session_id}`

El panel necesitaba de dónde leer, y no había nada.

```python
@router.get("/profile/{session_id}", response_model=ProfileResponse)
def profile(session_id: str) -> ProfileResponse:
```

### Es de solo lectura, y es una decisión

No hay `PUT` y no hay formulario detrás. El corredor nunca escribe su perfil: lo
menciona y el coach lo oye.

Un perfil editable sería una segunda fuente de verdad compitiendo con la
conversación, y la conversación tiene que ganar. Si alguien escribe "20" en una
casilla y luego dice "corro quince", ¿cuál manda? La pregunta no tiene buena
respuesta, así que no se plantea.

La prueba lo fija de una forma tosca pero efectiva:

```python
assert page.count("<form") == 1, "solo el composer debe ser un formulario"
```

### Las semanas se calculan en el servidor

```python
weeks_to_race=weeks_until(user["race_date"], datetime.now(timezone.utc).date())
```

La aritmética ya existía dentro del renderizador del prompt, porque el coach
razona en semanas. Reimplementarla en JavaScript daría dos versiones libres de
discrepar: el coach diciendo "faltan seis semanas" y el panel mostrando siete.

Así que se extrajo a una función pública que usan las dos:

```python
def weeks_until(race_date: str | None, today: date) -> int | None:
```

### Todo es opcional, y eso es un estado

Una primera visita devuelve cuatro nulos. No es un error, es la situación normal
de alguien que acaba de llegar, y el panel tiene que dibujarla. Hay prueba:

```python
def test_a_first_visit_returns_an_empty_profile_not_an_error(client):
```

---

## Un atributo gobierna la voz

```javascript
function setState(name, message) {
  el("voice").dataset.state = name;
  if (message !== undefined) el("status").textContent = message;
}
```

Todo lo que distingue visualmente los cinco estados vive en el CSS, colgando de
`[data-state]`. El JavaScript solo dice en cuál estamos.

La alternativa habitual, ir añadiendo y quitando clases desde varios sitios,
reparte la apariencia entre dos archivos y termina en estados imposibles, del
tipo "escuchando" y "no disponible" a la vez.

### El estado se dice, no solo se pinta

El texto se cambia **junto** al estado, nunca en su lugar:

```html
<p class="status" id="status" role="status">Listo para hablar o escribir</p>
```

El color solo dejaría el estado invisible para parte de quien la usa, y "sé si me
está escuchando" no es un adorno en una interfaz de voz: es la interfaz.

Medido con las transiciones desactivadas, los cinco difieren en más que el color:

| estado | borde | texto | halo | icono |
|---|---|---|---|---|
| idle | verde | verde | verde | micrófono |
| listening | verde + anillo latiendo | verde | verde | micrófono |
| thinking | gris | claro | ninguno | micrófono |
| speaking | cálido | cálido | cálido | micrófono |
| unavailable | apagado | atenuado | ninguno | micrófono tachado |

El tono cálido de "hablando" es deliberado: escuchar y hablar son direcciones
opuestas y no deben parecerse.

### Un detalle que casi se me escapa

Verificando los estados medí el borde del micrófono en los cinco y salía verde
siempre. Parecía que el CSS no se aplicaba.

No era eso. `.mic` tiene `transition: border-color 0.2s`, y `getComputedStyle`
leído en el mismo tick devuelve el fotograma intermedio, no el destino. Con las
transiciones desactivadas, los cinco valores salieron distintos.

Vale la pena anotarlo porque el instinto era "arreglar" un CSS que estaba bien.

---

## El panel, dos formas del mismo marcado

El perfil se escribe una vez en el HTML y el CSS decide cómo se ve.

En pantalla ancha es una columna al lado de la conversación. En estrecha, una
tira de chips que se desplaza en horizontal, y ahí los campos vacíos
**desaparecen**:

```css
.profile[data-known="true"] .field[data-empty="true"] {
  display: none;
}
```

Rellenar una tira estrecha con cuatro "sin registrar" gasta el ancho que hay
justo en lo único que no aporta nada. En pantalla ancha sí se muestran, porque
ahí el hueco sobra y saber qué falta es útil.

La pista solo aparece cuando no hay nada:

```css
.profile[data-known="true"] .profile-hint { display: none; }
```

Explica un panel vacío, y un panel vacío es lo único que necesita explicar.

Hay una prueba que ata el panel al endpoint:

```python
assert set(ProfileResponse.model_fields) == set(shown) | ignored
```

Si mañana el endpoint devuelve un campo nuevo y nadie le hace sitio, la prueba
falla en lugar de que el dato desaparezca en silencio.

---

## La respuesta degradada es la aplicación, no el coach

```javascript
addBubble("coach", data.reply, { notice: data.degraded });
```

Un límite de frecuencia se despeja en menos de un minuto. Pintarlo igual que una
respuesta del entrenador hace que parezca que el entrenador se equivocó; pintarlo
como un error rojo hace que parezca que la aplicación está rota. Va en un tono
propio, más apagado y en cursiva, que se lee como una nota al margen.

---

## El hueco de setecientos píxeles

Reportado usando la aplicación en un monitor de 1908 de ancho: un vacío enorme
entre la conversación y el panel.

La columna de la conversación era elástica y su contenido no:

```css
grid-template-columns: minmax(0, 1fr) 320px;   /* la columna crecía */
.stage { max-width: 52rem; }                   /* el contenido no */
```

El tope de ancho es correcto: una línea de texto deja de ser legible mucho antes
de dejar de caber. Lo que estaba mal era dónde iba el sobrante.

```css
grid-template-columns: minmax(0, 52rem) 320px;
justify-content: center;
```

El tope pasa a la columna, y el par se centra. El ancho extra se va a los
márgenes exteriores.

| ancho | hueco | márgenes |
|---|---|---|
| 1908 | 32 px | 362 px iguales |
| 1024 (el corte) | 32 px | 32 px, suma exacta |

---

## Verificación

Medida en navegador a 1908, 1440 y 1024, y en el layout estrecho:

- la página no se desplaza en horizontal a ningún ancho
- el micrófono y el composer quedan siempre en pantalla
- en estrecho el orden es conversación, micrófono, composer, con el micrófono en
  la mitad inferior, donde llega un pulgar
- los cinco estados difieren en borde, texto y halo
- un turno escrito en la interfaz movió el estado por "pensando" y volvió, y el
  panel se rellenó en vivo con 21K y 12 km por semana

Y una falsa alarma que conviene dejar escrita: midiendo pareció que sobraban
690 px bajo el composer. No era cierto. `100dvh` resolvía a 844 px y las filas
del grid sumaban exactamente 844; lo que no coincidía era el `innerHeight` que
reporta el panel de vista previa por su escalado. Tocar el CSS ahí habría roto
algo que funcionaba.

La voz con micrófono real la verificó el autor.

---

## Lo que F5 no hace

**El aviso de respuesta degradada no sobrevive a una recarga.** Se guarda el
texto del mensaje, no que fuera degradado, así que al recuperar el historial se
dibuja como una respuesta normal del coach. Guardarlo sería una columna más en
`messages`.

**No hay tema claro.** La interfaz es oscura y solo oscura. `prefers-color-scheme`
no se consulta.

**El panel no se puede plegar en móvil.** Ocupa su tira siempre. Con cuatro
campos cabe; con más habría que poder esconderlo.
