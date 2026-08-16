# Recorrido por el código

Una guía por fragmento que explica qué hace cada archivo, por qué está escrito
así y qué error concreto evita cada decisión.

No es documentación de API: para eso está `/docs`, que FastAPI genera solo. Esto
es el razonamiento detrás del código, que es justo lo que un diff no muestra.

| Fragmento | Guía | Cubre |
|---|---|---|
| F0 | [Esqueleto](f0-esqueleto.md) | Configuración, base de datos, salud, arranque, pruebas |
| F1 | [Cerebro del coach](f1-cerebro-del-coach.md) | Persona, reglas, cliente de Gemini, agente, rutas |
| F2 | [La voz](f2-voz.md) | Live API, proxy WebSocket, captura de audio, presupuesto |
| F3 | [La memoria](f3-memoria.md) | Modelos, persistencia, historial unificado, reglas verificadas |
| F4 | [El perfil](f4-perfil.md) | Extracción estructurada, compuerta de cuota, validación, límites por modelo |
| F5 | [La interfaz](f5-interfaz.md) | Diseño adaptado, estados de voz, panel de perfil, layout responsive |
| F6 | [Los recordatorios](f6-recordatorios.md) | Filas en vez de jobs, enlace profundo, ventana de gracia, enfriamiento |
| F7 | [El despliegue](f7-despliegue.md) | EC2, nginx con WebSocket, TLS, systemd, y por qué se abandonó Fly |

## Cómo leerlas

Cada archivo se explica de arriba abajo, con el código a la vista y la
explicación debajo. Los conceptos de Python o de las librerías que no son
evidentes se explican donde aparecen, no en un glosario aparte.

Los bloques marcados **Por qué así** contienen la parte que importa: la
alternativa que se descartó y la razón. Muchas de esas razones vienen de errores
reales cometidos en un primer intento del proyecto que se descartó por completo.
