# RunCoach

Un entrenador de running con el que se habla. No un chat al que se le escribe:
una conversación de voz, en la que se puede interrumpir, preguntar de nuevo y
seguir el hilo entre sesiones.

Prepara corredores de cualquier nivel para 5K, 10K, 21K y maratón.

Ejercicio técnico para la vacante de Practicante Dual en Adivor (Aldan
Ingeniería).

---

## La idea

Un corredor que empieza no necesita una tabla de Excel. Necesita preguntar
"me duele la rodilla desde el domingo, ¿corro hoy?" y recibir una respuesta que
tome en cuenta lo que corrió esta semana y para qué carrera se está preparando.

Eso es difícil de escribir y fácil de decir. Por eso la interfaz principal es la
voz, y por eso el sistema recuerda: la conversación de hoy sabe lo que se dijo
la semana pasada.

Tres decisiones dan forma al proyecto:

**La voz es conversación, no dictado.** RunCoach usa la Live API de Gemini, que
mantiene una sesión de audio bidireccional. El corredor puede interrumpir a
media frase, igual que haría con un entrenador de carne y hueso. La alternativa
habitual, transcribir y luego sintetizar, produce un intercambio de notas de voz,
no un diálogo.

**Las reglas de entrenamiento son código, no sugerencias al modelo.** El tope de
10 por ciento de incremento semanal, el día de descanso obligatorio y la
derivación médica ante dolor agudo viven en un módulo de dominio con pruebas
propias. Un cambio en la filosofía de entrenamiento es un diff revisable, no una
frase escondida en un prompt.

**Recordar es la mitad del trabajo.** Un entrenador que olvida el historial no
entrena, responde preguntas sueltas. La conversación persiste, y con ella el
objetivo del corredor, su nivel y su volumen semanal.

---

## Estado

El proyecto se construye por fragmentos. Cada uno es una rama, termina con la
suite en verde y se comitea sólo después de haberse ejecutado.

| Fragmento | Alcance | Estado |
|---|---|---|
| F0 | Esqueleto: aplicación, configuración, salud | Hecho |
| F1 | Cerebro del coach: persona, reglas, chat de texto | Hecho |
| F2 | Voz conversacional con la Live API | Hecho |
| F3 | Memoria entre sesiones | Hecho |
| F4 | Perfil del corredor extraído de la conversación | Siguiente |
| F5 | Interfaz web | Planeado |
| F6 | Recordatorios proactivos por Telegram | Planeado |
| F7 | Despliegue | Planeado |
| F8 | Entrega | Planeado |

Hoy corre: se le puede **hablar** y contesta con voz, en cualquier navegador con
micrófono. También responde por escrito. **Y recuerda**: la conversación persiste
entre recargas, y hablar y escribir alimentan un mismo historial, así que se
puede empezar por voz y seguir por texto sin perder el hilo. Todavía no extrae un
perfil estructurado de lo que se le cuenta; esa es F4. Este README se actualiza
conforme cada fragmento existe, no antes.

Una conversación hablada real, verificada de punta a punta contra la API:

> **[hablado]** Hola entrenador, corro veinte kilómetros por semana y quiero un
> medio maratón.
>
> **[hablado]** Veinte kilómetros ya son una buena base. Para armar el plan,
> dime cuánto tiempo llevas corriendo de forma constante y para cuándo tienes
> pensada la carrera. Así sumamos los kilómetros sin pasarnos de ese diez por
> ciento.

La regla del diez por ciento aparece sin que nadie la mencione: la persona llega
igual a la sesión de voz que al chat de texto, porque ambos la toman del mismo
módulo.

Y por escrito:

> **Corro 20 km por semana y quiero un 21K en diciembre. ¿Por dónde empiezo?**
>
> Veinte kilómetros semanales son una buena base para empezar a preparar un
> medio maratón de cara a diciembre. Antes de armarte el plan exacto, dime qué
> tiempo sueles tardar en hacer esos diez kilómetros o cinco kilómetros para
> saber cómo estás de ritmo.

Preguntó en lugar de suponer. Y bajo presión sostiene las reglas:

