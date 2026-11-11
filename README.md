# Wellpass web app

A local web app to browse EGYM Wellpass studios on a map, look at a studio's classes, and book them. It can also auto-book full classes as soon as a spot opens.

Unofficial. It uses the same APIs as the Wellpass mobile app, with your own account, and is meant for personal use.

## Run

```sh
printf 'you@example.com\nyour-password\n' > login.txt   # git-ignored
uv run app.py                                           # → http://127.0.0.1:8765
```

## How it talks to Wellpass

- **Studios:** `gymfinder.int.api.egym.com` (public, no login). The studio list is downloaded once a day and cached in `cache/`.
- **Login:** Netpulse (`qualitrain.netpulse.com/np/exerciser/login`) → SSO → Firebase ID token.
- **Classes and bookings:** `mwa-api.int.api.egym.com/mwa/api/class-booking/...` with that token. The OpenAPI spec is public at `/mwa/api/v3/api-docs`, and a copy is in `mwa-openapi.json`.

Classes are loaded for one studio at a time, only when you open it. While the server runs, it checks your bookings and auto-book list every 10 s.

## Files

- `app.py`: local server (login, API proxy, auto-book watcher)
- `index.html`: the page (Leaflet map, studio list, studio detail, bookings)
- `login.py`: minimal standalone login script
