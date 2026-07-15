# spt_tui — Spotify TUI (refactorizado)

El monolito `spt_tui.py` (6689 líneas, una sola clase de ~5600) se dividió en un
paquete por temas. Comportamiento idéntico; solo estructura + limpieza.

## Cómo ejecutar

```bash
pip install -r requirements.txt
python -m spt_tui
```

Credenciales: variables de entorno `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` /
`SPOTIPY_REDIRECT_URI`, o se introducen dentro de la propia app.

## Estructura

```
spt_tui/
├── __main__.py         Entry point (python -m spt_tui)
├── config.py           Paths, logging y credenciales (estado mutable centralizado)
├── constants.py        WELCOME, GLYPHS, LIBRARY_ITEMS
├── widgets.py          ResizableDataTable
├── spotify_client.py   RateLimiter, _SpotifyProxy, SpotifyClient (API de Spotify)
└── app/
    ├── __init__.py     Clase SptPy(App), ensamblada desde los mixins
    ├── core.py         Layout, montaje y enrutado de eventos
    ├── search.py       Búsqueda y render de resultados
    ├── navigation.py   Teclado, foco y navegación de secciones
    ├── tables.py       Construcción/render de DataTables y columnas liked/saved
    ├── library.py      Playlists y biblioteca guardada
    ├── lyrics_view.py  Letras sincronizadas (fetch/parse/render)
    ├── playback.py     Reproducción y barra "now playing"
    └── queue_devices.py Cola y selección de dispositivos
```

`SptPy` se compone por herencia múltiple de mixins; en tiempo de ejecución sigue
siendo una única clase, así que el reparto entre archivos no cambia la lógica.

## Auditoría — correcciones aplicadas

- **Bug: Enter no abría media biblioteca.** El despacho de items (Liked/Saved
  Artists/Albums/Podcasts/Episodes…) estaba duplicado y desincronizado entre
  teclado (`action_open`) y ratón (`on_list_view_selected`). Unificado en
  `LibraryMixin._open_library_item()` — un único mapeo para ambos.
- **Reproducir ya no congela la UI** (`_play_row`): la red va a un hilo, usa el
  shuffle cacheado (sin `get_playback()` de más) y se unificaron dos closures
  de resolución de índice duplicadas.
- **Now-bar sin hitch periódico:** `_sync_playback` (1.5s) hacía red en el hilo
  principal → movido a hilo. El tick de 0.5s (`_update_now_bar`) ahora pinta
  **solo desde caché** (antes hacía `get_playback()` cada tick → tráfico
  redundante).
- **Red en hilo, no en la UI:** vista de dispositivos (`_refresh_devices_table`)
  y `transfer()` al elegir dispositivo.
- **Menos llamadas a Spotify:** `_active_device_id()` cachea el dispositivo
  activo 5s (invalidado en `transfer`), evitando un `devices()` por cada
  volumen/seek.
- **Robustez:** `_fetch_synced_lyrics` ya no actualiza un widget desde un hilo
  (usa `call_from_thread`); `SpotifyClient.ensure()` es thread-safe (lock).
- **Limpieza:** eliminado `_focus_search_and_highlight` (muerto); deduplicadas
  las 4 ramas idénticas de seek-settings (helper `_save_seek_setting`); markup
  `</b]` corregido en la ayuda.

- **Métodos gigantes partidos:**
  - `action_toggle_favorite` 322 → 80 líneas: dispatcher fino + helpers
    (`_toggle_track_favorite`, `_toggle_saved_item` para álbum/podcast/episodio,
    `_toggle_artist_favorite`, `_call_first`, `_revalidate_saved_if_search`). De
    paso, los checks `contains`/`followed` ya no bloquean el hilo de UI.
  - `_do_search` 353 → 109 líneas: el render de resultados se extrajo a
    `_render_search_results()`.

> `on_key` (547 líneas) se deja intacto **a propósito**: es un router de teclado
> con lógica de fall-through entrelazada (handlers de →/← solapados, dependencia
> de que Textual consuma la tecla primero). Partirlo sin poder ejercitar cada
> combinación en la app real arriesga romper el teclado en silencio. No es un bug.

## Funcionalidades añadidas

- **Canción en reproducción resaltada** en morado (`#b388ff`) en la tabla de
  pistas; el resaltado se mueve solo al cambiar de canción.
- **Caché de playlists en disco** (`playlists_cache.json`): al arrancar se pinta
  la lista al instante y la red la refresca en segundo plano.
- **Contador de carga** en playlists grandes: se muestra la primera página al
  momento y el título indica `(loading 200/850)` mientras baja el resto.
- **Arte ASCII del título y del artista** en la vista de letras (`pyfiglet`),
  con degradado a texto plano en terminales pequeñas.

## Qué "basura" se quitó

