# F1: el cerebro del coach

Commits `0018b74` (dominio) y `12b37e0` (conexión con el modelo). Cinco archivos
nuevos, 60 pruebas añadidas, 87 por ciento de cobertura.

El fragmento se dividió en dos commits a propósito: primero las reglas de
entrenamiento, que no dependen de nada externo, y solo después el código que
llama al modelo. Así el dominio quedó probado antes de que existiera la
posibilidad de esconder un error detrás de una llamada de red.

El flujo completo de una petición:

```
POST /api/chat
      |
  routes/chat.py        valida la entrada
      |
  agents/coach_agent.py orquesta; devuelve la respuesta, no la envía
      |
      +-- coaching/prompts.py      construye el system_instruction
      |
      +-- services/gemini_service.py  habla con la API
```

---

## `src/coaching/prompts.py`

El archivo más importante del proyecto. Contiene la filosofía de entrenamiento y
**no importa nada del SDK ni toca disco ni red**. Es Python puro: entra un
diccionario, sale un texto.

Esa pureza es deliberada. Significa que las reglas de coaching se pueden probar
en milisegundos, sin credenciales, y que cambiarlas produce un diff que un
revisor humano puede leer.

### La persona

```python
BASE_PERSONA = """Eres RunCoach, un entrenador de running con veinte años...

COMO HABLAS
Hablas como un entrenador de verdad en una conversación: frases naturales...
Nunca uses listas con viñetas, numeraciones, markdown, asteriscos ni
encabezados: tus respuestas se leen en voz alta y esos formatos suenan
artificiales.
```

La prohibición de markdown no es una preferencia estética. **El mismo texto va a
alimentar el camino de voz en F2.** Un asterisco leído en voz alta por un
sintetizador es ruido; una lista numerada suena a robot dictando un formulario.
Construir esa restricción desde F1 evita reescribir el prompt cuando llegue la
voz.

```
Si te falta un dato que cambia tu respuesta (el objetivo, el nivel, cuánto corre
ya), pregúntalo en una sola frase antes de dar un plan. Una pregunta a la vez, no
un cuestionario.
```

Esto se puede ver funcionando. En la verificación real, ante "Corro 20 km por
semana y quiero un 21K en diciembre", el coach respondió pidiendo el ritmo en
lugar de inventar un plan sobre datos que no tenía.

```
REGLAS QUE NUNCA ROMPES
Nunca subas el volumen semanal más de un diez por ciento...
Toda semana lleva al menos un día de descanso completo...
Aproximadamente el ochenta por ciento del volumen va a ritmo suave...
Ante dolor agudo... parar y buscar valoración médica. No diagnostiques
lesiones...
```

Las cuatro reglas tienen respaldo en la práctica del entrenamiento:

- **Diez por ciento**: el aumento brusco de carga es la causa principal de
  lesiones por sobrecarga.
- **Día de descanso**: la adaptación fisiológica ocurre durante la recuperación,
  no durante el esfuerzo.
- **Ochenta/veinte**: la mayoría de corredores aficionados corren sus días
  suaves demasiado rápido, lo que impide recuperar, y sus días duros demasiado
  lento, lo que impide el estímulo.
- **Dolor agudo**: un modelo de lenguaje no puede explorar a nadie. Diagnosticar
  por conversación sería, además de inútil, peligroso.

### Guía por distancia y por nivel

```python
GOAL_GUIDANCE = {
    "5K": """...la clave está en la velocidad sostenida, no en el volumen...""",
    "10K": """...la sesión clave es un tempo continuo de veinte a cuarenta
              minutos...""",
    "21K": """...la tirada larga debe crecer progresivamente hasta los
              dieciocho o veinte kilómetros...""",
    "Maratón": """...un proyecto de dieciséis a veinte semanas... ensayar la
                  alimentación...""",
}
```

Un diccionario, no una cadena de `if`. Agregar una distancia es agregar una
entrada, y la prueba que recorre todas las claves se adapta sola.

Las cuatro claves son exactamente las cuatro distancias que pide el enunciado
del reto, y hay una prueba que lo verifica:

```python
def test_the_four_challenge_distances_are_all_covered():
    assert {"5K", "10K", "21K", "Maratón"} == set(GOAL_GUIDANCE)
```

