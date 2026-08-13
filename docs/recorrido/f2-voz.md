# F2: la voz

Commits `612a575` (servidor) y `cc4f187` (navegador). Seis archivos nuevos, 34
pruebas añadidas.

Es el fragmento con más riesgo del proyecto, y por eso lo primero que se hizo no
fue escribir código sino **medir la API**. Casi todo lo que el plan daba por
supuesto resultó distinto.

---

## Lo que se midió antes de escribir nada

Una sonda descartable contra la API real, antes de decidir el diseño:

| Pregunta | Respuesta medida |
|---|---|
| Formato de salida | `audio/pcm;rate=24000` |
| Formato de entrada | PCM 16 bits mono, 16 kHz |
| Latencia `3.1-flash-live` | 0.83 s al primer audio |
| Latencia `2.5-native-audio` | 3.01 s |
| Tokens por turno | 291 contra 480 |

Dos consecuencias directas.

**La elección del modelo quedó decidida por datos.** `gemini-3.1-flash-live-preview`
es 3.6 veces más rápido y consume 40 por ciento menos tokens. Su techo de 65K
tokens por minuto, que era la única razón para dudar, resultó holgado: a unos 470
tokens por turno real caben del orden de veinte conversaciones simultáneas.

**Las frecuencias de entrada y salida son distintas.** No es un error de lectura:
la API acepta 16 kHz y devuelve 24 kHz. Confundirlas no produce un error sino
audio al tono equivocado, que es peor porque parece un fallo del modelo.

### El hallazgo que nadie busca

La primera sonda se colgó. Enviando ruido sintético, el modelo nunca detecta fin
de habla, nunca emite `turn_complete`, y un bucle `async for` sobre
`session.receive()` espera para siempre.

Eso se convirtió en una regla de diseño: **ninguna espera sobre el socket puede
ser indefinida**. Está en la configuración como `voice_idle_timeout`.

---

## `src/services/live_service.py`

### Las constantes del contrato

```python
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000
INPUT_MIME = f"audio/pcm;rate={INPUT_SAMPLE_RATE}"
BYTES_PER_SAMPLE = 2  # 16-bit mono
```

Viven en el servidor y viajan al navegador en el saludo del WebSocket. El cliente
no las tiene escritas a mano, así que un cambio aquí no puede desincronizarse en
silencio. Hay una prueba que compara la constante del worklet con esta:

```python
def test_the_worklet_targets_the_rate_the_api_expects():
    source = (WEB / "pcm-processor.js").read_text(encoding="utf-8")
    declared = int(re.search(r"TARGET_RATE\s*=\s*(\d+)", source).group(1))
    assert declared == INPUT_SAMPLE_RATE
```

Es una prueba de Python que lee JavaScript. Poco ortodoxo, pero el fallo que
previene, audio al tono equivocado sin ningún error, es difícil de diagnosticar.

### La aritmética del audio

```python
def audio_seconds(byte_count: int, sample_rate: int = INPUT_SAMPLE_RATE) -> float:
    return byte_count / (sample_rate * BYTES_PER_SAMPLE)
```

Una línea, y aun así con prueba propia:

```python
def test_one_second_of_input_audio_is_32000_bytes():
    assert audio_seconds(32000) == pytest.approx(1.0)
```

La razón: si esta constante estuviera mal, el presupuesto de voz se equivocaría
en la misma proporción, y el síntoma sería un demo que se queda mudo antes de
tiempo sin explicación visible. Es exactamente el tipo de error que una prueba
trivial atrapa y una revisión de código no.

### El presupuesto de voz

```python
class VoiceBudget:
    def __init__(self, max_seconds: float) -> None:
        self.max_seconds = max_seconds
        self.spent_seconds = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.max_seconds - self.spent_seconds)
```

El nivel gratuito limita **tokens por minuto**, no peticiones, y el audio gasta
muchos más que el texto. Sin tope, una conversación larga podría agotar la cuota
y dejar al siguiente visitante, que puede ser quien evalúa el proyecto, con un
demo mudo.

El `max(0.0, ...)` en `remaining` no es paranoia: ese número se muestra en la
interfaz, y una cuenta atrás en negativo no significa nada. Tiene prueba.

Cuando el presupuesto se agota la sesión **no falla**: manda un mensaje de caída
y la conversación sigue en el chat de texto de F1.

### La configuración de la sesión