- **Bloque de constantes duplicado** (`USER_HOME`/`CACHE_DIR`/… definidos dos veces).
- **~115 líneas de código muerto de letras**: helpers a nivel de módulo
  (`_lrclib_find`, `_lyrics_ovh_get`, `_parse_lrc_to_timeline`, `_LYR_CACHE`,
  `_save_cache`, `_http_json/_http_text`, `_safe_ms`, `_distribute_unsynced`…)
  que ningún método usaba — la app reimplementa las letras en
  `lyrics_view._fetch_synced_lyrics`.
- **`try/except` muerto** para redefinir `CACHE_DIR` (siempre estaba definido).
- Líneas-comentario de solo espacios entre métodos.
- **Estado global disperso** (`CLIENT_ID`, `CONFIG_LOADED`, `_LOCAL_CFG` mutados con
  `global`/`globals()`) centralizado en `config` → una única fuente de verdad
  (antes, al separar en módulos, se habría desincronizado y roto el login).
- Setup de logging **reordenado antes** de cargar la config (el original usaba
  `logger` en un `except` antes de que existiera).

## Mejoras de rendimiento

- **Letras sin congelar la UI**: al abrir letras (`l`) ahora aparece
  `Lyrics loading…` al instante y el fetch (red) corre en un hilo de fondo.
  El tick de resaltado usa el estado de reproducción ya cacheado por la barra
  "now playing" en vez de llamar a la red cada 0.4s (antes bloqueaba el hilo
  de UI en cada tick). Los resultados obsoletos se descartan con un token de
  generación al cambiar de canción o cerrar la vista.
- **Playlists grandes mucho más rápidas** (`_open_playlist_table`):
  - Se piden solo los campos usados (`fields=`) → respuestas mucho menores.
  - Las páginas (>100 canciones) se descargan **en paralelo** usando el `total`.
  - El check de "liked" se hace en **lotes concurrentes**.
  - Se eliminó el `_revalidate_liked_column` redundante (el worker ya calcula
    los likes) y un `_load_playlists` parásito que recargaba toda la lista al
    abrir cada playlist.
  - **Render en dos fases**: la tabla de canciones se muestra en cuanto se bajan
    las filas; el check de "liked" (la parte lenta) se hace después y rellena los
    corazones, así no esperas a que termine para ver la playlist.

## Vista de letras con arte ASCII

- La cabecera muestra el **artista** en arte ASCII (pyfiglet), centrado y
  limitado al ancho del panel; debajo, el nombre de la canción en gris.
- **Fallback automático**: si la terminal es pequeña (ancho < 72 o alto < 26),
  si el nombre del artista es muy largo, o si `pyfiglet` no está instalado, se
  usa la cabecera de una línea de antes. Requiere `pip install pyfiglet` para
  ver el arte.
- El bloque de arte se paga a ancho completo por línea para que el centrado del
  panel no lo distorsione.

## Navegación con flechas

- **←** dentro de una vista abierta → mueve el foco al panel izquierdo **sin
  cerrar** la vista, para navegar Library/Playlists.
- **→** en **Library/Playlists** → vuelve el foco a la vista abierta a la derecha
  (tabla de canciones, álbum, cola, dispositivos…) para navegar sus filas.
- **→** dentro de una vista (LVL_VIEW) → **no hace nada**: nunca te saca de ahí.
- **→** en **Search** → siempre al recuadro **Help**; **Enter** abre la ayuda y
  **←** vuelve a Search.

## Fix: corazones (likes) que no aparecían

El check de "liked" se había paralelizado (varios hilos), pero spotipy comparte
una sola `requests.Session` y un solo auth manager, que **no son thread-safe**:
las llamadas concurrentes se corrompían y devolvían **todo False** (una canción
ya likeada salía sin corazón hasta re-likearla con `F`). Se volvió al check
**secuencial** original (que sí funcionaba), manteniendo `fields=` (el ahorro de
tiempo real) y la alineación **por id de pista**. Las páginas también se piden de
forma secuencial por el mismo motivo.

## Fix: playlists que "a veces no cargaban"

Causa raíz: los cargadores exigían un token **actualmente válido**
(`has_valid_user_token`, que solo mira `expires_at > ahora`). Como el token de
Spotify caduca a la hora, si abrías la app pasado ese rato el cargador pintaba
vacío y **no dejaba que spotipy refrescara el token**; el retry worker además
hacía `break` permanente. De ahí el truco de "pulsa Ctrl+R".

Arreglos:
- `_load_playlists` y los disparadores ahora exigen solo un token **cacheado**
  (`has_cached_token`); uno caducado pero refrescable se renueva solo en la
  llamada a la API.
- El retry worker ya no se rinde con un token caducado: reintenta hasta cargar.
- Tras autenticar (`finish_authorization`) ahora **sí** se dispara la carga de
  playlists (antes no ocurría en el primer login).
- Volver al menú (Escape) o abrir la ayuda reintentan la carga → retry
  implícito, con lo que Ctrl+R deja de hacer falta en la práctica.

## Nota pendiente

`app/navigation.py` (`on_key`) tiene un `return` dentro de un bloque `finally`
(heredado del original) que silencia excepciones. Se dejó tal cual para no
cambiar comportamiento; conviene revisarlo aparte.
