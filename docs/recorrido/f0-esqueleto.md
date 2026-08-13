# F0: el esqueleto que arranca

Commit `0e4e746`. Cinco archivos de código, cuatro pruebas, 308 líneas en total.

El objetivo de F0 no era construir funcionalidad sino **dejar el terreno
verificado**: que las dependencias instalen, que la aplicación levante, que las
pruebas corran. Un cimiento sobre el que los siguientes fragmentos solo agregan.

---

## `requirements.txt`

```
fastapi>=0.141,<0.142
uvicorn[standard]>=0.52,<0.53
pydantic>=2.13,<3
pydantic-settings>=2.15,<3
SQLAlchemy>=2.0.52,<2.1
python-dotenv>=1.2,<2
pytest>=9.1,<10
pytest-asyncio>=1.4,<2
pytest-cov>=7.1,<8
httpx>=0.28,<0.29
```

Las versiones usan **pisos con techo**: `>=0.141,<0.142` significa "al menos
esta, pero no la siguiente versión mayor". Se evita `==` exacto porque impide
recibir parches de seguridad, y se evita dejarlo abierto porque una versión
mayor puede romper la API.

**Por qué así.** El primer intento del proyecto usaba pins exactos de 2023
(`pydantic==2.5.0`, `aiohttp==3.9.1`). En Python 3.14 esas versiones no tienen
*wheel* precompilado, así que `pip` intentaba compilarlas desde código fuente y
fallaba. Un *wheel* es un paquete ya compilado para tu sistema operativo y
versión de Python; sin él, pip necesita un compilador de C instalado.

La comprobación que garantiza que esto no vuelva a pasar:

```bash
pip install -r requirements.txt --only-binary=:all:
```

`--only-binary=:all:` prohíbe compilar. Si el comando pasa, ninguna dependencia
necesita compilador, y quien clone el repositorio no se va a topar con un muro.

El archivo **crece con el código**. F0 no usa Gemini, así que `google-genai` no
aparece hasta F1. Un manifiesto que declara lo que no se usa miente sobre las
dependencias reales del proyecto.

---

## `src/config.py`

```python
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: str = "sqlite:///./runcoach.db"
```

`BaseSettings` viene de `pydantic-settings`. Al instanciar la clase, lee cada
campo desde una variable de entorno con el mismo nombre en mayúsculas: el campo
`database_url` se llena con `DATABASE_URL`. Si la variable no existe, usa el
valor por defecto escrito a la derecha del `=`.

`SettingsConfigDict` configura ese comportamiento:

- `env_file=".env"` — también lee de un archivo `.env` en la raíz
- `extra="ignore"` — si `.env` trae variables que la clase no declara, las
  ignora en vez de reventar. Sin esto, agregar una variable temporal al `.env`
  rompería el arranque.

`Literal["DEBUG", "INFO", "WARNING", "ERROR"]` es un tipo que solo admite esos
cuatro textos exactos. Pydantic lo valida al arrancar: si alguien pone
`LOG_LEVEL=VERBOSE`, la aplicación falla de inmediato con un mensaje claro, en
lugar de arrancar y comportarse raro después.

```python
    @property
    def gemini_enabled(self) -> bool:
        return bool(self.google_api_key.strip())
```

Un `@property` es un método que se usa como si fuera un atributo:
`settings.gemini_enabled`, sin paréntesis. Sirve para calcular algo derivado sin
que el que lo usa tenga que saber cómo se calcula.

El `.strip()` importa más de lo que parece: quita espacios en blanco. Sin él,
una clave configurada como `GOOGLE_API_KEY="   "` contaría como configurada y el
fallo aparecería mucho después, al llamar a la API, en lugar de aquí.