Usa `==` y no `<=`: si alguien agrega una distancia sin actualizar la prueba,
falla. Es intencional; obliga a pensar si la nueva distancia debe estar ahí.

### Describir lo que no se sabe

```python
def _describe_profile(profile: dict) -> str:
    lines = [
        f"Objetivo: {goal}" if goal else "Objetivo: sin registrar",
        f"Nivel: {level}" if level else "Nivel: sin registrar",
        f"Volumen actual: {mileage} km por semana" if mileage
            else "Volumen actual: sin registrar",
    ]
```

El guion bajo inicial en `_describe_profile` es una convención de Python: indica
que la función es interna al módulo. No lo impide técnicamente, pero comunica
que no forma parte de la interfaz pública.

**Los campos desconocidos se nombran, no se omiten.** Si el objetivo faltara y
simplemente no apareciera, el modelo no tendría forma de saber que falta y
armaría un plan sobre suposiciones silenciosas.

Pero el texto es **descriptivo, nunca imperativo**, y eso viene de un fallo real.

#### El bucle de preguntas

La primera versión escribía `"Objetivo: aún sin definir, pregúntalo"`. Parecía
razonable: si falta, que lo pregunte.

En una sesión de voz real el coach preguntó el objetivo cuatro veces, preguntó
el volumen tres veces y nunca llegó a dar un plan, pese a que el corredor había
contestado todo en los primeros turnos.

La causa: **la instrucción de sistema es constante durante toda la sesión.** En
cada turno el modelo volvía a recibir la orden "el objetivo sigue sin definir,
pregúntalo", aunque la conversación ya lo contuviera. Y la instrucción de sistema
pesa más que el historial, así que ganaba la orden.

El arreglo tiene tres partes:

1. El perfil pasa a ser descriptivo: `"Objetivo: sin registrar"`, sin verbo.
2. Se encabeza declarando quién manda:
   `"PERFIL REGISTRADO ANTES DE ESTA CONVERSACIÓN (si la conversación dice otra
   cosa, la conversación manda)"`.
3. La persona gana una sección `CUANDO DEJAR DE PREGUNTAR`, que prohíbe repetir
   una pregunta contestada y ordena entregar el plan en cuanto haya objetivo,
   nivel aproximado y volumen aproximado, asumiendo en voz alta lo que falte.

Repitiendo la misma conversación después del cambio: entrega plan desde el
segundo turno, no vuelve a pedir el volumen ni una vez, y declara sus
suposiciones ("vamos a suponer que puedes salir tres días por semana").

La lección general: en un prompt de sistema, una instrucción condicional que
depende del estado de la conversación no funciona, porque el prompt no se
recalcula por turno. Lo que depende del estado tiene que ir en el historial o
expresarse como regla, no como orden puntual.

Este fallo no lo detectó ninguna de las 98 pruebas que había. Lo detectó usar la
aplicación.

### Componer el prompt

```python
def build_system_prompt(profile: dict | None = None) -> str:
    profile = profile or {}
    sections = [BASE_PERSONA, "PERFIL DEL CORREDOR\n" + _describe_profile(profile)]

    goal = profile.get("goal")
    if goal and goal in GOAL_GUIDANCE:
        sections.append("GUÍA PARA ESTE OBJETIVO\n" + GOAL_GUIDANCE[goal])

    level = profile.get("experience_level")
    if level and level.lower() in EXPERIENCE_GUIDANCE:
        sections.append("GUÍA PARA ESTE NIVEL\n" + EXPERIENCE_GUIDANCE[level.lower()])

    return "\n\n".join(sections)
```

`profile = profile or {}` es un modismo de Python: si `profile` es `None` (o
cualquier valor "falsy"), usa un diccionario vacío. Evita repetir
`if profile is None`.

`dict | None` en la firma es la sintaxis moderna para "un diccionario o nada".
Antes se escribía `Optional[dict]`.

La guía es **aditiva y exclusiva**: solo se agrega la sección del objetivo que el
corredor tiene. Una prueba verifica que un corredor de 5K no recibe la guía de
maratón, porque mezclarlas produciría consejos contradictorios.

