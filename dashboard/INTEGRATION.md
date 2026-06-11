# Integrating "What's On Orlando" events into another dashboard

This describes everything another repo needs to pull live Central Florida
event data from Supabase and render it (e.g. as a new page/tab on a
touchscreen dashboard).

## Source project

- Repo: `web-projects-live/whats-on-orlando` (branch `claude/florida-events-aggregator-zhru0f`)
- A nightly GitHub Action scrapes ~35 sources (Ticketmaster, theaters, museums,
  comic cons, start.gg tournaments, Pokemon GO events, etc.), normalizes and
  dedupes them, and upserts into a Supabase Postgres database.
- A working reference page already exists at `dashboard/events-today.html` in
  that repo. The full source is included at the bottom of this doc so it can
  be copied standalone.

## Connection details

The data is **publicly readable** via Supabase's REST API (PostgREST), using
the `anon` key. Row Level Security only grants this key `SELECT` on
`events`, `venues`, and `sources` (read-only) — it's safe to embed
client-side in a static HTML/JS dashboard.

```js
const SUPABASE_URL = "https://zskqjwwcyczezouglzmi.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inpza3Fqd3djeWN6ZXpvdWdsem1pIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEyMDMzMDEsImV4cCI6MjA5Njc3OTMwMX0.ofebeGhucWa6G7MpoUIyJhthK2CnK8PPkAPINg7HW7s";

fetch(`${SUPABASE_URL}/rest/v1/events?select=*`, {
  headers: {
    apikey: SUPABASE_ANON_KEY,
    Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
  },
});
```

No server-side proxy or backend is required — this is a direct browser ->
Supabase REST call, and PostgREST sends `Access-Control-Allow-Origin: *`.

## `events` table — relevant columns

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `title` | text | |
| `description` | text | nullable |
| `category` | text | one of the standardized categories below |
| `tags` | text[] | free-form tags, see taxonomy below |
| `venue_name_raw` | text | display name of venue, nullable |
| `address_raw` | text | nullable |
| `city` | text | nullable |
| `latitude` / `longitude` | double | nullable |
| `start_datetime` | timestamptz (UTC) | always set |
| `end_datetime` | timestamptz (UTC) | nullable |
| `is_all_day` | boolean | |
| `timezone` | text | source timezone, usually `America/New_York` |
| `price_min` / `price_max` | numeric | nullable |
| `price_text` | text | human-readable price, nullable |
| `is_free` | boolean | |
| `age_restriction` | text | nullable, e.g. `21+` |
| `ticket_url` / `event_url` | text | nullable |
| `image_url` | text | nullable |
| `status` | text | `active`, `cancelled`, `postponed`, `sold_out` |

`venues` and `sources` tables also exist but the `events` table is
denormalized enough (`venue_name_raw`, `city`, lat/lon) that you usually
don't need to join.

## Category & tag taxonomy

`category` (top-level, always one of):

```
music, theater, comedy, family, festival, arts_culture, sports,
nightlife_edm, gaming, community, food_drink, other
```

`tags` (free-form, array, examples — not exhaustive):

```
edm, rock, metal, punk, reggae, ska, hip_hop, country, jazz, blues,
classical, latin, pop, drag, comedy, renaissance_faire, comic_con, film,
art_exhibit, kids_friendly, free, outdoor, 21_plus, holiday, burlesque,
trivia, karaoke, esports, melee, smash_ultimate, pokemon_go, tournament,
local, indie, ucf
```

## Example queries (PostgREST syntax)

All queries go to `${SUPABASE_URL}/rest/v1/events` with the headers shown
above.

**Today's events (incl. multi-day events already in progress), ordered by
start time:**

```
?select=id,title,start_datetime,end_datetime,is_all_day,venue_name_raw,city,category,tags,price_text,is_free,event_url,ticket_url,image_url,status
&status=neq.cancelled
&or=(and(start_datetime.gte.2026-06-11T00:00:00.000Z,start_datetime.lt.2026-06-12T00:00:00.000Z),and(start_datetime.lt.2026-06-11T00:00:00.000Z,end_datetime.gte.2026-06-11T00:00:00.000Z))
&order=start_datetime.asc
&limit=300
```

Compute the day boundaries client-side from the local clock and convert to
UTC ISO strings (`new Date(y, m, d, 0, 0, 0).toISOString()`).

**Filter by category** (just add): `&category=eq.music`

**Free events only:** `&is_free=eq.true`

**Date range (e.g. "this week"):** swap the two ISO timestamps in the `or=`
clause for your desired window start/end.

**Full-text search:** the table has a generated `search_vector` column
(title/venue/description), queryable via
`&search_vector=fts.<query>` (PostgREST full-text search operator).

## Data freshness

- Scraper runs nightly (~09:00 UTC / 4-5am Eastern). Polling every 5 minutes
  from the dashboard is plenty; the underlying data only changes once a day.
- All stored timestamps are UTC (`timestamptz`); convert to local time for
  display.