```python
def build_config(self, system_prompt: str) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=system_prompt,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
```

`response_modalities=["AUDIO"]` es obligatorio: este modelo **rechaza** la
modalidad TEXT. Es un modelo de voz, y por eso el texto va por el camino REST de
F1 y no por aquí.

Las dos transcripciones están activas a propósito. Se midió que llegan en el
mismo flujo que el audio y sin costarle calidad, y sin ellas una conversación
hablada no dejaría ningún texto que F3 pueda guardar. La memoria de una sesión de
voz depende enteramente de esta línea.

`system_instruction` recibe el mismo prompt que construye
`src/coaching/prompts.py`. Es lo que hace que las reglas de entrenamiento se
apliquen igual hablando que escribiendo, y se puede comprobar en la verificación:
el coach citó la regla del diez por ciento en voz sin que nadie se la mencionara.

---

## `src/routes/voice.py`

El proxy. El navegador nunca ve la clave de API: cada trama de audio pasa por
aquí.

### Dos bombas concurrentes

```python
mic = asyncio.create_task(_pump_microphone(websocket, session, budget, settings))
model = asyncio.create_task(_pump_model(websocket, session))

done, pending = await asyncio.wait({mic, model}, return_when=asyncio.FIRST_COMPLETED)
for task in pending:
    task.cancel()
```

Una conversación de voz es bidireccional y simultánea: el corredor puede hablar
mientras el coach responde. Eso no se puede modelar con petición y respuesta, así
que hay dos tareas corriendo a la vez.

`FIRST_COMPLETED` significa que en cuanto una termina, la otra se cancela. Sin
esa cancelación quedaría una tarea esperando sobre un socket que ya nadie lee, y
esas fugas se acumulan por cada sesión.

### El plazo en la espera del micrófono

```python
try:
    message = await asyncio.wait_for(
        websocket.receive(), timeout=settings.voice_idle_timeout
    )
except asyncio.TimeoutError:
    return "idle"
```

Aquí está la lección de la sonda colgada. Una pestaña que se queda abierta sin
hablar mantendría el socket y la sesión Live vivos indefinidamente, gastando
recursos por nada.

### El tope como techo real

```python
if audio:
    budget.charge(len(audio))
    if budget.exhausted:
        return "budget"
    await session.send_realtime_input(...)
```

El orden importa y es deliberado: se cobra, se comprueba, y **solo entonces** se
envía. Al revés, el trozo que rebasa el límite ya habría llegado al modelo y el
tope sería aproximado. Hay una prueba específicamente para esto:

```python
def test_audio_over_the_cap_is_not_forwarded_to_the_model(voice_client):
    ...
    assert session.sent_audio == []
```

### Cerrar siempre

```python
finally:
    await _safe_close(websocket)


async def _safe_close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:
        pass
```

Un WebSocket que no se cierra deja al navegador esperando. El `try`/`except`
vacío está justificado: si el navegador ya se fue, cerrar lanza excepción, y esa
excepción no aporta nada.

---

## `web/pcm-processor.js`

El archivo más delicado del fragmento.

### Por qué un AudioWorklet y no MediaRecorder

`MediaRecorder` es la forma evidente de grabar audio en el navegador y aquí es la
equivocada: produce contenedores **webm/opus**, comprimidos, y la Live API quiere
PCM crudo. No hay opción de configuración que lo cambie.

Un `AudioWorkletProcessor` entrega las muestras sin codificar, en `Float32`, y
corre en el hilo de renderizado de audio. Ese hilo no puede bloquearse nunca: sin
`await`, sin registros, sin reservas de memoria innecesarias en el camino
caliente.

### El remuestreo con posición fraccionaria

```js
while (this.position < samples.length) {
  const index = Math.floor(this.position);
  const frac = this.position - index;
  const current = samples[index];
  const next = index + 1 < samples.length ? samples[index + 1] : current;
  this.pending[this.pendingCount++] = current + (next - current) * frac;
  this.position += this.ratio;
}
this.position -= samples.length;
```

El navegador entrega bloques de 128 muestras a la frecuencia del contexto,
normalmente 44100 o 48000 Hz. Hay que bajarlos a 16000.

`this.ratio` es cuántas muestras de origen equivalen a una de destino: a 48 kHz
son exactamente 3, pero a 44.1 kHz son 2.75625, un número no entero.

