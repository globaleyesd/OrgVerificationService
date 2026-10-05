# UI guide: pages, sign-in, source sidebar and header controls

The web pages are plain files in `app/web` with no third-party requests, and no inline scripts or styles (the security policy forbids them). Colours and the name come from `branding` in the config (see [CONFIGURATION.md](CONFIGURATION.md)). Every command for running and deploying is in [COMMANDS.md](COMMANDS.md).

## The pages: each has its own address
| Address | Page | Sign-in and role check? |
|---|---|---|
| `/` | Small launcher with two links | No |
| `/ask` | **Ask**: chat with highlighted sources | **Yes** |
| `/add` | **Add knowledge**: one big box to type, paste or drop files | **Yes: any signed-in user** |
| `/review` | **Review documents**: choose who can read each document | **Yes, top role only** |
| `/signin` | **Sign in**: pick a demo account (or username and password outside demo mode); also how you **switch user** mid-demo | No (it is the sign-in) |

There is no switch between them in the page: people open the address they need. The header has the title (a link to `/`), the signed-in user (for reference) and **Costs** (top role only). Switching user and signing out happen only on the sign-in screen, `/signin`. There is no on/off button: on AWS the Control Center turns projects on and off; locally `python -m app.cli service on|off` does.

## The two demo accounts
Two accounts complete the demo scenarios. They are created with one command (see [COMMANDS.md](COMMANDS.md)) and stored in the protected credentials store (a private S3 bucket on AWS; the `creds/` folder locally). **In demo mode the sign-in page has no text boxes: you just click the user you want to be.**

| User | Role | What they see |
|---|---|---|
| **Eileen** | Super | Everything: all sources, level tags, the diagram, the disagreement scene, the **Costs** button |
| **AllMinusEileen** | Employee | One shared account standing for everyone except Eileen: Employee-level sources only, no level tags, no diagram, and a "some sources were not available" notice |
| **Mark** | Super | Exactly the same as Eileen: a second Super account |
| **AllMinusMark** | Employee | Exactly the same as AllMinusEileen: a second Employee account |

Click one, then **Sign out** (header) and click the other, to run the same questions both ways. The accounts still get random passwords when created (shown once, stored only as salted hashes). Those are used by the password form, which replaces the picker when `ui.demo_mode` is false.

