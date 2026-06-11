-- Fix mutable search_path on trigger function
create or replace function set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Enable RLS everywhere
alter table sources enable row level security;
alter table venues enable row level security;
alter table events enable row level security;
alter table event_sources enable row level security;
alter table scrape_runs enable row level security;

-- Public (anon/authenticated) read-only access to the queryable dataset.
-- Writes are performed by the nightly job using the service_role key,
-- which bypasses RLS entirely.
create policy "Public read access" on events
  for select using (true);

create policy "Public read access" on venues
  for select using (true);

create policy "Public read access" on sources
  for select using (true);

-- event_sources / scrape_runs are internal/operational; no public policies
-- means they are inaccessible via PostgREST to anon/authenticated roles.
