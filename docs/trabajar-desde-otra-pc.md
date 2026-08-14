# Trabajar desde otra máquina

Todo lo de aquí está ejecutado, no supuesto: se clonó el repositorio desde
GitHub en un directorio limpio, se instaló de cero y se corrió la aplicación
contra la API real. Los comandos son los que funcionaron, copiados tal cual.

---

## Lo que necesitas llevarte

Solo dos cosas, porque el resto está en el repositorio:

1. **Python 3.10 o superior.** Verificado en 3.14.2.
2. **Tu clave de Gemini.** No está en GitHub y no debe estarlo. Si no la
   tienes a mano, se saca de nuevo en https://aistudio.google.com/apikey

No necesitas compilador, ni base de datos instalada, ni nada más.

---

## Puesta en marcha

### 1. Clonar

```bash
git clone https://github.com/Colcolat/RunCoach.git
```

```bash
cd RunCoach
```

### 2. Crear el entorno

```bash
py -m venv venv
```

En Windows usa `py`, no `python`. El `python` pelado puede resolver a un stub de
la Microsoft Store que no crea entornos y falla con un mensaje confuso. En Linux
o macOS es `python3 -m venv venv`.

### 3. Instalar

```bash
venv/Scripts/python.exe -m pip install -r requirements.txt --only-binary=:all:
```

En Linux o macOS la ruta es `venv/bin/python`.

Dos detalles que no son accidentales:

**Se llama al intérprete del entorno por su ruta completa** en lugar de activar
el entorno primero. Activar es un paso que se olvida, y olvidarlo instala las
dependencias en el Python del sistema, donde funcionan hasta que dejan de
hacerlo. Llamándolo por ruta no hay forma de equivocarse.

**`--only-binary=:all:` obliga a instalar solo desde rueda precompilada.** Si el
comando termina bien, queda probado que esta máquina no necesita compilador. Si
algún pin no tuviera rueda, fallaría aquí en lugar de intentar compilar y
reventar diez minutos después con un error de C.

Debe terminar sin errores e instalar estas versiones:

```
fastapi 0.141.1 · google-genai 2.18.1 · SQLAlchemy 2.0.52 · pydantic 2.13.4
pydantic-settings 2.15.0 · uvicorn 0.52.3 · pytest 9.1.1 · httpx 0.28.1
```

### 4. Configurar

```bash
cp .env.example .env
```

Abre `.env` y pega tu clave en la línea `GOOGLE_API_KEY=`. Todo lo demás tiene un
valor por defecto que funciona, así que un `.env` con solo esa línea rellenada es
perfectamente válido.

No juzgues la clave por su prefijo. Google ha usado más de un formato y una clave
buena puede no parecerse a la que viste en un tutorial. La forma de comprobarla
es `/health`, más abajo.

`.env` está en `.gitignore` y nunca debe commitearse.

---

## Comprobar que funciona

### La suite, primero

```bash
venv/Scripts/python.exe -m pytest
```

Esperado: **199 pruebas en verde, 6 deseleccionadas**. Las 6 son las que llaman a
la API real y están excluidas a propósito.

La suite no toca la red y no necesita clave. Si esto pasa, la instalación está
bien aunque la clave esté mal.

### Levantar la aplicación

Desde la raíz del proyecto:

```bash
venv/Scripts/python.exe -m uvicorn src.main:app --reload --port 8000
```

Desde la raíz porque `src` tiene que ser importable y porque `.env` se busca en
el directorio de trabajo.

### Verificar la clave

En otra terminal:

```bash
curl -s http://localhost:8000/health
```

Lo que importa es el campo `gemini`:

- `"gemini":"configured"` — la clave se leyó, todo listo
- `"gemini":"not_configured"` — el `.env` no se está leyendo o la línea está vacía

Si sale `not_configured`, revisa que el archivo se llame exactamente `.env` (sin
`.txt` detrás, que es lo que hace el Bloc de notas de Windows) y que lo levantaste
desde la raíz del proyecto.

`/health` consulta la base de datos de verdad, así que un `"database":"connected"`
significa que SQLite funciona, no que alguien devolvió una constante.

### Un turno real

Abre `http://localhost:8000` y escribe. O sin navegador:

```bash
curl -s -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d "{\"message\":\"Corro 12 km por semana y quiero un 10K\",\"session_id\":\"prueba\"}"
```

Verificado desde un clon limpio, la respuesta aplicó la regla del diez por ciento
(doce a trece y medio) y el perfil quedó guardado como `10K` y `12.0`.

---

## Sin clave también arranca

Si prefieres no gastar cuota mientras programas, deja `GOOGLE_API_KEY` vacía. La
aplicación arranca igual: `/health` reporta `not_configured`, la voz cae a texto y
el chat contesta con un mensaje de respaldo en lugar de romperse.

