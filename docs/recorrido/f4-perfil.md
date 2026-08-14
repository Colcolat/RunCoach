# F4: el perfil

Commits `3d82776` (extracción), `b8393f8` (el calendario y el trinquete) y
`88e317c` (los límites de frecuencia). Un archivo nuevo, 19 pruebas añadidas.

F3 hizo que el coach recordara la conversación. Este fragmento hace que recuerde
al corredor, que no es lo mismo: la conversación se acaba, el perfil no.

---

## El punto de partida

Las columnas estaban desde F3 y nadie las escribía.

```python
goal: Mapped[str | None] = mapped_column(String(20), default=None)
experience_level: Mapped[str | None] = mapped_column(String(20), default=None)
weekly_km: Mapped[float | None] = mapped_column(default=None)
race_date: Mapped[str | None] = mapped_column(String(10), default=None)
```

`build_system_prompt` ya las inyectaba. `update_profile` ya filtraba campos
desconocidos "porque F4 va a llenar esto con salida de un modelo". Estaba todo
esperando a que alguien lo conectara.

Por eso este fragmento fue cablear, no reescribir.

---

## La restricción que da forma a todo

El nivel gratuito da **500 peticiones de texto al día**. Extraer en cada turno
significa dos llamadas por turno, y la capacidad de la aplicación cae a 250
conversaciones diarias.

Esa cuenta es la que decide el diseño de este fragmento. No se extrae siempre:
se extrae cuando el turno plausiblemente trae algo.

---

## `src/coaching/extraction.py`

Lógica de dominio, como `prompts.py`: sin imports del SDK y sin I/O. Las reglas
que deciden cuándo gastar una petición y qué es creíble se prueban sin red y sin
clave.

### La compuerta

```python
def mentions_profile_information(message: str) -> bool:
    text = _strip_accents((message or "").lower())
    if _DIGIT.search(text):
        return True
    words = set(_WORD.findall(text))
    if words & _TRIGGER_WORDS:
        return True
    return any(word.startswith("veinti") for word in words)
```

Un turno sin números, sin distancias, sin niveles y sin meses no lleva nada que
extraer. En una conversación real la mayoría son así: "gracias", "¿y cómo
respiro en las cuestas?", "vale, lo intento".

**Los números escritos con letra cuentan tanto como los dígitos.** Los turnos de
voz llegan transcritos, y la Live API escribe "quince kilómetros", no "15 km".
Una compuerta que solo mirara dígitos sería ciega justo en el camino principal
del producto.

```python
# "un" y "una" quedan fuera a propósito
```

Son artículos mucho más a menudo que cantidades. Incluirlos haría que "dame una
respuesta corta" gastara una petición. Y "un kilómetro" se captura igual, por la
palabra de distancia.

La compuerta es deliberadamente generosa. Un falso positivo cuesta una petición
de quinientas; un falso negativo pierde un campo del perfil hasta que el corredor
lo repita.

### Por qué el esquema no es texto libre

```python
GOALS = ("5K", "10K", "21K", "Maratón")
LEVELS = ("principiante", "intermedio", "avanzado")
```

Estos valores se usan como **claves** en `GOAL_GUIDANCE` y `EXPERIENCE_GUIDANCE`.
Un valor fuera de la lista se guardaría sin quejarse y luego no seleccionaría
ninguna guía. El síntoma no sería un error, sería "el coach suena algo genérico",
que es mucho peor de encontrar.

Dos pruebas lo vigilan:

```python
def test_every_goal_the_model_may_return_selects_guidance():
    assert set(GOALS) == set(GOAL_GUIDANCE)
```

### La fecha de hoy va en el prompt

```python
return f"""Extraes datos del perfil de un corredor...

Hoy es {today.isoformat()}.
```

Sin esa línea, "octubre" se fecha en el año del corte de entrenamiento del
modelo, que ya pasó. Una fecha de carrera en el pasado desactiva en silencio
todo razonamiento sobre plazos.

Esto se arregla dos veces a propósito: el prompt lo dice, y el validador rechaza
fechas pasadas de todos modos.

### La distinción que ya costó cara

