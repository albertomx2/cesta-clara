# Cesta Clara

Proyecto de lista de la compra que consulta precios de supermercados españoles, compara productos por precio unitario y estima el coste de una cesta. **Se publica como ejemplo de proyecto, no como servicio público ni aplicación de producción.** No contiene listas personales, bases de datos ni direcciones de una instalación privada.

## Funcionalidades

- Buscar productos y añadirlos a una lista persistente.
- Elegir supermercados, cambiar cantidades y marcar productos comprados.
- Priorizar €/kg, €/litro o €/unidad comparable; usar el precio del envase si no hay datos unitarios.
- Mostrar fuentes y fecha de consulta, y refrescar precios.
- Señalar bajadas diarias de precio cuando coinciden con la fuente de promociones.
- Comparar una cesta en una sola tienda o repartirla entre tiendas, indicando cobertura.

**Los resultados son estimaciones.** Las búsquedas pueden devolver marcas y formatos diferentes. Los totales multiplican el precio del envase seleccionado por la cantidad de la lista; no comparan necesariamente un mismo peso total ni incluyen desplazamientos.

## Arquitectura

| Servicio | Tecnología | Función |
|---|---|---|
| `web` | Nginx + HTML/CSS/JavaScript | Interfaz y proxy a `/api/` |
| `api` | Python 3.13 + FastAPI | Lista, búsqueda y comparación |
| `db` | PostgreSQL 17 | Persistencia |
| Adaptador | `grocery-cli` | Lectura de catálogos |

La API y PostgreSQL no publican puertos. La web se vincula solo a `127.0.0.1:8081`.

## Probar localmente

Necesitas Docker con Docker Compose y conexión a Internet.

```bash
git clone https://github.com/albertomx2/cesta-clara.git
cd cesta-clara
cp .env.example .env
```

Edita `.env` con una contraseña larga y única. Para evitar problemas al introducirla en la URL de PostgreSQL, usa caracteres alfanuméricos en esta versión.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl http://localhost:8081/api/health
```

Abre [http://localhost:8081](http://localhost:8081). Para diagnosticar: `docker compose logs --tail=50 api`. Cambiar `.env` no cambia la contraseña de una base ya inicializada; no borres un volumen con datos para resolverlo.

## Fuentes y ofertas

Se utiliza [grocery-cli](https://github.com/jgalea/grocery-cli), bajo licencia MIT y fijado al commit `a5c96f8e1f0f5e33d1c6cc4c89bc498d1c56dd1b` en el Dockerfile.

El código contempla Mercadona, Carrefour, Lidl, Aldi, DIA y Alcampo. La disponibilidad depende de cada catálogo y sus protecciones, y puede cambiar. Carrefour usa datos abiertos y DIA dispone de respaldo del conjunto diario de [CholloSúper](https://chollosuper.com/datos), con atribución y licencia CC BY 4.0.

Las bajadas de precio se cotejan por tienda, precio y semejanza del nombre: puede haber errores y no se cubren necesariamente cupones, 2×1, tarjetas ni todas las promociones. Mercadona usa el almacén Barcelona; Aldi, la región Península; algunos datos abiertos se refieren a Granollers. Son valores de los adaptadores, no la ubicación del visitante.

Comprueba siempre producto, formato, stock y precio en la tienda. La caché de búsqueda dura cinco minutos; la de bajadas de precio, una hora. No se ofrece información nutricional verificada.

## Datos y backups

La lista se guarda en `compra_db`. Puedes exportarla así:

```bash
docker compose exec -T db pg_dump -U compra compra | gzip > compra-backup.sql.gz
```

Guarda también configuración y `.env` en un lugar privado fuera del servidor, y comprueba la recuperación. Git excluye backups y secretos. **`docker compose down -v` elimina los datos**; `docker compose down` los conserva.

El arranque crea/adapta tablas y elimina tablas antiguas llamadas `shopping_items`, `prices` y `products`. Usa una base dedicada y haz backup antes de actualizar una versión antigua.

## Seguridad y alcance de la publicación

Esta versión **no tiene login ni listas por usuario**: cualquier visitante puede leer y modificar la misma lista. No la expongas a Internet con datos reales sin añadir control de acceso. HTTPS no sustituye autenticación.

Este repositorio no enlaza una demo privada ni incluye despliegue automático. Las búsquedas consultan servicios externos. No publiques `.env`, dumps, registros con datos personales ni direcciones privadas. Consulta [SECURITY.md](SECURITY.md).

## Desarrollo

```bash
python -m unittest discover -s tests -v
python -m compileall -q backend/app.py
node --check frontend/app.js
docker compose config --quiet
```

Las pruebas cubren funciones de comparación, no la disponibilidad o exactitud de todos los catálogos. Consulta [CONTRIBUTING.md](CONTRIBUTING.md).

Mejoras pendientes: autenticación, listas independientes, ubicación configurable, equivalencia estricta de productos, cobertura de promociones y pruebas de integración.

## Licencia

El código propio todavía no declara una licencia de reutilización. Publicar el repositorio no concede automáticamente una licencia open source. Las licencias del adaptador, datasets e imágenes son independientes.
