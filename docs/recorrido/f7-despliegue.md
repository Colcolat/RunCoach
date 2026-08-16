# F7: el despliegue

Commits `84b0989` a `c963b23`. Tres archivos en [`deploy/`](../../deploy/), un
`Dockerfile` que sobrevive a un cambio de plan, y dos fallos que solo podían
aparecer desplegando.

El plan decía que este fragmento **no se sacrifica nunca**: la entrega es un
correo, y lo primero que hace el revisor es abrir el enlace.

---

## Fly.io se construyó entero y se abandonó

La primera plataforma elegida fue Fly.io, por una razón concreta: HTTPS
automático sin comprar dominio, y el micrófono **exige contexto seguro**. Sin
TLS, la función principal del producto no funciona en un navegador.

Se escribió el `Dockerfile`, se escribió `fly.toml`, y se verificó el contenedor
entero en local: `/health`, los assets, un turno real, polling de Telegram, y
—lo importante— destruir el contenedor y recrearlo para comprobar que el volumen
conservaba los datos.

Entonces, mirando la facturación, resultó que **la asignación gratuita ya no
existe**. Nuestra configuración —una máquina que nunca duerme, porque una máquina
dormida deja de hacer polling y de barrer recordatorios— habría costado unos
dólares al mes de forma continua.

Se cambió a AWS, donde la capa gratuita da doce meses de verdad. El coste del
cambio fue tiempo: nginx, systemd, certbot y un subdominio.

**El `Dockerfile` se quedó**, y no por nostalgia. Es lo que demostró que la
aplicación corre en contenedor, y es el camino más corto a cualquier otra
plataforma si esta se acaba.

---

## Let's Encrypt no emite para amazonaws.com

El hostname que AWS regala no sirve: ese dominio está en la Public Suffix List y
Let's Encrypt no emite certificados para él. Hace falta un dominio propio.

Comprar uno el domingo por la tarde, con la entrega el lunes, era un riesgo
innecesario. **DuckDNS** da un subdominio gratis al instante y Let's Encrypt sí
emite para él.

Un detalle que costó un intento: DuckDNS rellena el campo de IP automáticamente
con **la IP desde la que entras**, no con la del servidor. Apuntaba al router de
casa. Con eso, certbot valida contra la máquina equivocada y el certificado nunca
sale.

---

## Las dos cosas de nginx que no son decoración

```nginx
location /ws/ {
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

**El upgrade de WebSocket.** Un proxy que no reenvía esas cabeceras convierte la
voz en una petición HTTP corriente que falla en el handshake. El cliente entonces
cae a texto y la función estrella simplemente no está, **sin nada en los
registros que parezca un error**.

Este era el riesgo abierto más grande del plan, señalado desde el primer día como
algo que se prueba en F7 y no se descubre el día de la entrega. Se verificó
conectando de verdad:

```
HANDSHAKE OK -- nginx reenvio el upgrade
{"type": "ready", "input_sample_rate": 16000, "output_sample_rate": 24000}
```

**El tiempo de espera.** nginx cierra una conexión ociosa a los 60 segundos por
defecto. Una sesión de voz en la que el corredor se para a pensar un minuto se
cortaría a media conversación, y eso parece exactamente un fallo de la
aplicación.

---

## El servicio reinicia siempre

```ini
Restart=always
```

Esto no sirve solo páginas: hace polling de Telegram y barre recordatorios. Un
proceso muerto a las tres de la mañana no deja de atender visitas —no hay
visitas a esa hora—, deja de **avisar a la gente**, y nadie se entera hasta que
un corredor lo menciona.

Los secretos llegan por `EnvironmentFile`, no en el archivo de unidad ni en los
argumentos, donde `ps` los mostraría a cualquiera con acceso a la máquina.

Y la base vive en `/opt/runcoach/data`, **fuera del checkout**, para que el
`git reset --hard` que actualiza el código no se lleve el historial de nadie.

---

## Escrito para ejecutarse dos veces

Cada paso de [`setup.sh`](../../deploy/setup.sh) comprueba antes de actuar. Un
fallo a la mitad se arregla **volviéndolo a lanzar**, no averiguando qué quedó
hecho. Esa propiedad importa cuando lo que se aprovisiona es el entregable y la
fecha es mañana.

Lo cual hace más vergonzoso que la primera versión no pudiera ejecutarse dos
veces.

---

## Dos fallos que Docker en local no podía encontrar

### nginx 1.24 no entiende `http2 on`

Esa directiva llegó en nginx 1.25 y Ubuntu 24.04 LTS trae la 1.24, donde es un
error fatal, no algo que se ignore.

Falló de la peor forma disponible: **nginx siguió sirviendo la configuración
anterior desde memoria**, así que el sitio parecía perfectamente sano mientras el
archivo en disco lo habría matado al siguiente reinicio.

La forma antigua, `listen 443 ssl http2`, está deprecada en 1.25+ pero se sigue
aceptando. Funciona en las dos versiones; la nueva funciona en una, y no es la
que se despliega.

### El script no podía correr dos veces

La primera pasada entrega el checkout al usuario del servicio, y git entonces se
niega a tocarlo como root: *detected dubious ownership*. La segunda pasada moría
en el paso que actualiza el código.

Un script escrito para ser reejecutable que no lo es, es solo un script con un
comentario que miente.

---

## La verificación

Desde fuera, como lo encontrará el revisor:

- TLS validado por `curl` **sin** `--insecure`
- HTTP redirige a HTTPS
- todos los assets sirviendo
- `/health` reporta cada componente
- un turno real con la regla del diez por ciento aplicada y el perfil persistido
- el WebSocket de voz completa su handshake y la Live API responde

Y después, **reiniciando la instancia entera** sin tocar nada más: volvió en 60
segundos, ambos servicios arrancaron solos, el swap se remontó desde `fstab`, la
conversación sobrevivió y `certbot renew --dry-run` pasa.

---

## Detalles pequeños que evitan sorpresas

**Swap.** Las imágenes de Ubuntu en la nube no traen, y `t3.micro` tiene 1 GiB de
RAM. Sin swap el kernel no se ralentiza cuando falta memoria: mata un proceso. Un
archivo de 1 GiB convierte un fallo duro en un momento lento.

**Finales de línea.** El repositorio se desarrolla en Windows. Un `setup.sh` con
CRLF falla como *bad interpreter: no such file or directory*, que es una forma
confusa de aprender sobre finales de línea con un despliegue en marcha.
`.gitattributes` los fuerza a LF.

---

## Lo que F7 no hace

**No hay IP elástica.** Parar y arrancar la instancia cambia la IP pública y deja
el DNS apuntando al vacío. Un reinicio la conserva; un stop/start no.

**No hay copias de seguridad automáticas.** La base vive en el disco de la
instancia. Para una demostración alcanza; para datos que importen, no.

**No hay despliegue continuo.** Actualizar es entrar por SSH y ejecutar el
script. Con un solo servidor y una sola persona, un pipeline sería más máquina
que trabajo.
