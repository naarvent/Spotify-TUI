# Origen del código de `spt_tui/scraper/`

Este paquete es un port reducido, mantenido por este proyecto, de **SpotifyScraper**
(https://github.com/AliAkhtari78/SpotifyScraper, Ali Akhtari, 2025), distribuido bajo licencia MIT.
Se portó a partir de la rama `master` en 2026-11-06: token anónimo desde la página embed
(`auth/anonymous.py`, `api/parse_embed.py`), consultas persistidas (`api/pathfinder.py`) y los parsers de
`track` y `playlist` (`api/parse_entities.py`). No usa `httpx`, ni modelos, ni cliente asíncrono.

Spotify no respalda este código y sus endpoints no están documentados: pueden cambiar sin aviso. Cuando
pase, los síntomas son `PersistedQueryNotFound` (hash rotado → `pathfinder.py`) o `ParsingError`
(cambió el JSON → `parse.py`).

---

MIT License

Copyright (c) 2025 Ali Akhtari

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