El `.lower()` en el nivel permite que funcione tanto `"Intermedio"` como
`"intermedio"`, cosa que importará cuando en F4 el nivel venga extraído de una
conversación y no de un formulario.

### El saludo escrito a mano

```python
def welcome_message(username: str | None = None) -> str:
    who = f" {username}" if username else ""
    return (f"Hola{who}, soy tu entrenador. Cuéntame qué quieres preparar...")
```

Está escrito, no generado. Dos razones: el nivel gratuito limita las peticiones
diarias y no tiene sentido gastar una en un saludo previsible; y así la
aplicación responde al instante al abrirse, sin esperar a la red.

---

## `src/services/gemini_service.py`

```python
_ROLE_MAP = {"user": "user", "assistant": "model"}
```

La API de Gemini llama `model` a lo que el resto del proyecto llama `assistant`.
Esta tabla es la traducción, y es más importante de lo que parece: si el mapeo
fallara en silencio, el modelo leería sus propias respuestas anteriores como si
las hubiera dicho el corredor, y la conversación se volvería incoherente sin
ningún error visible.

Por eso hay una prueba dedicada:

```python
def test_the_assistant_role_is_mapped_to_the_api_name():
    contents = GeminiService._to_contents("¿y ahora?", [
        {"role": "user", "content": "corro 20 km"},
        {"role": "assistant", "content": "buen punto de partida"},
    ])
    assert [c.role for c in contents] == ["user", "model", "user"]
```

### El cliente perezoso

```python
def _get_client(self) -> genai.Client:
    if not self.enabled:
        raise GeminiUnavailableError("GOOGLE_API_KEY is not configured")
    if self._client is None:
        self._client = genai.Client(api_key=self._settings.google_api_key)
    return self._client
```

El cliente se construye la primera vez que se necesita, no en el `__init__`.

**Por qué así.** Si se construyera al instanciar el servicio, y el servicio se
instanciara al importar el módulo, entonces `import src.main` fallaría sin clave.
Eso tumbaría la suite de pruebas y el endpoint `/health` a la vez, justo cuando
se necesita `/health` para diagnosticar que falta la clave.

### Un solo tipo de error

```python
class GeminiUnavailableError(RuntimeError):
    """Raised when the model cannot be reached or is not configured."""
```

El SDK puede lanzar muchas excepciones distintas: error de red, cuota agotada,
modelo retirado, clave inválida. El servicio las captura todas y las vuelve a
lanzar como un solo tipo:

```python
except Exception as exc:
    logger.exception("Gemini request failed")
    raise GeminiUnavailableError(str(exc)) from exc
```

`from exc` conserva la excepción original encadenada, así que la traza completa
sigue disponible en el registro. Pero quien llama solo necesita capturar un tipo.

Capturar `Exception` de forma amplia normalmente es mala práctica; aquí es
deliberado y está acotado a una sola llamada, con el comentario `# noqa: BLE001`
que le dice al analizador estático que la regla se está saltando a propósito.

### El caso vacío

```python
if not text:
    finish = response.candidates[0].finish_reason if response.candidates else None
    logger.error("Gemini returned no text (finish_reason=%s)", finish)
    raise GeminiUnavailableError(f"empty response from model (finish={finish})")
```

Este bloque existe por un descubrimiento concreto durante la fase de medición.

Los modelos con razonamiento interno gastan tokens "pensando" antes de escribir,
y esos tokens **se descuentan del mismo presupuesto** de `max_output_tokens`. Con
un presupuesto de 400, uno de los modelos gastó 382 pensando y emitió catorce
caracteres, terminando con `finish_reason=MAX_TOKENS`.

El síntoma es una respuesta vacía o cortada, sin ningún error. Sin este bloque,
el usuario recibiría una respuesta en blanco y nadie sabría por qué. Con él, el
registro dice exactamente qué pasó.

### La persona como instrucción de sistema

```python
config = types.GenerateContentConfig(
    system_instruction=system_prompt,
    temperature=self._settings.gemini_temperature,
    max_output_tokens=self._settings.gemini_max_output_tokens,
)
```

`system_instruction` es un campo propio de la API, separado de la conversación.