Es útil para trabajar en la interfaz, en la persistencia o en las rutas sin gastar
una sola petición. Toda la suite corre en este modo.

---

## Lo que no viaja contigo

Estas cosas están en `.gitignore` y **no** llegan con el clon:

| qué | por qué |
|---|---|
| `.env` | Contiene tu clave |
| `venv/` | Se recrea; los binarios no son portables entre máquinas |
| `runcoach.db` | Es tu base local; el clon empieza vacío |
| `__pycache__/`, `.pytest_cache/` | Regenerados |

**La base de datos no viaja.** El clon arranca sin conversaciones y sin perfiles,
y crea las tablas solo al arrancar. Es lo correcto: son tus datos de prueba, no
código.

Si quisieras llevarte una conversación concreta para reproducir algo, copia
`runcoach.db` a mano por USB o similar. No lo commitees.

---

## Trabajar en los dos sitios

El riesgo real de dos días fuera no es la instalación, es acabar con dos ramas
divergentes.

**Antes de empezar a tocar nada, en la laptop:**

```bash
git pull
```

**Antes de cerrar, en la laptop:**

```bash
git status
```

```bash
git push
```

**Al volver a casa, antes de nada:**

```bash
git pull
```

Si `git pull` se queja de cambios locales sin commitear, guárdalos primero:

```bash
git stash
```

y recupéralos después del pull con `git stash pop`.

### Sobre las ramas

La convención del proyecto es que cada fragmento vive en su rama y entra a
`master` con `--no-ff`. Si empiezas F5 en la laptop:

```bash
git checkout -b feature/f5-interfaz
```

```bash
git push -u origin feature/f5-interfaz
```

El `-u` la deja enlazada, así que después basta `git push`. Y desde casa la
recuperas con `git fetch && git checkout feature/f5-interfaz`.

---

## Los límites de cuota, que muerden

El nivel gratuito da, **por cada modelo de texto**:

- 500 peticiones al día
- **15 peticiones por minuto**

El límite por minuto es el que sorprende: se agota probando la aplicación a mano,
sin acercarse siquiera al diario. Si te topas con él, el coach responde:

> Vamos más rápido de lo que el servicio permite ahora mismo. Espera unos
> segundos y vuelve a preguntar: no he perdido el hilo de la conversación.

No es un error: es el mensaje de límite de frecuencia, y la conversación sigue
intacta. Espera medio minuto.

**La cuota es del proyecto de Google, no de la clave.** Generar una clave nueva
en el mismo proyecto no da cuota nueva. Solo un proyecto nuevo tendría su propio
cupo, y no debería hacer falta.

El consumo se ve en https://aistudio.google.com, mirando las columnas RPM y RPD
por separado.

### Programar sin gastar

```bash
venv/Scripts/python.exe -m pytest
```

No gasta nada. Las 6 pruebas que sí llaman a la API están excluidas por defecto y
solo corren si las pides:

```bash
venv/Scripts/python.exe -m pytest -m live
```

Esas tardan unos dos minutos porque se auto-limitan a una llamada cada 4.2
segundos, justo por debajo de las 15 por minuto.

Y si quieres la aplicación viva pero sin gastar en extracción de perfil, en
`.env`:

```
PROFILE_EXTRACTION_ENABLED=false
```

---

## Si algo falla

**`pip install` falla con un error de compilación.** No debería, porque
`--only-binary=:all:` lo impide. Si pasa, es que tu Python es más nuevo que las
ruedas disponibles; comprueba la versión con `py --version`.

**`ModuleNotFoundError: No module named 'src'`.** Estás lanzando uvicorn desde
otro directorio. Vuelve a la raíz del proyecto.

**`/health` dice `not_configured` con la clave puesta.** El archivo no se llama
`.env`, o no estás en la raíz. En Windows, comprueba que el explorador no le haya
añadido `.txt`.

**El micrófono no arranca.** Necesita contexto seguro. `localhost` cuenta, así que
funciona en local; al desplegar hará falta HTTPS, y eso es F7.

**Las pruebas fallan nada más clonar.** No debería pasar: se verificaron 199 en
verde sobre un clon limpio sin `.env`. Si ocurre, pega la salida completa antes de
tocar nada.

---

## Resumen, para copiar y pegar

```bash
git clone https://github.com/Colcolat/RunCoach.git && cd RunCoach
```

```bash
py -m venv venv && venv/Scripts/python.exe -m pip install -r requirements.txt --only-binary=:all:
```

```bash
cp .env.example .env
```

```bash
venv/Scripts/python.exe -m pytest
```

```bash
venv/Scripts/python.exe -m uvicorn src.main:app --reload --port 8000
```