Ahí está la sutileza. `this.position` **sobrevive entre llamadas** a `process()`,
y la última línea arrastra la fracción sobrante al siguiente bloque. Si se
reiniciara a cero cada vez, la posición de lectura se redondearía cada 128
muestras y el error se acumularía: la voz iría desviándose poco a poco. Es un
fallo que no produce ningún error y solo se nota como una degradación gradual de
la transcripción.

Se usa interpolación lineal y no el vecino más cercano. Cuesta un poco más, pero
el reconocimiento de voz es sensible a la aspereza que introduce el redondeo.

### La conversión asimétrica

```js
out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
```

Un entero de 16 bits con signo cubre de -32768 a 32767. El rango **no es
simétrico**: hay un valor más hacia abajo.

Usar 32767 para ambos lados desperdicia ese valor y, más importante, recorta el
audio bajo. Es un detalle de una línea que afecta directamente a lo bien que el
modelo entiende a alguien que habla en voz baja.

---

## `web/app.js`

### Encadenar la reproducción

```js
const startAt = Math.max(ctx.currentTime, state.nextPlayTime);
source.start(startAt);
state.nextPlayTime = startAt + buffer.duration;
```

El audio llega en muchos trozos pequeños. Reproducir cada uno con `start()` en
"ahora" los solaparía todos en un ruido ininteligible.

Programar cada trozo para empezar donde terminó el anterior mantiene el habla
continua. El `Math.max` cubre el caso de que la reproducción se haya quedado
atrás respecto al reloj, cuando llega audio más despacio de lo que se consume.

### Acumular transcripciones parciales

```js
function appendTranscript(role, text) {
  if (!state.partial[role]) {
    state.partial[role] = addBubble(role, text, { partial: true });
  } else {
    state.partial[role].textContent += text;
  }
}
```

Las transcripciones llegan en fragmentos conforme alguien habla. Añadir cada uno
como su propio globo destrozaría una frase en una docena de líneas. Se acumulan
en el mismo globo, marcado con opacidad reducida, hasta que el turno se completa.

### El nodo de ganancia silenciosa

```js
const silence = state.audioContext.createGain();
silence.gain.value = 0;
node.connect(silence).connect(state.audioContext.destination);
```

Parece absurdo conectar algo a la salida con volumen cero. La razón: algunos
navegadores suspenden un grafo de audio que no llega a ningún destino, y el
worklet dejaría de recibir muestras. Con ganancia cero el grafo sigue vivo y el
micrófono no se escucha por los altavoces.

---

## La verificación

Las pruebas usan una sesión Live falsa, así que por definición no demuestran que
el sistema real funcione. Y no había micrófono disponible.

La solución fue dar un rodeo: **pedirle a la Live API que hablara**, remuestrear
su salida de 24 kHz a 16 kHz con la misma interpolación lineal que usa el worklet,
y empujar ese audio por nuestro propio WebSocket en trozos de 100 ms, exactamente
como los manda el navegador.

Eso ejercita todo salvo la captura del worklet: el proxy, el presupuesto, la
sesión Live, la transcripción en ambos sentidos y el audio de vuelta.

```
lo que el coach ESCUCHÓ  : 'Hola, entrenador. Corro 20 km por semana y quiero
                            un medio maratón.'
lo que el coach RESPONDIÓ: 'Veinte kilómetros ya son una buena base. [...]
                            Así sumamos los kilómetros sin pasarnos de ese
                            diez por ciento.'
audio de vuelta: 643202 bytes (13.4s a 24 kHz)
```

Tres cosas confirmadas de una vez: el habla se reconoce, la persona llega a la
sesión de voz (cita la regla sin que se la mencionen) y vuelve audio real.

La página se abrió además en un navegador y renderiza sin errores de consola.

---

## Lo que F2 no hace

**No recuerda.** Cada sesión de voz empieza en blanco. La transcripción ya se
captura y se envía al navegador, pero nadie la guarda todavía: eso es F3, y esta
es justamente la pieza que lo hace posible.

**El micrófono exige contexto seguro.** Los navegadores permiten `getUserMedia`
en `localhost`, pero al desplegar hace falta HTTPS. Es un requisito de F7, no un
detalle opcional.

**La interfaz es funcional, no diseñada.** F5 la reemplaza con un diseño hecho en
Google Stitch. Lo que hay ahora existe para poder usar y demostrar la voz.