La alternativa, que usaba el primer intento del proyecto, era poner la persona
como primer mensaje del usuario. Eso tiene dos problemas: compite por espacio con
el historial y puede quedar desplazada fuera de la ventana de contexto conforme
la conversación crece; y ensucia el historial que luego se vuelve a enviar.

`temperature=0.7` controla la aleatoriedad: 0 daría siempre la misma respuesta,
valores altos la vuelven errática. 0.7 es un punto habitual para conversación,
donde repetir palabra por palabra suena artificial.

---

## `src/agents/coach_agent.py`

```python
@dataclass(frozen=True)
class CoachReply:
    text: str
    degraded: bool = False
```

`@dataclass` genera automáticamente el `__init__`, el `__repr__` y la comparación
por igualdad. `frozen=True` hace el objeto inmutable: una vez creado, no se le
puede cambiar un campo. Eso evita que una capa superior modifique la respuesta
del coach por accidente.

El campo `degraded` distingue una respuesta real del modelo de un mensaje de
respaldo. Sin él, quien llama no tendría forma de saber si el coach de verdad
respondió.

### La firma que ya piensa en F3

```python
async def handle_message(
    self,
    message: str,
    profile: dict | None = None,
    history: list[dict[str, str]] | None = None,
) -> CoachReply:
```

F1 nunca pasa `profile` ni `history`: no hay base de datos todavía. Aun así los
parámetros existen.

**Por qué así.** Cuando F3 agregue la memoria, va a llenar esos dos argumentos
desde la base. Si no estuvieran, F3 tendría que cambiar la firma, y con ella
todos los llamadores y todas las pruebas. Dejarlos preparados convierte F3 en
cablear en lugar de reescribir.

### Devolver, no enviar

```python
        return CoachReply(text=reply)
```

El agente termina devolviendo un objeto. **No envía nada.** Quien lo llamó decide
si eso se convierte en una respuesta HTTP, en audio hablado o en un mensaje de
Telegram.

Esta es la decisión de arquitectura central del proyecto. En el primer intento,
el agente llamaba directamente a `telegram_service.send_text()`. Con esa forma,
añadir la interfaz web habría obligado a duplicar todo el pipeline o a meter
banderas de canal por todas partes. Separando la generación de la entrega, el
mismo agente sirve a la web, a la voz de F2 y al bot de F6 sin tocarlo.

### Degradar en vez de reventar

```python
except GeminiUnavailableError:
    logger.warning("Coach falling back to degraded reply")
    return CoachReply(text=DEGRADED_MESSAGE, degraded=True)
```

Si el modelo no responde, el corredor recibe un mensaje escrito en lugar de un
error. La petición devuelve HTTP 200.

Puede parecer discutible devolver 200 ante un fallo. El razonamiento: un error de
cuota no es problema del corredor, que está a mitad de una conversación. La
bandera `degraded` transporta la verdad para quien la necesite, y `/health`
reporta la causa real para quien opera el sistema.

---

## `src/dependencies.py`

```python
@lru_cache
def get_gemini() -> GeminiService:
    return GeminiService()


@lru_cache
def get_coach() -> CoachAgent:
    return CoachAgent(gemini=get_gemini())
```

Este archivo es el *composition root*: el único lugar donde se decide qué
implementación concreta usa cada cosa.

Nada se construye al importar. Las funciones se ejecutan la primera vez que
FastAPI las necesita, y `@lru_cache` garantiza que haya una sola instancia por
proceso.

`get_coach` construye el agente pidiendo el servicio a `get_gemini`, en lugar de
crear uno propio. Así ambos comparten la misma instancia, y sustituir el servicio
en las pruebas sustituye el que usa el agente.

---

## `src/routes/chat.py`

```python
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    username: str | None = Field(default=None, max_length=100)
```

Un modelo de Pydantic define la forma de la petición. FastAPI lo usa para validar
automáticamente: si llega un mensaje vacío o de más de 4000 caracteres, responde
422 sin que el código del endpoint llegue a ejecutarse.

Ese límite superior no es decorativo: evita que alguien envíe un texto enorme que
consumiría tokens y cuota.

```python
@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, coach: CoachAgent = Depends(get_coach)):
```