```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`@lru_cache` guarda el resultado de la función. La primera llamada construye el
objeto `Settings` y lee el `.env`; todas las siguientes devuelven exactamente el
mismo objeto sin volver a leer el disco.

**Por qué así.** La alternativa obvia sería `settings = Settings()` a nivel de
módulo. Eso ejecuta la lectura en el momento en que alguien hace
`import src.config`, es decir, antes de que las pruebas puedan preparar el
entorno. Con la función cacheada, las pruebas cambian las variables y luego
llaman a `get_settings.cache_clear()` para forzar una relectura. Esa capacidad de
limpiar el caché es lo que hace el módulo comprobable.

---

## `src/database.py`

```python
@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=False, connect_args=connect_args)
```

El *engine* de SQLAlchemy es el objeto que gestiona las conexiones. Se construye
una sola vez por proceso porque mantiene un *pool*: un conjunto de conexiones
reutilizables. Crear uno por consulta desperdiciaría ese trabajo.

`check_same_thread: False` merece explicación. SQLite, por defecto, prohíbe usar
una conexión desde un hilo distinto al que la creó, como medida de seguridad.
Pero FastAPI ejecuta las funciones síncronas (las declaradas con `def` y no con
`async def`) en un *threadpool*, o sea en hilos distintos. Sin desactivar esa
comprobación, cualquier consulta fallaría. Solo se aplica a SQLite; PostgreSQL
no tiene esa restricción, y por eso el `if`.

```python
@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)
```

Una *session* de SQLAlchemy es una conversación con la base: acumula cambios y
los envía juntos. `sessionmaker` es una fábrica que produce sesiones ya
configuradas.

`expire_on_commit=False` evita un comportamiento que sorprende: por defecto,
después de un `commit()`, SQLAlchemy marca los objetos como "caducados" y vuelve
a consultarlos a la base la próxima vez que se lee un atributo. Si la sesión ya
se cerró, ese acceso lanza `DetachedInstanceError`. Desactivarlo evita esa clase
de error.

```python
@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

`@contextmanager` convierte una función generadora en algo usable con `with`. El
código antes del `yield` se ejecuta al entrar; el de después, al salir.

El patrón garantiza tres cosas: si el bloque termina bien se confirma
(`commit`); si lanza una excepción se revierte (`rollback`) y la excepción sigue
subiendo (`raise`); y pase lo que pase la sesión se cierra (`finally`). Sin este
patrón, una excepción a mitad de una operación dejaría la sesión abierta y con
cambios a medias.

```python
def check_connection() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connectivity check failed")
        return False
```

`SELECT 1` es la consulta más barata posible: no lee ninguna tabla, solo
comprueba que la conexión responde. `text()` es necesario porque SQLAlchemy 2.0
exige marcar el SQL escrito a mano de forma explícita, para que no se confunda
con las consultas construidas por el ORM.

`logger.exception` registra el mensaje **y** la traza completa del error. Es
distinto de `logger.error`, que solo registra el texto. Aquí interesa la traza
porque el error se está tragando para devolver `False`.

---

## `src/routes/health.py`

```python
router = APIRouter(tags=["health"])

VERSION = "0.1.0"    # en F1 pasó a "0.2.0"


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check() -> dict:
    database_ok = check_connection()
    return {
        "status": "healthy" if database_ok else "degraded",
        ...
    }
```

Un `APIRouter` agrupa rutas relacionadas para registrarlas juntas en la
aplicación. `tags=["health"]` solo afecta a la documentación generada: agrupa
estos endpoints bajo ese título en `/docs`.

**Por qué así.** La versión original de este endpoint en el primer intento
devolvía un diccionario fijo con `"status": "healthy"` escrito a mano. Eso es
peor que no tener chequeo: responde "sano" incluso con la base caída, así que un
operador lo consulta, ve verde y busca el problema en otro lado.

Aquí `database_ok` viene de ejecutar una consulta real. El endpoint puede
responder `degraded`, y hay una prueba que lo obliga a hacerlo.

El campo `timestamp` usa `datetime.now(timezone.utc)`, no `datetime.now()`. El
segundo devuelve la hora local sin zona horaria, lo cual es ambiguo: un registro
que dice "11:27" no significa nada sin saber desde dónde se generó. Con UTC
explícito, el `isoformat()` incluye el sufijo `+00:00`.

---

