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
| F1 | Cerebro del coach: persona, reglas, chat de texto | Siguiente |
| F2 | Voz conversacional con la Live API | Planeado |
| F3 | Memoria entre sesiones | Planeado |
| F4 | Perfil del corredor extraído de la conversación | Planeado |
| F5 | Interfaz web | Planeado |
| F6 | Recordatorios proactivos por Telegram | Planeado |
| F7 | Despliegue | Planeado |
| F8 | Entrega | Planeado |

Hoy corre: la aplicación arranca, `GET /health` reporta estado medido y la suite
pasa. Nada más. Este README se actualiza conforme cada fragmento existe, no
antes.

---

## Arquitectura prevista

```
Navegador                          Telegram
  voz  <--WebSocket-->               texto
  texto <--HTTP------>                 |
        |                              |
        +---------- FastAPI -----------+
                       |
                  CoachAgent
          (devuelve la respuesta;
           quien llama la entrega)
                       |
        +--------------+--------------+
        |              |              |
   Persistencia    Gemini        Reglas de
    (SQLite)    Live + texto    entrenamiento
```

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
| Voz | Gemini Live (modelo pendiente) | Audio bidireccional nativo; el navegador sólo necesita micrófono y WebSocket, así que funciona en cualquiera |
| Texto | `gemini-3.5-flash-lite` | 1.26 s medidos, sin razonamiento interno; 500 requests/día vs. 20 de los modelos no-lite |
| Persistencia | SQLite con SQLAlchemy 2.0 | Cero configuración; el ORM deja abierta la ruta a PostgreSQL |
| Recordatorios | Telegram con APScheduler | Lo único que una pestaña cerrada no puede hacer |

**Sobre los modelos:** Los modelos con razonamiento interno resultaron entre cinco
y trece veces más lentos, y sus tokens de razonamiento se descuentan del
presupuesto de salida. Con un presupuesto de 400 tokens, uno gastó 382 pensando
y emitió catorce caracteres. Para una conversación hablada, donde la latencia es
la experiencia, el modelo ligero gana.

**Sobre la voz:** Dos modelos Live cumplen. `gemini-3.1-flash-live-preview` tiene
65K tokens/minuto; `gemini-2.5-flash-native-audio-latest` tiene 1M. El modelo
elegido se decide en F2 midiendo consumo real de una conversación hablada, no a
ojo. El tope de minutos de voz por sesión se dimensiona con la misma medición.

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

La documentación interactiva queda en `http://localhost:8000/docs`.

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

`GET /health` ejecuta una consulta real contra la base en lugar de devolver una
respuesta fija, y hay una prueba que fuerza específicamente la rama degradada:
un chequeo de salud que no puede fallar no informa nada.

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