## Reference implementation (`dashboard/events-today.html`)

Self-contained HTML/CSS/JS, no build step, no dependencies. Drop this file
into the other dashboard's pages and link/iframe it as a new page. Defaults
to "today, ordered by start time", with touch-friendly category filter chips
(+ a "Free" toggle) and a 5-minute auto-refresh.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>What's On Orlando - Today</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #14161c;
    --card: #1f232c;
    --card-border: #2c313c;
    --text: #f2f3f5;
    --muted: #9aa1ad;
    --accent: #4da6ff;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-user-select: none; user-select: none;
  }
  header {
    padding: 16px 20px 8px;
    display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap; gap: 8px;
  }
  header h1 { margin: 0; font-size: 28px; font-weight: 700; }
  header .clock { font-size: 18px; color: var(--muted); }

  .filters {
    display: flex; flex-wrap: wrap; gap: 8px;
    padding: 4px 20px 16px;
  }
  .chip {
    padding: 12px 18px;
    border-radius: 999px;
    border: 2px solid var(--card-border);
    background: var(--card);
    color: var(--text);
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    min-height: 44px;
  }
  .chip.active {
    border-color: var(--accent);
    background: var(--accent);
    color: #0a0a0a;
  }

  main { padding: 0 20px 24px; }

  #status {
    color: var(--muted);
    font-size: 18px;
    padding: 40px 0;
    text-align: center;
  }

  .event-list { display: flex; flex-direction: column; gap: 10px; }

  .event {
    display: flex; align-items: center; gap: 16px;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-left: 6px solid var(--cat-color, var(--accent));
    border-radius: 12px;
    padding: 12px 16px;
    text-decoration: none;
    color: var(--text);
  }
  .event-time {
    flex: 0 0 90px;
    font-size: 20px;
    font-weight: 700;
    text-align: center;
    line-height: 1.1;
  }
  .event-time small { display: block; font-size: 12px; color: var(--muted); font-weight: 400; }
  .event-thumb {
    flex: 0 0 64px; height: 64px;
    border-radius: 8px; object-fit: cover;
    background: var(--card-border);
  }
  .event-body { flex: 1; min-width: 0; }
  .event-title {
    font-size: 19px; font-weight: 700;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .event-meta {
    margin-top: 4px; font-size: 15px; color: var(--muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .event-tags { margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }
  .tag {
    font-size: 12px; padding: 2px 8px; border-radius: 999px;
    background: var(--card-border); color: var(--muted);
  }
  .event-price {
    flex: 0 0 auto; font-size: 15px; font-weight: 700;
    color: var(--accent); text-align: right; min-width: 60px;
  }

  footer {
    padding: 8px 20px 16px;
    color: var(--muted); font-size: 13px; text-align: center;
  }
</style>
</head>
<body>
  <header>
    <h1>What's On Orlando</h1>
    <div class="clock" id="clock"></div>
  </header>

  <div class="filters" id="filters"></div>

  <main>
    <div id="status">Loading today's events...</div>
    <div class="event-list" id="event-list"></div>
  </main>

  <footer id="footer"></footer>

<script>
  // Public read-only project credentials. Safe to expose client-side:
  // RLS grants anon SELECT-only access to events/venues/sources.
  const SUPABASE_URL = "https://zskqjwwcyczezouglzmi.supabase.co";
  const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inpza3Fqd3djeWN6ZXpvdWdsem1pIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODEyMDMzMDEsImV4cCI6MjA5Njc3OTMwMX0.ofebeGhucWa6G7MpoUIyJhthK2CnK8PPkAPINg7HW7s";

  const REFRESH_MS = 5 * 60 * 1000; // re-poll every 5 minutes

  const CATEGORIES = [
    { key: "all",          label: "All" },
    { key: "music",        label: "Music",        emoji: "🎵", color: "#e74c3c" },
    { key: "theater",      label: "Theater",      emoji: "🎭", color: "#9b59b6" },
    { key: "family",       label: "Family",       emoji: "👨‍👩‍👧", color: "#2ecc71" },
    { key: "festival",     label: "Festival",     emoji: "🎪", color: "#e67e22" },
    { key: "sports",       label: "Sports",       emoji: "🏟️", color: "#1abc9c" },
    { key: "nightlife_edm",label: "Nightlife/EDM",emoji: "🌃", color: "#e91e63" },
    { key: "gaming",       label: "Gaming",       emoji: "🎮", color: "#00bcd4" },
    { key: "comedy",       label: "Comedy",       emoji: "😂", color: "#f39c12" },
    { key: "arts_culture", label: "Arts & Culture",emoji: "🎨", color: "#3498db" },
    { key: "community",    label: "Community",    emoji: "🤝", color: "#95a5a6" },
    { key: "food_drink",   label: "Food & Drink", emoji: "🍽️", color: "#d35400" },
    { key: "other",        label: "Other",        emoji: "📌", color: "#7f8c8d" },
  ];
  const CATEGORY_BY_KEY = Object.fromEntries(CATEGORIES.map(c => [c.key, c]));

  let allEvents = [];
  let activeCategory = "all";
  let freeOnly = false;

  function renderFilters() {
    const el = document.getElementById("filters");
    el.innerHTML = "";
    for (const cat of CATEGORIES) {
      const chip = document.createElement("button");
      chip.className = "chip" + (activeCategory === cat.key ? " active" : "");
      chip.textContent = cat.emoji ? `${cat.emoji} ${cat.label}` : cat.label;
      chip.onclick = () => { activeCategory = cat.key; renderFilters(); renderEvents(); };
      el.appendChild(chip);
    }
    const freeChip = document.createElement("button");
    freeChip.className = "chip" + (freeOnly ? " active" : "");
    freeChip.textContent = "🆓 Free";
    freeChip.onclick = () => { freeOnly = !freeOnly; renderFilters(); renderEvents(); };
    el.appendChild(freeChip);
  }

  function todayRangeISO() {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0);
    const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
    return { start: start.toISOString(), end: end.toISOString() };
  }

  async function fetchTodayEvents() {
    const { start, end } = todayRangeISO();
    const params = new URLSearchParams({
      select: "id,title,start_datetime,end_datetime,is_all_day,venue_name_raw,city,category,tags,price_text,is_free,event_url,ticket_url,image_url,status",
      status: "neq.cancelled",
      or: `(and(start_datetime.gte.${start},start_datetime.lt.${end}),and(start_datetime.lt.${start},end_datetime.gte.${start}))`,
      order: "start_datetime.asc",
      limit: "300",
    });
    const res = await fetch(`${SUPABASE_URL}/rest/v1/events?${params.toString()}`, {
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
      },
    });
    if (!res.ok) throw new Error(`Supabase request failed: ${res.status}`);
    return res.json();
  }

  function formatTime(event) {
    if (event.is_all_day) return { time: "All", sub: "Day" };
    const d = new Date(event.start_datetime);
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const [num, ampm] = time.split(" ");
    return { time: num, sub: ampm || "" };
  }

  function renderEvents() {
    const list = document.getElementById("event-list");
    const status = document.getElementById("status");

    let events = allEvents.filter(e => activeCategory === "all" || e.category === activeCategory);
    if (freeOnly) events = events.filter(e => e.is_free);

    if (events.length === 0) {
      list.innerHTML = "";
      status.style.display = "block";
      status.textContent = "Nothing found for today with this filter.";
      return;
    }
    status.style.display = "none";

    list.innerHTML = events.map(e => {
      const cat = CATEGORY_BY_KEY[e.category] || CATEGORY_BY_KEY.other;
      const { time, sub } = formatTime(e);
      const meta = [e.venue_name_raw, e.city].filter(Boolean).join(" • ");
      const tags = (e.tags || []).slice(0, 3)
        .map(t => `<span class="tag">${t.replace(/_/g, " ")}</span>`).join("");
      const price = e.is_free ? "Free" : (e.price_text || "");
      const thumb = e.image_url
        ? `<img class="event-thumb" src="${e.image_url}" loading="lazy" alt="">`
        : `<div class="event-thumb"></div>`;
      const href = e.event_url || e.ticket_url || "#";

      return `
        <a class="event" style="--cat-color:${cat.color || "#4da6ff"}" href="${href}" target="_blank" rel="noopener">
          <div class="event-time">${time}<small>${sub}</small></div>
          ${thumb}
          <div class="event-body">
            <div class="event-title">${e.title}</div>
            <div class="event-meta">${cat.emoji || ""} ${cat.label}${meta ? " &mdash; " + meta : ""}</div>
            ${tags ? `<div class="event-tags">${tags}</div>` : ""}
          </div>
          <div class="event-price">${price}</div>
        </a>`;
    }).join("");
  }

  async function refresh() {
    try {
      allEvents = await fetchTodayEvents();
      renderEvents();
      document.getElementById("footer").textContent =
        "Last updated " + new Date().toLocaleTimeString();
    } catch (err) {
      document.getElementById("status").style.display = "block";
      document.getElementById("status").textContent = "Couldn't load events: " + err.message;
    }
  }

  function tickClock() {
    const now = new Date();
    document.getElementById("clock").textContent = now.toLocaleString([], {
      weekday: "long", month: "long", day: "numeric",
      hour: "numeric", minute: "2-digit",
    });
  }

  renderFilters();
  tickClock();
  refresh();
  setInterval(tickClock, 30 * 1000);
  setInterval(refresh, REFRESH_MS);
</script>
</body>
</html>
```

## Caveats / known gaps

- `venue_name_raw`, `image_url`, and `tags` can be `null`/empty for some
  sources — handle gracefully (the reference implementation already does).
- Not every category will have events on a given day; an empty filtered
  list is normal.
- Several scraper sources are still being fixed/added on the source repo, so
  event counts/coverage will grow over time without any change needed on the
  dashboard side.
