# F6: los recordatorios

Commit `1bf15ab`, más cuatro correcciones posteriores que salieron de usar el
producto y no de las pruebas. Un modelo nuevo, dos servicios, un barrido y 34
pruebas.

Hasta aquí el coach solo sabía contestar. Esta es la parte que busca al corredor,
que es lo único que una pestaña cerrada no puede hacer.

---

## Los recordatorios son filas, no trabajos programados

APScheduler trae un almacén que persiste trabajos, y era la opción obvia. No es
la que se tomó, y la razón merece decirse porque parece que se rechaza algo que
funciona.

Un trabajo persistido de APScheduler es un *callable* serializado junto a sus
argumentos. Eso sobrevive a un reinicio, pero **no sobrevive a un despliegue que
renombre la función o le cambie la firma**. Y falla en el momento de dispararse,
no al importar, que es el peor instante posible para enterarse: de madrugada, sin
nadie mirando, en el código que precisamente actúa solo.

Así que el scheduler aquí es **solo un reloj**:

```python
scheduler.add_job(run_due_reminders, trigger="interval", seconds=60, ...)
```

Un único trabajo recurrente que pregunta "¿qué toca?". Todo lo que decide algo
vive en la base de datos y en [`src/coaching/reminders.py`](../../src/coaching/reminders.py),
donde es una función pura de tres marcas de tiempo.

La consecuencia es que **reiniciar deja de ser interesante**. No hay estado del
scheduler que persistir, porque el estado nunca estuvo en el scheduler. Eso se
verificó como pedía el plan: recordatorio a dos minutos, proceso muerto en medio,
y entregado a tiempo por un proceso distinto.

```
13:42:52  proceso NUEVO arranca (el anterior ya no existe)
13:44:08  Sent 1 reminder(s)
```

---

## Enlace profundo, no código que teclear

El corredor entra por la web y no tiene nada que ver con Telegram. ¿Cómo le
llega un aviso?

```python
return f"https://t.me/{self.username}?start={session_id}"
```

Telegram entrega al bot lo que venga después de `start=`, así que el id de sesión
viaja con el toque. Un solo gesto ata ese chat a la conversación que **ya
existe**, con su historial y su perfil. Sin tabla de códigos, sin caducidades,
sin un segundo paso.

La prueba de que funciona no es que el bot conteste, sino esto:

> **[por Telegram]** ¿Cuál era mi objetivo?
>
> Tu objetivo es preparar y terminar un maratón.

Ese dato nunca se le dijo al bot. Venía del perfil de la sesión web.

### Quien abre el bot sin venir de la web

```python
if not _looks_like_a_session(payload):
    return UNLINKED_GREETING
```

Recibe instrucciones, **no un perfil vacío nuevo**. Crear silenciosamente un
segundo corredor es exactamente lo que el enlace existe para evitar, y hacerlo
aquí lo derrotaría.

---

## Polling, no webhook

Un webhook necesita una dirección HTTPS pública, que no existe hasta F7 y nunca
existe en un portátil. Y necesita un secreto, para que quien encuentre la URL no
pueda publicar actualizaciones haciéndose pasar por Telegram.

El primer intento de este proyecto tenía un webhook que aceptaba peticiones sin
autenticar. **No tener el endpoint es mejor arreglo que acordarse de
protegerlo.**

El polling cuesta una conexión saliente y funciona idéntico en todos los
entornos.

---

## Dos reglas cuya ausencia es invisible

**La ventana de gracia.** Un recordatorio de las 07:00 que el servidor no pudo
entregar porque estaba caído sí se manda a las 09:00; a las 23:00 no.

```python
if local_now - scheduled > DAILY_GRACE:
    return False
```

Un aviso para salir a correr a las once de la noche es peor que ningún aviso.

**El enfriamiento.** Sin él, el mismo silencio dispara un aviso en cada barrido:
uno por minuto, para siempre. Así es como un bot útil se convierte en la razón
por la que alguien lo bloquea.

Ambas son puras y se prueban sin reloj:

```python
def test_but_not_hours_late():
    assert daily_is_due("07:00", None, utc(2026, 8, 20, 20, 0), MADRID) is False
```

---

## La hora se dice hablando

No hay formulario de configuración. Se pide como se pide todo lo demás:

> recuérdame a las siete

F4 lo extrae igual que extrae el objetivo, pero el resultado **no es una columna
del perfil**, es una fila en `reminders`. El agente lo separa antes de escribir
el resto:

```python
at_time = updates.pop(REMINDER_FIELD, None)
if at_time:
    self._db.set_daily_reminder(user["id"], at_time)
```

El prompt distingue dos frases que se parecen mucho: *"suelo correr a las siete"*
es cuándo entrena, y va a null. *"recuérdame a las siete"* es una petición.

---

## Cuatro fallos que las pruebas no encontraron

Las 34 pruebas de este fragmento pasaban, y el producto tenía cuatro defectos.
Los cuatro salieron de usarlo en un teléfono.

### El coach negaba poder hacerlo

De una conversación real:

> **corredor:** ¿me puedes recordar en Telegram en 2 minutos?
>
> **coach:** No puedo hacer eso, mi trabajo es solo ayudarte con el
> entrenamiento.

Y **tenía razón en decirlo**. Nada en su persona mencionaba los recordatorios, y
un modelo al que le piden algo fuera de su encargo declina educadamente.

F6 estaba construido, probado, desplegado y funcionando, y era **invisible para
la única parte del sistema con la que un corredor habla**. Un revisor que
hiciera esa pregunta concluiría que el extra opcional nunca se hizo.

Es el fallo más instructivo del proyecto: cobertura completa de una función que,
desde fuera, no existía.

### El extractor no tenía reloj

Se le pasaba la fecha de hoy pero no la hora, así que *"en dos minutos"* no tenía
contra qué resolverse y devolvía null en silencio. Ahora recibe la hora local del
corredor —local, no UTC, porque es la zona en la que se leerá de vuelta— y acepta
las dos formas.

### Un corredor activo iba camino de que le dijeran que había desaparecido

`last_seen_at` se actualizaba con `onupdate=utcnow`, que solo dispara **cuando
cambia la fila del usuario**. Y un turno normal cambia mensajes, no al corredor.

O sea: alguien que hablara con el coach cada mañana, y cuyo perfil ya estuviera
completo, dejaba de actualizar esa columna. A los tres días el barrido decidía
que se había ido en silencio.

`touch_last_seen` ya existía escrita exactamente para esto, y **nunca se
llamaba**. Código muerto que parecía cubrir un caso.

### Un float en una pantalla de bloqueo

> Vas por **3.0** kilómetros a la semana

La columna es un float porque los medios kilómetros existen. Pero un número
entero que llega como `3.0` parece una base de datos derramada sobre el teléfono
de alguien.

---

## Nada de esto puede tumbar la aplicación

Es la única parte del sistema que actúa sin que se lo pidan, así que es también
la única que puede fallar sin que nadie mire.

- el bot y el scheduler arrancan dentro de `try`, y si fallan la web sigue
- `run_due_reminders` **nunca lanza**: una excepción escapando de ahí mataría el
  trabajo y con él todos los recordatorios futuros, en silencio
- una entrega fallida deja `last_sent_at` intacto, así que se reintenta
- `/health` reporta el bot sin sondearlo, y no cuenta para `status`: un
  despliegue sin Telegram debe servir, no entrar en bucle de reinicio

---

## Lo que F6 no hace

**Una sola zona horaria.** Los recordatorios se leen en `America/Mexico_City`
para todo el mundo. El navegador conoce la suya y pedírsela es un fragmento más.

**Un recordatorio diario por corredor.** Decir "recuérdame a las siete" dos veces
mueve la hora, no añade una segunda alarma. Es lo que la gente quiere decir, pero
no permite dos avisos al día.

**No hay forma de cancelarlo hablando.** Se puede cambiar la hora, no apagarlo.