## `src/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format=...)

    if not check_connection():
        logger.error("Database unreachable at startup")

    logger.info("RunCoach started")
    yield
    logger.info("RunCoach stopped")
```

`lifespan` es el mecanismo de FastAPI para ejecutar código al arrancar y al
apagar. Todo lo anterior al `yield` corre una vez al levantar el servidor; lo
posterior, al cerrarlo. Es donde van las conexiones a servicios externos, los
planificadores de tareas y las comprobaciones de arranque.

El detalle que importa: cuando la base no responde **se registra el error pero
no se aborta el arranque**. La tentación es hacer que falle rápido, pero
entonces `/health` tampoco estaría disponible, y ese es exactamente el momento
en que alguien necesita consultarlo para saber qué pasa.

```python
@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "internal_server_error"})
```

Red de seguridad para cualquier excepción no capturada. Sin esto, FastAPI
devolvería la traza completa en la respuesta HTTP, exponiendo rutas de archivos
y estructura interna a quien haga la petición. Aquí la traza va al registro del
servidor, donde sirve, y el cliente recibe un mensaje genérico.

---

## `tests/conftest.py`

`conftest.py` es un archivo especial de pytest: las *fixtures* que define están
disponibles en todas las pruebas de esa carpeta sin necesidad de importarlas.

Una *fixture* es una función que prepara algo para una prueba. Se pide
declarándola como parámetro de la prueba, y pytest la inyecta.

```python
@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    yield
    get_settings.cache_clear()
    ...
```

`autouse=True` la aplica a todas las pruebas automáticamente, sin declararla.

`tmp_path` es una fixture de pytest que da una carpeta temporal distinta para
cada prueba, borrada al terminar. Cada prueba trabaja sobre su propio archivo
SQLite y no puede contaminar a las demás.

`monkeypatch` permite modificar variables de entorno, atributos u objetos, y
**deshace los cambios automáticamente** al terminar la prueba. Cambiar
`os.environ` a mano dejaría el cambio activo para el resto de la sesión.

Las tres llamadas a `cache_clear()` son la parte sutil. Sin ellas, la secuencia
sería: se cambia `DATABASE_URL`, pero `get_settings` ya tiene el valor viejo
guardado del arranque, así que devuelve la configuración anterior y la prueba
usa la base real. El caché se limpia antes (para que la prueba vea el valor
nuevo) y después (para no dejar el caché sucio a la siguiente).

---

## `tests/test_health.py`

Cuatro pruebas. Vale la pena mirar dos.

```python
def test_health_degrades_when_the_database_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("src.routes.health.check_connection", lambda: False)
    body = client.get("/health").json()
    assert body["status"] == "degraded"
```

Sustituye `check_connection` dentro del módulo de la ruta por una función que
siempre devuelve `False`, y comprueba que el endpoint reacciona.

**Es la prueba más importante de F0.** La contraria (que responda `healthy`
cuando todo va bien) pasaría igual con el diccionario fijo del primer intento.
Solo esta demuestra que el chequeo mide algo de verdad.

Nótese que se parchea `src.routes.health.check_connection` y no
`src.database.check_connection`. Cuando un módulo hace
`from src.database import check_connection`, se crea una referencia local en
`src.routes.health`. Parchear el origen no cambia esa referencia ya copiada; hay
que parchear donde se usa.

```python
def test_health_timestamp_is_timezone_aware(client):
    timestamp = datetime.fromisoformat(client.get("/health").json()["timestamp"])
    assert timestamp.tzinfo is not None
```

Verifica que la marca de tiempo lleva zona horaria. Una fecha sin zona es
ambigua en cuanto el servidor se despliega en otra región, que es exactamente lo
que va a pasar en F7 al subir a AWS.

---

## Lo que F0 dejó verificado

```
instalación limpia    ok, solo wheels, sin compilador
pytest                4 passed
GET /health           {"status":"healthy","components":{"database":"connected"}}
```

Nada de esto se dio por supuesto: se ejecutó antes de comitear. El fragmento
siguiente construye sobre terreno probado.
