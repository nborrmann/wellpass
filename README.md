# Wellpass web app

A local web app for EGYM Wellpass. You can browse studios on a map, look at a studio's classes, and book or cancel with one click. It can also **auto-book** a full class as soon as a spot opens.

Unofficial. It uses the same APIs as the Wellpass mobile app, with your own account, and is meant for personal use.

## Quick start

```sh
git clone https://github.com/nborrmann/wellpass && cd wellpass
uv run app.py --open
```

The browser opens a short onboarding:
1. Log in with your Wellpass email and password.
2. Choose where you train.

That's all the setup. The login is saved on your computer, so next time `uv run app.py` starts already logged in.

You need [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`). It installs Python and the one dependency (`requests`) automatically.

Options: `--port 8766` if 8765 is taken, `--open` to open the browser.

## Features

- **Map and studio list:** every Wellpass studio in your area, with photos, distance, opening status, and filters for activities and in-app booking.
- **Studio page:** photo gallery, address, the Wellpass terms, and classes for the next 7 days with spots left.
- **Book and cancel:** shows the studio's late-cancel and no-show fees before you confirm.
- **Auto-book:** for full classes, or classes whose booking hasn't opened yet. The server checks every 10 s and books as soon as it can. On macOS you get a notification. It only runs while the server is running.
- **My bookings:** upcoming classes, history and the auto-book list. Refreshes every 10 s.

## What's stored locally (all git-ignored)

| File | Contents |
|---|---|
| `.credentials.json` | email and password from the login screen (chmod 600). Used to log in again if the token expires. |
| `.token.json` | Firebase refresh token (chmod 600) |
| `watches.json` | classes on auto-book |
| `cache/overview.json` | public studio list, refreshed daily |

**Log out** in the header deletes the credentials and the token.

## How it talks to Wellpass

1. **Studios:** `gymfinder.int.api.egym.com`, public, no login. The full list is downloaded once a day.
2. **Login:** Netpulse `POST /np/exerciser/login` returns a session. `GET …/externalAccount/egym/qltLocations/sso` returns a Firebase custom token. `signInWithCustomToken` turns that into an ID token (valid 1 h) and a refresh token.
3. **Classes and bookings:** `mwa-api.int.api.egym.com/mwa/api/class-booking/...` with `Authorization: Bearer <ID token>`. The OpenAPI spec is public at [`/mwa/api/v3/api-docs`](https://mwa-api.int.api.egym.com/mwa/api/v3/api-docs), and a copy is in `mwa-openapi.json`.

The app loads classes for one studio at a time, only when you open that studio. It never fetches classes for a whole area.

## Local API

The page uses these endpoints, and you can call them with `curl` as well. They're served on `127.0.0.1` only.

| Endpoint | |
|---|---|
| `GET /api/status` | `{loggedIn, email, name, studios, autoBooking}` |
| `POST /api/login` `{email, password}` / `POST /api/logout` | |
| `GET /api/studios?lat=&lng=&radius=&inApp=1` | studios near a point |
| `GET /api/studio?slug=` | studio details |
| `GET /api/classes?gymId=&from=&to=` | one studio's classes (ISO UTC times) |
| `GET /api/bookings?from=&to=` / `POST /api/book {classSessionId}` / `POST /api/cancel {bookingId}` | |
| `GET /api/watches` / `POST /api/watch {class}` / `POST /api/unwatch {id}` | auto-book list |