```
weekly_km es el volumen que corre en una SEMANA COMPLETA. La distancia de una
sola sesión o la distancia más larga que aguanta de un tirón NO son el volumen
semanal.
```

Es la misma distinción que el prompt del coach hace desde F3, cuando confundirlas
convirtió a un corredor de 3 km semanales en un plan de 14. La diferencia es que
ahora el error sería **persistente**: se escribiría en la base y acompañaría al
corredor en todas sus conversaciones futuras.

### Del último mensaje, no de todo el historial

```
El dato tiene que venir del ÚLTIMO mensaje del corredor. Los turnos anteriores
están ahí solo para entenderlo, por ejemplo para saber a qué pregunta responde
un "quince" suelto.
```

El extractor ve seis turnos de contexto porque `"quince"` a secas no significa
nada. Pero ver historial trae su propio riesgo: volver a extraer un valor viejo
que el corredor ya corrigió. De ahí la instrucción.

### La validación

```python
def clean(raw: dict | None, today: date) -> dict:
    candidates = {...}
    return {key: value for key, value in candidates.items() if value is not None}
```

Devuelve **solo lo que sobrevive**. Un diccionario vacío significa "no escribas
nada", no "escribe nulos encima de lo que ya sabíamos". Un campo que el corredor
no mencionó este turno nunca debe borrar lo que dijo la semana pasada.

Lo que se rechaza y por qué:

| campo | regla | qué previene |
|---|---|---|
| `goal` | debe estar en `GOALS` | un objetivo que no selecciona guía |
| `experience_level` | debe estar en `LEVELS` | lo mismo |
| `weekly_km` | `0 < km <= 300` | un maratoniano de élite hace 250; 400 es una mala lectura |
| `race_date` | ISO, ni pasada ni a más de 5 años | el modelo fechando en su año de corte |

"que parseó" no es lo mismo que "que es verdad", y este perfil es duradero de una
forma que una respuesta no lo es.

---

## `CoachAgent.converse`

```python
reply, updates = await asyncio.gather(
    self.handle_message(text, profile=user, history=history),
    self._read_profile(text, history),
)
```

Las dos llamadas arrancan a la vez, así que un turno que además se lee cuesta el
mismo tiempo de reloj que uno que no. En serie doblarían la espera, y esto es un
producto de voz donde la latencia **es** la experiencia.

Medido contra la API real: 2.5 s con extracción, 1.2 s cuando la compuerta la
salta.

### El perfil se escribe después de responder

La respuesta de este turno se compuso con el perfil anterior, y no cuesta nada:
el modelo ya vio al corredor decirlo, en este mismo mensaje. Lo que compra la
escritura es la **sesión siguiente**, cuando la conversación ya no está y el
perfil es lo único que queda.

### Nunca revienta

```python
except Exception:  # noqa: BLE001
    logger.exception("Profile extraction failed")
    return {}
```

La respuesta es lo que el corredor pidió. El perfil es un efecto secundario que
mejora la próxima conversación. Un fallo al extraer no puede costarle la
respuesta.

---

## Lo que apareció al verificar

### El calendario

La fecha se guardaba y se inyectaba como `2026-10-01` a secas. El modelo no tiene
un sentido fiable de qué día es hoy, así que no distinguía una carrera a seis
semanas de una a veinte, que es la forma entera del plan. La guía de 21K razona
en semanas: "un bloque de doce a dieciséis semanas".

Ahora llega con la cuenta hecha:

```
Fecha de la carrera: 2026-10-01 (faltan 6 semanas)
```

`today` se pasa como parámetro en lugar de leerse del reloj, para que
`prompts.py` siga sin I/O y las semanas se puedan probar contra una fecha fija.

### El trinquete, y la prueba que borré

Observado una vez, con transcripción. Con 18 km en el registro:

| turno | qué dijo el coach |
|---|---|
| n | "vamos a subir a diecinueve y medio" |
| n+1 | "como **ya estás** en diecinueve y medio... subimos a veintiuno y medio" |

Contra los 18 que el corredor declaró, eso es un **19 por ciento**, y nadie había
corrido un kilómetro de más.

