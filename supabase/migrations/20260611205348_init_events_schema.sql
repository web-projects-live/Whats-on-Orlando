-- Extensions
create extension if not exists pgcrypto;
create extension if not exists pg_trgm;
create extension if not exists cube;
create extension if not exists earthdistance;

-- ============================================================
-- sources: registry of every scraper / data source
-- ============================================================
create table sources (
  id uuid primary key default gen_random_uuid(),
  slug text unique not null,
  name text not null,
  source_type text not null check (source_type in ('api','html','ics','social','manual')),
  base_url text,
  homepage_url text,
  default_category text,
  is_active boolean not null default true,
  scrape_config jsonb not null default '{}'::jsonb,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table sources is 'Registry of every data source/scraper feeding the events table.';

-- ============================================================
-- venues: normalized venue/location records
-- ============================================================
create table venues (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique,
  address text,
  city text,
  state text not null default 'FL',
  postal_code text,
  county text,
  latitude double precision,
  longitude double precision,
  venue_type text, -- arena, theater, bar_club, museum, park, convention_center, festival_grounds, other
  website text,
  phone text,
  social_links jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index venues_geo_idx on venues using gist (ll_to_earth(latitude, longitude));
create index venues_city_idx on venues (city);
create index venues_name_trgm_idx on venues using gin (name gin_trgm_ops);

-- ============================================================
-- events: canonical, deduplicated event records
-- ============================================================
create table events (
  id uuid primary key default gen_random_uuid(),
  dedup_hash text unique not null,

  title text not null,
  description text,

  -- standardized top-level category (music, theater, comedy, family, festival,
  -- arts_culture, sports, nightlife_edm, community, food_drink, other)
  category text not null default 'other',
  -- finer-grained free-form tags (edm, rave, rock, reggae, ska, renaissance,
  -- comic_con, free, kids_friendly, outdoor, 21_plus, etc.)
  tags text[] not null default '{}',

  venue_id uuid references venues(id) on delete set null,
  venue_name_raw text,
  address_raw text,
  city text,
  latitude double precision,
  longitude double precision,

  start_datetime timestamptz not null,
  end_datetime timestamptz,
  is_all_day boolean not null default false,
  timezone text not null default 'America/New_York',

  price_min numeric,
  price_max numeric,
  price_text text,
  is_free boolean not null default false,
  age_restriction text,

  ticket_url text,
  event_url text,
  image_url text,

  status text not null default 'active' check (status in ('active','cancelled','postponed','sold_out')),

  search_vector tsvector generated always as (
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(venue_name_raw, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'C')
  ) stored,

  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index events_start_idx on events (start_datetime);
create index events_category_idx on events (category);
create index events_city_idx on events (city);
create index events_tags_idx on events using gin (tags);
create index events_search_idx on events using gin (search_vector);
create index events_geo_idx on events using gist (ll_to_earth(latitude, longitude));
create index events_venue_idx on events (venue_id);

comment on table events is 'Canonical, deduplicated live-event records for the Central Florida corridor.';
comment on column events.dedup_hash is 'Stable hash of normalized title+venue+date used for upsert-based deduplication across sources.';

-- ============================================================
-- event_sources: links a canonical event back to every raw
-- source record that contributed to it (for audit + re-scrape)
-- ============================================================
create table event_sources (
  id uuid primary key default gen_random_uuid(),
  event_id uuid not null references events(id) on delete cascade,
  source_id uuid not null references sources(id) on delete cascade,
  source_event_id text,
  source_url text,
  raw_data jsonb,
  scraped_at timestamptz not null default now(),
  unique (source_id, source_event_id)
);

create index event_sources_event_idx on event_sources (event_id);

-- ============================================================
-- scrape_runs: log of each scraper execution
-- ============================================================
create table scrape_runs (
  id uuid primary key default gen_random_uuid(),
  source_id uuid references sources(id) on delete set null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text check (status in ('success','failed','partial')),
  events_found integer not null default 0,
  events_new integer not null default 0,
  events_updated integer not null default 0,
  error_message text
);

create index scrape_runs_source_idx on scrape_runs (source_id, started_at desc);

-- updated_at triggers
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trg_sources_updated_at before update on sources
  for each row execute function set_updated_at();
create trigger trg_venues_updated_at before update on venues
  for each row execute function set_updated_at();
create trigger trg_events_updated_at before update on events
  for each row execute function set_updated_at();
