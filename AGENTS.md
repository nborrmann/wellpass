# Setting up this app for a user (agent guide)

Goal: get the user from zero to logged in and browsing in under a minute, **without the user's password ever passing through you**.

## Steps

1. **Check for uv:** run `uv --version`. If it's missing, install it with `brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh` (ask first if the user hasn't agreed to installs).
2. **Start the server** in the background, from the repo root:
   ```sh
   uv run app.py --open
   ```
   - Wait for the line `ready: http://127.0.0.1:8765`. The first start downloads the studio list (~20 MB), which takes a few seconds.
   - If you see `port 8765 is busy`, the app may already be running. Check with `curl -s localhost:8765/api/status`. Otherwise start it with `--port 8766`.
3. **Hand over to the user:** tell them to open http://127.0.0.1:8765 (`--open` already tried to open it) and log in with their **Wellpass email and password** in the browser.
   - **Do not ask for the password in chat.** Don't write `.credentials.json` yourself, and don't call `/api/login` with credentials the user typed into the chat.
   - If the user pastes their password into the chat anyway, tell them the browser login screen is the safer place.
4. **Confirm the login:** run `curl -s localhost:8765/api/status`. `"loggedIn": true` means onboarding is done. You can poll this every few seconds while the user logs in.
5. **Next:** the user picks an area in step 2 of the onboarding, then browses studios on the map. Clicking a studio shows its classes, with Book and Auto-book buttons.

## Good to know

- **Restarting:** `uv run app.py` reuses the saved login. A login is only needed after **Log out**, or after the Wellpass password changes. In that case the page shows the login screen again.
- **Secrets:** `.credentials.json` and `.token.json` are chmod 600 and git-ignored. Never print them, commit them or upload them.
- **Auto-book only runs while the server runs.** If the user relies on it, keep the server running, for example in a terminal tab, tmux, or a launchd agent if they ask for one.
- **Be gentle with Wellpass:** don't script bulk requests against `/api/classes` for many studios. The app deliberately fetches one studio at a time.
- **Login suddenly fails for everyone:** Wellpass may have changed the expected app version. Update `NP_HEADERS` / `APP_UA` in `app.py` to a current app build.
- **Platform:** notifications use `osascript` (macOS). On other systems everything else still works, just without notifications.