La causa es la regla de F1 de que la conversación manda sobre el perfil. Es
cierta para lo que dijo el corredor y falsa para lo que propuso el coach. El
persona ahora separa las dos mitades:

```
Un plan que tú propusiste no es kilometraje que el corredor haya corrido.
```

**Escribí una prueba live para esto y la borré.** En cuatro formulaciones
distintas pasó igual con la regla que sin ella: no discriminaba nada.

Publicar una prueba que siempre pasa es exactamente el fallo del que trata F3,
donde cuatro pruebas afirmaban que el tope del diez por ciento estaba en el
prompt y ninguna detectó al modelo ignorándolo. Una prueba no está verificada
hasta que se la ha visto fallar.

La regla se queda, porque la transcripción es real y la regla es correcta. Lo que
no se queda es la afirmación de que hay algo cuidándola. La prueba unitaria
comprueba que el texto está en el prompt, y eso es todo lo que hace.

---

## El límite que F4 destapó

Reportado usando la aplicación: una conversación murió a mitad de turno. La
consola de Google dijo por qué, y no era la cuota diaria:

| modelo | RPM | RPD |
|---|---|---|
| `gemini-3.5-flash-lite` | **22 / 15** | 110 / 500 |
| `gemini-3.1-flash-lite` | 1 / 15 | 1 / 500 |

Peticiones **por minuto**, no por día. F4 hizo que cada turno cualificado costara
dos peticiones contra un mismo techo de quince, y `asyncio.gather` las dispara en
el mismo instante. Un corredor escribiendo a ritmo normal se pasa, mientras 390
peticiones del día quedan sin usar.

Los límites son por modelo, así que la extracción corre en su propio id:

```python
gemini_extraction_model: str = "gemini-3.1-flash-lite"
```

Misma familia, mismo precio, bucket propio. Y leer cuatro campos de una frase no
necesita el modelo mejor. Verificado que extrae bien en el nuevo, trampa
incluida.

Hay una prueba que afirma que los dos ids son distintos, porque ponerlos iguales
devolvería el problema en silencio.

### El 429 dice la verdad

```python
class GeminiRateLimitedError(GeminiUnavailableError):
```

Subclase, no tipo aparte: todos los llamadores que no distinguen siguen
degradando exactamente igual, y los que sí pueden separar un límite que se
despeja en un minuto de un modelo genuinamente caído.

> Vamos más rápido de lo que el servicio permite ahora mismo. Espera unos
> segundos y vuelve a preguntar: no he perdido el hilo de la conversación.

Esa última frase es la que más importa. El mensaje genérico le decía al corredor
que el entrenador estaba inalcanzable, cuando la verdad es que fue más rápido de
lo que permite el nivel gratuito, y no le decía lo único que necesitaba saber.

---

## Verificación

Cuatro turnos por HTTP contra la API real, leyendo la base después de cada uno:

| turno | perfil resultante |
|---|---|
| "15 km/semana, un 10K en octubre" | `10K` · `15.0` · `2026-10-01` |
| "¿cómo respiro en las cuestas?" | sin segunda llamada |
| "el domingo hice 9 km seguidos" | `weekly_km` **sigue en 15.0** |
| "ya voy por 18 km/semana" | `18.0`, objetivo conservado |

Y el ajuste del modelo de extracción, apuntándolo a un id inexistente: la
extracción da 404, el perfil se lee vacío, y el coach responde igual.

---

## Lo que F4 no hace

**No confirma lo que dedujo.** El extractor devuelve null cuando tendría que
suponer, que es la forma práctica de "confirmar en vez de asumir": si el dato no
se dijo explícitamente, el perfil sigue diciendo "sin registrar" y el coach
pregunta de forma natural. Un mecanismo de confirmación explícita sería más
completo y bastante más caro en peticiones.

**No hay historial de perfil.** Se guarda el valor actual, no la serie. Saber que
alguien pasó de 15 a 18 en tres semanas sería útil para un entrenador y necesita
otra tabla.

**El volumen no caduca.** Un corredor que declaró 20 km y desaparece seis meses
vuelve con 20 km en el registro. El coach debería sospechar de un dato viejo y
todavía no lo hace.
