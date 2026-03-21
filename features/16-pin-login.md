# 4-Digit PIN Login

## Overview

Add a 4-digit PIN (password) to all player accounts. Existing users are prompted to set a PIN on their next username-based login; new users create a PIN during registration. Token-based auto-login (`pb_token` in localStorage) bypasses PIN entry since the token already represents an authenticated session. PINs stored as plain text per Tyler's request.

## Architecture — Three Login Paths

1. **Token auto-login** (`tryAutoLogin`): Token already authenticated. No PIN prompt. Unchanged.
2. **Existing user, username login**: Server checks PIN status.
   - PIN is NULL → `needs_pin_setup` → user sets 4-digit PIN → `set_pin` → `login_ok`
   - PIN is set → `needs_pin` → user enters PIN → `verify_pin` → `login_ok` or `pin_error`
3. **New user** (username not found): `login_error` → frontend shows PIN setup → user sets PIN → `register` with PIN → `register_ok`

## Database Changes

Migration in `_migrate()`:
```python
if "pin" not in cols:
    conn.execute("ALTER TABLE players ADD COLUMN pin TEXT DEFAULT NULL")
```

NULL means PIN not yet set (legacy accounts).

## Backend Changes

### `player_accounts.py`

1. **`_migrate()`** — Add `pin` column
2. **`register()`** — Accept `pin=None` parameter, include in INSERT
3. **`_row_to_dict()`** — Add `"pin": d.get("pin")` (server-side only) and `"has_pin": d.get("pin") is not None`
4. **New `set_pin(self, player_id, pin)`** — UPDATE players SET pin = ? WHERE id = ?

### `server.py`

1. **Modify `login` handler** — For username login: check PIN status, send `needs_pin` or `needs_pin_setup`. Store `player._pending_pin_account` for verification. Token login unchanged.

2. **New `verify_pin` handler** — Compare submitted PIN against `_pending_pin_account["pin"]`. On match: complete login. On mismatch: send `pin_error`.

3. **New `set_pin` handler** — Validate 4 digits (`re.match(r'^\d{4}$', pin)`). Save via `account_mgr.set_pin()`. Complete login.

4. **Modify `register` handler** — Require `pin` field, validate 4 digits, pass to `account_mgr.register(username, pin=pin)`. Strip `pin` from `register_ok` response.

5. **Ensure PIN never leaks** — `get_profile()` doesn't include `pin` (confirmed). Strip `pin` from `register_ok` response dict.

## Frontend Changes

### HTML — PIN entry UI (inside `screen-login`)

```html
<!-- PIN Setup (new users + existing without PIN) -->
<div id="pin-setup" class="login-form" style="display:none">
  <div class="pin-title">Set Your 4-Digit PIN</div>
  <div class="pin-subtitle" id="pin-setup-subtitle">Choose a PIN to protect your account</div>
  <div class="pin-input-row">
    <input id="pin-digit-1" type="tel" maxlength="1" inputmode="numeric" class="pin-digit">
    <!-- ... 4 digits total ... -->
  </div>
  <button class="btn btn-primary" onclick="submitPinSetup()">SET PIN</button>
  <div id="pin-setup-error" class="error-text"></div>
</div>

<!-- PIN Verify (existing users with PIN) -->
<div id="pin-verify" class="login-form" style="display:none">
  <!-- Same structure, onclick="submitPinVerify()" -->
</div>
```

Uses `type="tel"` with `inputmode="numeric"` for mobile numeric keyboard.

### CSS

```css
.pin-digit {
  width: 56px; height: 64px; font-size: var(--fs-3xl);
  text-align: center; background: var(--bg3);
  border: 2px solid var(--border); border-radius: 8px;
  -webkit-text-security: disc;  /* Hide digits */
}
```

### JavaScript

**New state:** `S._pinMode`, `S._pinUsername`, `S._pinIsNewUser`

**New message handlers:**
- `needs_pin_setup` → show PIN setup UI
- `needs_pin` → show PIN verify UI
- `pin_error` → show error, clear inputs

**Modified `login_error`:** Instead of auto-registering, show PIN setup first, then register with PIN.

**New functions:** `showPinSetup()`, `showPinVerify()`, `submitPinSetup()`, `submitPinVerify()`, `clearPinInputs()`, `getPinValue()`

**PIN digit auto-advance:** Each digit input auto-focuses the next on input, backspace goes to previous. Enter submits.

**Login/logout reset:** Reset PIN UI state on `login_ok`, `register_ok`, and `logout()`.

## WebSocket Message Reference

| Client Sends | Server Responds | Description |
|---|---|---|
| `{type: "login", username}` | `needs_pin` | Existing user with PIN |
| `{type: "login", username}` | `needs_pin_setup` | Existing user, no PIN yet |
| `{type: "login", username}` | `login_error` | User not found |
| `{type: "login", token}` | `login_ok` | Token auto-login, bypasses PIN |
| `{type: "verify_pin", pin}` | `login_ok` / `pin_error` | Verify existing PIN |
| `{type: "set_pin", pin}` | `login_ok` / `pin_error` | Set PIN (first time) |
| `{type: "register", username, pin}` | `register_ok` / `register_error` | New user with PIN |

## Edge Cases

- **Disconnect during PIN flow:** `_pending_pin_account` destroyed with Player object. Reconnect starts fresh.
- **Token login for user without PIN:** Skips PIN entirely. Prompted on next username login.
- **`get_profile()` never exposes PIN:** Confirmed safe.
- **No rate limiting:** Home game, not public. Could add later if needed.

## Implementation Steps

1. `player_accounts.py` — Add `pin` column migration, `pin` param to `register()`, `set_pin()` method, update `_row_to_dict()`
2. `server.py` — Modify `login` handler, add `verify_pin`/`set_pin` handlers, modify `register` handler, strip PIN from responses
3. `index.html` — Add PIN HTML, CSS, JavaScript (setup/verify UI, auto-advance, message handlers)
4. Test: new user registration, existing user PIN setup, PIN verify, token bypass, wrong PIN, logout + re-login

## Critical Files

- `player_accounts.py` — DB migration, `register()`, `set_pin()`, `_row_to_dict()`
- `server.py` — Login flow restructuring, new handlers
- `index.html` — PIN UI (HTML + CSS + JS)