### Ask (`/ask`)
- Asks you to **sign in** first: in demo mode a picker with one button per demo account, otherwise a username and password form. The card also links to the full sign-in screen. A signed-in user whose role is not allowed sees "You don't have access to this page".
- Chat with a question bar at the bottom (padded on left, right and bottom) and an optional microphone button.
- Answers have clickable `[1]` markers and chips that open the source sidebar.
- Buttons under answers (when relevant): **Show all N matching passages**, **Show as diagram**, **Add a correction** and **Add knowledge about this** (these two open `/add` with the box pre-filled; the text travels in the browser tab's session storage, not in the address), **Copy answer with sources**.

### Sign-in screen (`/signin`)
- **Every page needs sign-in.** Opened without one (or when a session expires), a page goes straight to `/signin` and returns afterwards; the pages have no sign-in prompt of their own.
- To switch user or sign out, open **`/signin`** (the only place for either). It which shows who is signed in and one card per demo account, grouped by role (Super first). Click a card to become that account; you go straight back to the page you came from.
- The demo accounts come from the server (`DEMO_USERS` in `app/auth.py`), so adding one there adds its card everywhere. Outside demo mode the screen shows a username and password form instead.
- Create any missing demo accounts with `python -m app.cli create-demo-users` (existing ones are left alone).

### The source sidebar (Ask)
- **Collapsed** (the normal state): a slim, full-height **vertical sidebar** on the right edge with the word "Sources" running down it. Click it to open the panel.
- **Open:** 20% of the page width by default (at least 240 px) and **resizable** (drag its left edge, use the arrow keys on the edge, or double-click to reset). Shows the passage with the matching text highlighted, its location and level, and "Quote matched in the source document" when the server verified it. Opened from the sidebar before any source was picked, it says to select a source marker.
- **All matching passages:** every passage the search found for the question, from every document the user may read, best match first, with the question's words highlighted. Cited passages are marked "cited [n]" (click to open that source). A single source has an "All N matching passages" button to get back to the list.
- **Every occurrence:** when a cited quote appears in several places (other pages, other documents), each place gets its own source chip under the answer, the open source lists all of them ("This quote appears in N places"), and **Copy answer with sources** includes them all.
- **Colours:** each citation has its own colour, continuing across answers so `[1]` in one answer never matches `[1]` in another. The `[n]` buttons, the source chips and the highlights share that colour. The first place a quote appears is the strongest shade and later places get lighter. Words from the question get a neutral grey highlight. Nothing is underlined. A legend at the top of the panel explains this.
- **Collapse ›** (or Esc) slides it back to the sidebar and the chat gets the full width. On phones the open panel is a bottom sheet.

### Add knowledge (`/add`)
- One large box: type or paste, or drop files anywhere inside it, or use "choose files".
- A queue shows each file's status. Types and sizes are checked in the browser for convenience and again on the server.
- Needs a signed-in user (any role) but no access-level picker: every new upload starts at the top clearance level and a reviewer decides who can read it.

### Review (`/review`)
- Top role only (Eileen). A table of every document with a drop-down: **Employee and above** or **Super only**. Changing it saves at once ("saved ✓") and moves the document and all its passages together.
- New uploads start at the top level, so this is how a document becomes readable by Employees.

## Who can use what
A user's **role is their clearance level** (`super` or `employee` by default). Clearance decides **which material** they can read. The rules below decide **which pages and actions** they can use.

| Thing | Rule |
|---|---|
| `/ask` page and `POST /api/ask` | Needs sign-in and a role listed in `ui.ask_roles`. Nobody signed in has no role, so no access |
| `/add` page and `POST /api/upload` | Needs sign-in (any role). No role check beyond that |
| Every page | Needs sign-in: a page opened without one, or any request answered `401`, goes to `/signin` and comes back afterwards. There is no sign-in prompt on the pages themselves |
| Costs | Top clearance level only (Eileen) |
| `/review` page and `/api/documents*` | Needs sign-in and the top role |
| Everything under `/api` | Needs the service switch to be on |

The checks live on the **server**. The page only reflects what the server says.

**Think about `/add` being open.** Anyone who finds the address can submit text or files. What limits the risk: submissions can't be read by lower roles until a reviewer relabels them, file types and sizes are limited, and the service switch can close everything. What is not built: rate limiting, spam or malware screening, and per-user limits.

### How sign-in works
- **Demo mode** (`ui.demo_mode: true`): the page shows the two demo users as buttons. Clicking one calls `POST /api/auth/demo-login`, which signs you in **without a password**. The server accepts this only while demo mode is on and **only for the two demo accounts** (any other account is refused even if it exists).
- **Outside demo mode:** the picker disappears, `demo-login` answers `403`, and the page shows a username and password form that calls `POST /api/auth/login` and checks the password against the stored hash.
- Either way the result is a **session cookie**: `HttpOnly` (scripts can't read it), `Secure` (HTTPS only), `SameSite=Strict`, limited to `/api`.
- The cookie holds a signed token (user, role, expiry) valid for `auth.session_minutes`. The signing key is stored with the credentials, never in the code or the config.
- Tokens can't be revoked early. A role change takes effect at the next sign-in. Too many wrong passwords lock that **username** for `auth.lockout_minutes`.
- Safari does not accept `Secure` cookies on plain `http://localhost`; use Chrome or Firefox for local runs.

**Security consequence of the picker:** while demo mode is on, anyone who can reach the site **while the service switch is on** can become Eileen (Super) with one click and read everything. That is acceptable for a controlled demo and nothing else. Keep the service switch off when you aren't demoing, and set `ui.demo_mode: false` before real use.

## Service offline screen
While the service switch is off (or the server is stopped) every page shows **Service offline** and says where to turn it on (the Control Center on AWS, `python -m app.cli service on` locally). On AWS, while a server is starting, the page shows "starting, about a minute" and loads by itself; at zero it says the project must be brought back from the Control Center. If the page can't reach anything at all (for example a local run with the server down) it just says the server can't be reached. Any request that comes back "offline" while you are using a page sends you here too. Sign-in is also refused while the service is off.

## Trying the pages with no backend
Open a page as a plain file with `?mock=1` on the end (for example `app/web/ask.html?mock=1`), or run the server with `ui.allow_mock: true` and use `http://localhost:8000/ask?mock=1`. Answers and cost numbers are fictional and nothing is sent anywhere. On the sign-in card or `/signin`, click any demo account; the choice is remembered for that browser tab so every page agrees. `&state=off` starts on the offline screen.

## Backend contract (what the pages expect)
All data routes are under `/api`.

| Route | Purpose |
|---|---|
| `GET /api/service/status` | `{on, auto_off_minutes, allow_mock, branding: {app_name, theme}}`. Always reachable |
| `GET /control/status` | **AWS only.** The status function: reports the server state. Always reachable, even when the server is stopped or at zero. It can't turn anything on or off: only the Control Center can |
| `POST /api/service/on` / `off` | `{password}`. `401` wrong password, `429` locked out, `503` credential store unavailable. Always reachable |
| `POST /api/auth/login` | `{username, password}` gives `{user}` and sets the cookie. `401` wrong username or password, `429` locked, `503` store unavailable |
| `POST /api/auth/demo-login` | `{username}` gives `{user}` and sets the cookie, **no password**. Only while `ui.demo_mode` is true and only for the two demo accounts: otherwise `403`. `404` if the demo accounts haven't been created |
| `POST /api/auth/logout` | Clears the cookie |
| `GET /api/auth/me` | `{user}` (or `{user: null}`) |
| `GET /api/settings/public` | Non-secret page settings: `branding`, allowed file types, max size, speech mode, `user`, `can_view_costs`, `ui: {demo_mode, demo_login, can_ask, allow_mock}` |
| `POST /api/upload` | Form data, one item per request: `file` or `text`. Needs sign-in (`401` otherwise); any role. No level field: the server assigns `clearance.default_upload_level`. Returns `{id, title, chunks}` (never the level). Refusals: `400` nothing sent, `409` document limit, `411` no length, `413` too big, `415` type not allowed, `422` unreadable or empty |
| `GET /api/documents` | Top role: `{levels, documents: [{id, title, file_type, level, chunks, size_bytes, created_at}]}` |
| `POST /api/documents/level` | Top role: `{id, level}` marks who can read a document. `400` unknown level, `404` no such document |
| `POST /api/ask` | Needs sign-in and an allowed role (`401` / `403`). `{question, history}`, answered as below. `429` daily AI limit, `502` AI service down, `503` anything else |
| `POST /api/costs/estimate` | Top role only. See [SERVICE_SWITCH_AND_COSTS.md](SERVICE_SWITCH_AND_COSTS.md) |

While the service is off, everything except the status, switch and health routes returns `503 {"detail": "Service offline", "offline": true}`.

### `POST /api/ask`
Request: `{"question": "...", "history": [{"role": "user"|"assistant", "text": "..."}]}`. `history` holds the last few turns so follow-ups like "and when does that expire?" work.

Response:
```json
{
  "answer": "The license expires on 30 June 2027 [1].",
  "citations": [
    {"id": 1, "document_title": "Vendor agreement.pdf", "location_label": "Page 6",
     "text": "full passage text ...", "highlight": {"start": 120, "end": 132},
     "quote": "30 June 2027", "highlights": [{"start": 120, "end": 132}, {"start": 410, "end": 422}],
     "terms": [{"start": 4, "end": 11}], "verified": true, "level": "employee"}
  ],
  "withheld": false,
  "matches": [
    {"document_title": "Vendor agreement.pdf", "document_id": 4, "location_label": "Page 6",
     "text": "full passage text ...", "highlights": [{"start": 4, "end": 11}],
     "quote_spans": [{"start": 120, "end": 132, "cite": 1}], "cites": [1], "cited": 1, "level": "employee"}
  ],
  "actions": ["diagram", "correct", "add_knowledge"],
  "diagram": {"nodes": [{"label": "Web front end", "cite": 1}]}
}
```
- `[1]` markers become clickable buttons that open the matching citation.
- `highlight` is character positions inside `text`. The server must verify the quote really appears there before sending, and sets `verified: true` only then.
- `level` is optional: send it only to users allowed to see level tags (Super in the sample design).
- `matches` lists every passage the search returned (up to `retrieval.top_k`), from every document, including when the answer is "couldn't find" or "couldn't verify". They come from the same clearance-filtered search, so nothing above the asker's level is ever included. `highlights` are the question's words (other forms too: "discovered" also marks "discover"); `quote_spans` marks every occurrence of every verified quote in that passage, so a quote that appears in several places or documents is marked in all of them; `cites` lists those citation numbers (`cited` is the first, or `null`).
- In a citation, `highlights` marks every occurrence of the verified quote in the passage (`highlight` is the one that was checked) and `terms` marks the question's words. The page shows quotes bold and question words light.
- `actions` are hints: `diagram` (needs `diagram`), `correct` ("Add a correction") and `add_knowledge` ("Add knowledge about this"). Real answers only ever send `add_knowledge`; `diagram` and `correct` appear in sample mode.
- Real answers: every citation's quote is checked against the passage it names, and dropped if it isn't there. Citation numbers follow the order they appear in the answer. If nothing can be verified the answer says so instead of guessing.
- `diagram.nodes` are drawn top to bottom; each node's `cite` opens that source.
- `withheld: true` shows "Some sources were not available at your access level." It is a flag only and never says what was withheld.

## Sample script (sample mode)
Five clickable questions on `/ask?mock=1`, all fictional. Run them as Eileen, then as AllMinusEileen:

| Scene | Question | Eileen (Super) | AllMinusEileen (Employee) |
|---|---|---|---|
| 1. Simple lookup | "When does the reporting platform license expire?" | Two sources, level tags, "quote matched" | One source and the "not available" notice |
| 2. Voice + merged answer | Tap the mic (simulated) | Four sources and "Show as diagram" | Two sources, no diagram, notice |
| 3. Sources disagree | "Who owns the renewal process?" | Both answers with dates and "Add a correction" | Only the newer source, notice |
| 4. Nothing found | "What's the password policy for the billing system?" | No guessing, "Add knowledge about this" | Same |
| 5. Follow-up | "And when does that expire?" (after scene 2) | Cites a Super-only source | "Couldn't find" |

## Safety notes for contributors
- Text from documents and the server is added with `textContent` or text nodes, never `innerHTML`. The fixed page chrome in `common.js` is static text written in the file.
- The pages make no requests to third-party sites and load no external scripts or fonts.
- Colours from the server are checked as `#RRGGBB` again in the browser before use.
- No inline `<script>`, `<style>` or `style=""`: the policy in `app/pages.py` (and the same text in the AWS template) blocks them. A test keeps the two copies identical.
- Passwords are only sent in a request body, never in an address, and the fields are cleared afterwards.
- Every element id must be unique across the shared chrome plus the page (a test checks this): two elements sharing an id silently break whichever script looks it up second.