> **Quiero llegar a 60 km la próxima semana, tengo prisa. Dame el plan.**
>
> Entiendo que tengas prisa, pero subir de veinte a sesenta kilómetros de golpe
> es una receta segura para lesionarte. Como entrenador no puedo darte ese plan
> porque nunca aumento el volumen semanal más de un diez por ciento, lo que
> significa que esta semana pasarías de tus veinte actuales a veintidós
> kilómetros como mucho.

---

## Arquitectura

Lo que ya existe está en trazo continuo; lo planeado, con guiones.

```
Navegador                             Telegram (F6)
  voz   <==WebSocket==>                 texto
  texto <==HTTP=======>                   :
        |                                 :
        +============ FastAPI ............+
                         |
              +==========+==========+
              |                     |
         CoachAgent            /ws/voice
    (devuelve la respuesta;   (proxy hacia
     quien llama la entrega)   Gemini Live)
              |                     |
              +==========+==========+
                         |
        +================+===================+
        |                |                   |
    Reglas de      Gemini texto        Persistencia
  entrenamiento    + Gemini Live          (SQLite)
```

El navegador nunca ve la clave de API: cada trama de audio pasa por el servidor.

El audio tiene dos frecuencias distintas, y no es una errata. La API acepta PCM
de 16 bits a 16 kHz y devuelve a 24 kHz. Ambas viajan en el saludo inicial del
WebSocket para que el cliente no las tenga escritas a mano y se desincronicen en
silencio.

`CoachAgent` no envía nada: devuelve la respuesta y quien la pidió decide si eso
se convierte en audio, en una respuesta HTTP o en un mensaje de Telegram. Esa
separación es lo que permite que una sola implementación del coach sirva a la web
y al bot sin duplicar el pipeline.

Las sesiones de base de datos no salen de la capa de persistencia; quien llama
recibe diccionarios e identificadores. Eso elimina por construcción una familia
entera de errores de objetos desconectados.

---

## Decisiones técnicas

Cada elección está medida contra la API real, no supuesta.

| Capa | Elección | Razón |
|---|---|---|
| API | FastAPI | Async nativo, tipado, documentación generada |
| Voz | Gemini Live (`gemini-3.1-flash-live-preview`) | Audio bidireccional nativo; el navegador sólo necesita micrófono y WebSocket, así que funciona en cualquiera |
| Texto | `gemini-3.5-flash-lite` | 1.26 s medidos, sin razonamiento interno; 500 requests/día vs. 20 de los modelos no-lite |
| Persistencia | SQLite con SQLAlchemy 2.0 | Cero configuración; el ORM deja abierta la ruta a PostgreSQL |
| Recordatorios | Telegram con APScheduler | Lo único que una pestaña cerrada no puede hacer |

**Sobre los modelos:** Los modelos con razonamiento interno resultaron entre cinco
y trece veces más lentos, y sus tokens de razonamiento se descuentan del
presupuesto de salida. Con un presupuesto de 400 tokens, uno gastó 382 pensando
y emitió catorce caracteres. Para una conversación hablada, donde la latencia es
la experiencia, el modelo ligero gana.

**Sobre la voz:** Se midieron los dos modelos Live disponibles antes de elegir.

| modelo | primer audio | tokens/turno | límite |
|---|---|---|---|
| `gemini-3.1-flash-live-preview` | **0.83 s** | **291** | 65K tokens/min |
| `gemini-2.5-flash-native-audio-latest` | 3.01 s | 480 | 1M tokens/min |

El elegido gana en las dos dimensiones que importan pese a tener el techo de
tokens más bajo. Ese techo resultó holgado: a unos 470 tokens por turno real,
son del orden de veinte conversaciones simultáneas.

**Sobre la captura de audio:** `MediaRecorder`, que es la forma evidente de
grabar en el navegador, produce contenedores webm/opus y la API quiere PCM
crudo. Por eso la captura usa un AudioWorklet, que entrega las muestras sin
codificar, y el remuestreo a 16 kHz se hace a mano.

**Restricción de cuota:** El tier gratuito da 500 requests/día para los modelos
de texto. Gastar dos llamadas por turno (respuesta más extracción de perfil)
reduciría la capacidad a 250 turnos diarios. La extracción de perfil (F4) se
dispara solo cuando el turno plausiblemente trae información nueva, no en cada
mensaje.

---

## Cómo correrlo

Requiere Python 3.10 o superior. Verificado en 3.14.2.