`Depends(get_coach)` es la inyección de dependencias de FastAPI. En lugar de que
el endpoint construya el agente, lo pide y FastAPI se lo entrega llamando a
`get_coach()`.

La ventaja aparece en las pruebas: `app.dependency_overrides[get_coach]` permite
sustituir esa función por otra que devuelve un doble, sin tocar el código de
producción.

```python
    try:
        reply = await coach.handle_message(payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

El agente lanza `ValueError` ante un mensaje vacío. La ruta lo traduce a un
código HTTP. Esa traducción vive aquí y no en el agente, porque el agente no debe
saber que existe HTTP: en F2 lo va a llamar un WebSocket y en F6 un bot.

---

## Las pruebas

### El doble del modelo

```python
class StubGemini:
    def __init__(self, reply: str = "Sube de veinte a veintidós kilómetros.") -> None:
        self.reply = reply
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None

    async def generate(self, message, system_prompt, history=None):
        self.calls.append({"message": message, "system_prompt": system_prompt,
                           "history": history})
        if self.fail_with is not None:
            raise self.fail_with
        return self.reply
```

Un *stub* es un reemplazo controlado. Este hace tres cosas:

1. Devuelve una respuesta fija, así las pruebas son deterministas.
2. **Guarda lo que le pidieron** en `self.calls`, lo que permite comprobar qué
   prompt construyó el coach, no solo qué devolvió.
3. Puede fallar a voluntad con `fail_with`, para probar la degradación.

Gracias a esto la suite no toca la red, no gasta cuota, corre en medio segundo y
funciona sin credenciales.

### La prueba que verifica el prompt

```python
async def test_the_persona_travels_as_system_prompt_not_as_a_user_turn(coach, gemini):
    await coach.handle_message("hola")
    call = gemini.calls[0]
    assert call["message"] == "hola"
    assert "diez por ciento" in call["system_prompt"]
```

Comprueba que la regla del diez por ciento viaja en `system_prompt` y no mezclada
en el mensaje. Es una prueba sobre la *arquitectura*, no sobre el resultado.

### La regla que no se relaja

```python
@pytest.mark.parametrize("profile", [
    {},
    {"goal": "5K"},
    {"experience_level": "avanzado"},
    {"goal": "Maratón", "experience_level": "avanzado", "weekly_km": 90},
])
def test_volume_cap_is_present_for_every_profile_shape(profile):
    assert "diez por ciento" in build_system_prompt(profile)
```

`@pytest.mark.parametrize` ejecuta la misma prueba una vez por cada caso de la
lista, y las reporta por separado.

El caso interesante es el último: un corredor avanzado que ya hace 90 km
semanales. Es exactamente el perfil donde uno podría sentirse tentado de relajar
la regla. La prueba lo impide.

---

## La verificación

Las pruebas usan un doble, así que por definición no demuestran que el sistema
real funcione. Por eso F1 cerró con una comprobación contra la API en vivo, a
través de la aplicación corriendo y no de un script suelto contra el SDK.

**Petición normal**, 1.68 segundos:

> Veinte kilómetros semanales son una buena base [...] Antes de armarte el plan
> exacto, dime qué tiempo sueles tardar en hacer esos diez kilómetros.

Preguntó en lugar de suponer, como pide la persona.

**Dolor agudo con petición de medicamento:**

> Para ya mismo con ese entrenamiento [...] debes parar y consultar con un
> médico. Yo no puedo diagnosticarte nada y, por seguridad, tampoco puedo
> recomendarte ningún medicamento.

**Presión para saltarse el tope:**

> Como entrenador no puedo darte ese plan porque nunca aumento el volumen
> semanal más de un diez por ciento, lo que significa que esta semana pasarías
> de tus veinte actuales a veintidós kilómetros como mucho.

Las tres respuestas llegaron en prosa conversacional, sin markdown. Es la
comprobación de que el prompt está listo para la voz de F2.

---

## Lo que F1 no hace

El coach **no recuerda nada**. Cada petición es independiente: si le dices tu
objetivo y luego preguntas otra cosa, ya lo olvidó.

Es intencional. La memoria es F3, y meterla aquí habría mezclado dos problemas
distintos en un solo fragmento. La firma de `handle_message` ya la espera.
