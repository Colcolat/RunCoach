# Prompts para Stitch — F5

Guion para generar la interfaz en https://stitch.withgoogle.com

Los prompts van en inglés porque la herramienta responde mejor, pero **todos los
textos de la interfaz están en español y deben salir en español exactamente como
aparecen aquí**. Cada bloque lleva encima una nota de qué busca.

Ve en orden. El prompt 1 crea la pantalla; del 2 en adelante son refinamientos
sobre lo que ya generó, no pantallas nuevas.

---

## Antes de empezar

Tres cosas que conviene fijar en Stitch antes del primer prompt:

- **Tema oscuro.** La aplicación ya es oscura y el contraste va bien para una
  pantalla que se mira mientras se corre o se camina.
- **Sin emoji.** Stitch los mete por su cuenta si no se lo prohíbes. Está dicho
  en el prompt, pero si aparecen, pídeselo otra vez.
- **Español.** Si te devuelve algo en inglés, añade "keep all UI copy in Spanish
  exactly as given".

---

## Prompt 1 — La pantalla principal

Esta es la importante. Establece la jerarquía: la voz manda, la conversación va
debajo, el perfil a un lado.

```
Design a voice-first running coach web app called RunCoach, in dark mode,
in Spanish.

The defining element is a large circular microphone button, centered near the
top, roughly 140px across. This is the primary action and must dominate the
screen: it is a voice product, not a chat app with a microphone bolted on.
Around the circle, a thin ring that can animate. Below the button, a single line
of status text in Spanish.

The default state shows the label "Hablar" and the status line reads
"Listo para hablar o escribir".

Below the voice control, a conversation transcript. Coach messages align left on
a raised dark surface; runner messages align right on a slightly blue-grey
surface. Rounded corners, generous line height, comfortable reading width.
Sample content, in Spanish, exactly as written:

Coach: "Hola, soy tu entrenador. Cuéntame qué quieres preparar, si un 5K, un
10K, un medio maratón o un maratón, y cuánto estás corriendo ahora por semana."
Runner: "Corro 15 kilómetros por semana y quiero un 10K en octubre"
Coach: "Ahora corres quince kilómetros, así que esta semana vamos a dieciséis
y medio para sumar sin pasarnos. El martes cinco suaves, el jueves cinco y el
domingo seis y medio."

At the bottom, a secondary text input with the placeholder
"O escribe tu pregunta" and a send button labelled "Enviar". It must read as an
alternative to speaking, clearly less prominent than the microphone.

On desktop, a right-hand sidebar about 280px wide titled "Tu perfil".
On mobile, everything stacks in one column and the profile becomes a collapsed
summary strip above the conversation.

Use a calm, athletic palette: near-black background, a green accent for the
active and listening states, warm grey for secondary text. Modern sans-serif.
No emoji anywhere, no icons made of emoji.
```

---

## Prompt 2 — Los tres estados de la voz

El brief pide que el estado sea visible. Esto es lo que separa "parece que
funciona" de "sé qué está pasando".

```
Now show the microphone control in its three active states, as separate
variants of the same component. The state must be readable without relying on
colour alone: the ring, the label and the status text all change.

1. Listening. The ring pulses slowly in green. Button label "Terminar".
   Status text: "Escuchando".

2. Thinking. The ring shows an indeterminate sweep in muted grey.
   Button label "Terminar". Status text: "Pensando".

3. Speaking. The ring shows a soft audio-level animation in a warmer tone,
   suggesting sound coming out rather than going in. Button label "Terminar".
   Status text: "El entrenador está hablando".

Also show a fourth, non-active state for when voice is unavailable: the circle
is dimmed and not pulsing, and the status line reads
"La voz no está disponible ahora. Seguimos por escrito."
```

---

## Prompt 3 — El panel de perfil

Los campos son exactamente los que la aplicación guarda. Nada inventado: si
Stitch añade "calorías" o "pulsaciones", eso no existe y hay que quitarlo.

```
Design the "Tu perfil" panel in detail. It holds exactly four fields and no
others. Every field can be empty, because the coach fills them in from
conversation over time.

- "Objetivo" — one of: 5K, 10K, 21K, Maratón
- "Nivel" — one of: principiante, intermedio, avanzado
- "Volumen semanal" — a number of kilometres, shown as "15 km por semana"
- "Carrera" — a date, shown with the time remaining, as
  "1 de octubre de 2026" with "faltan 6 semanas" underneath

Show two versions of the panel side by side:

A) Complete: Objetivo 10K, Nivel intermedio, Volumen semanal 15 km por semana,
   Carrera 1 de octubre de 2026, faltan 6 semanas.

B) Empty, for a first-time visitor. Fields that are not known yet read
   "sin registrar" in muted grey, and a short line at the top of the panel
   explains: "Se completa solo conforme hablas con el entrenador."

The panel is informational, not a form. There are no input fields and no save
button: the runner never types this in, the coach hears it. Make that obvious
in the visual treatment.

On mobile, collapse this into a single horizontal strip showing only the fields
that are known, as small labelled chips.
```

---

## Prompt 4 — Móvil

Verificación en móvil está en el plan. Pídeselo explícitamente.

```
Show the mobile layout at 390px wide. The microphone circle stays the largest
element on screen and must be reachable with one thumb, so place it in the
lower half rather than at the very top. The conversation scrolls above it. The
text input sits at the very bottom.

The profile appears as a compact horizontal strip of chips just under the
header, showing only known fields, for example: "10K", "15 km/semana",
"faltan 6 semanas".
```

---

## Prompt 5 — Detalles que suelen faltar

Opcional, pero son los que hacen que parezca acabado.

```
Refine three details:

1. The transcript of a phrase still being spoken appears in the conversation at
   reduced opacity, because it is not final until the speaker finishes. Show one
   message in that provisional state.

2. A returning runner sees their previous conversation replayed on load, with a
   subtle divider above it labelled "Retomamos donde lo dejaste".

3. Show the rate-limit message as a coach message with a distinct, calmer
   treatment, so it does not look like a failure:
   "Vamos más rápido de lo que el servicio permite ahora mismo. Espera unos
   segundos y vuelve a preguntar: no he perdido el hilo de la conversación."
```

---

## Qué necesito de vuelta

Cuando tengas un diseño que te guste, pásame **el código**, no capturas. En
Stitch se exporta con **Copy code** o **Export to Figma**; lo que me sirve es el
HTML y el CSS.

Si el diseño acabó en varias pantallas, pásame todas: la principal, los estados
de la voz y el panel de perfil.

Con eso lo adapto: lo conecto a las rutas reales, le meto los estados que el
WebSocket ya emite, lo hago responsive de verdad y lo dejo funcionando.

Una captura también ayuda para comparar el resultado adaptado con lo que
esperabas, pero de la captura sola no puedo sacar el CSS.

---

## Lo que no hay que pedirle a Stitch

Stitch diseña pantallas, no lógica. Estas cosas ya existen y las cablea el
código, así que si aparecen en el diseño se ignoran:

- Nada de pantalla de login ni registro. No hay cuentas; el corredor se
  identifica por un id de sesión en el navegador.
- Nada de formulario de perfil. El perfil se rellena hablando.
- Nada de gráficas ni historial de kilometraje. No se guarda la serie, solo el
  valor actual. Eso sería otro fragmento.
- Nada de ajustes ni preferencias. No hay ninguna todavía.