```bash
git clone https://github.com/Colcolat/RunCoach.git
cd RunCoach
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

En Linux o macOS la activación es `source venv/bin/activate`.

Todas las dependencias tienen rueda precompilada, de modo que no hace falta
compilador:

```bash
pip install -r requirements.txt --only-binary=:all:
```

Configuración:

```bash
cp .env.example .env
```

Arranque:

```bash
uvicorn src.main:app --reload --port 8000
```

Abre `http://localhost:8000` y pulsa **Hablar**. El navegador pedirá permiso del
micrófono. La documentación interactiva de la API queda en `/docs`.

El micrófono requiere un contexto seguro: los navegadores lo permiten en
`localhost`, pero al desplegar hace falta HTTPS. Eso se resuelve en F7.

Sin cliente web, por escrito:

```bash
curl -s -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"Corro 20 km por semana, quiero un 21K\"}"
```

Sin `GOOGLE_API_KEY` la aplicación arranca igual: `/health` reporta
`not_configured`, la voz responde con una caída a texto y el chat contesta con un
mensaje de respaldo en lugar de fallar. Eso mantiene la suite y el chequeo de
salud utilizables en integración continua.

---

## Pruebas

```bash
pytest
```

```bash
pytest --cov=src --cov-report=term-missing
```

Las pruebas no tocan la red. El modelo y el bot se sustituyen por dobles, así
que la suite es determinista y corre sin credenciales.

125 pruebas sin red, 91 por ciento de cobertura. Las reglas de entrenamiento, el
agente y la persistencia están al 100; lo que falta es el camino de red de los
clientes.

Hay además seis pruebas que **sí** llaman al modelo real, porque verifican algo
que ninguna prueba con dobles puede: que el coach *obedezca* sus reglas, no que
las tenga escritas. Esa distinción no es teórica. La regla del diez por ciento
estaba en el prompt y tenía cuatro pruebas, y aun así el coach le propuso 14 km
semanales a alguien que corría 3.

```bash
pytest -m live
```

Gastan cuota, así que están excluidas de la ejecución normal, y van limitadas a
una llamada cada 4.2 segundos: a toda velocidad se pasan de las quince peticiones
por minuto del nivel gratuito y fallan con errores 429 que se leen como fallos de
comportamiento.

La voz se prueba contra una sesión Live falsa que reproduce un guion de mensajes
del servidor. Eso cubre el protocolo del WebSocket, el presupuesto de voz y la
caída a texto sin abrir un socket real. Que el audio suene de verdad es una
pregunta de navegador, y se verificó a mano.

`GET /health` ejecuta una consulta real contra la base en lugar de devolver una
respuesta fija, y hay una prueba que fuerza específicamente la rama degradada:
un chequeo de salud que no puede fallar no informa nada.

Las pruebas que más importan son las que fijan la seguridad: que el tope del
diez por ciento aparece en el prompt para cualquier perfil, incluido el de un
corredor avanzado; que un corredor de 5K no recibe guía de maratón; y que un
fallo del modelo degrada en lugar de propagarse.

---

## Recorrido por el código

[`docs/recorrido/`](docs/recorrido/) explica cada fragmento archivo por archivo:
qué hace, por qué está escrito así y qué error concreto evita cada decisión. Es
el razonamiento que un diff no muestra.

- [F0: el esqueleto](docs/recorrido/f0-esqueleto.md)
- [F1: el cerebro del coach](docs/recorrido/f1-cerebro-del-coach.md)
- [F2: la voz](docs/recorrido/f2-voz.md)
- [F3: la memoria](docs/recorrido/f3-memoria.md)

---

## Alcance excluido a propósito

Se documenta para que las ausencias se lean como decisiones y no como olvidos.

- **WhatsApp**: exige Twilio de pago o aprobación de Business API, sin aportar
  ninguna capacidad que Telegram no dé ya.
- **Autenticación**: el identificador de sesión no está autenticado. Es adecuado
  para una demostración y no para producción.
- **PostgreSQL**: SQLite alcanza a esta escala. La ruta de migración queda
  documentada en lugar de ejecutada.
- **Importación desde Strava**: atractiva, pero OAuth más límites de tasa no
  cabe en el tiempo disponible.

---

## Autor

Juan José Zapata Buenfil
TSU en Desarrollo de Software
https://github.com/Colcolat
