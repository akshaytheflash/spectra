-- =============================================================================
-- SPECTRA schema for Supabase (managed PostgreSQL)
--   * tables matching backend/app/db/models.py
--   * pg_trgm fuzzy search indexes
--   * JSONB evidence / explanation / OCR payloads
--   * Row Level Security with Supabase Auth analyst roles
--   * Realtime publication for `alerts` (live notification bell)
-- Apply with:  psql "$DATABASE_URL" -f backend/migrations/001_init.sql
-- Idempotent: safe to re-run.
-- =============================================================================

create extension if not exists pg_trgm;
create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------- channels ---
create table if not exists channels (
  id                uuid primary key default uuid_generate_v4(),
  platform          text not null default 'telegram',
  username          text not null,
  title             text,
  is_public         boolean not null default true,
  is_verified       boolean not null default false,
  subscriber_count  integer,
  first_seen        timestamptz not null default now(),
  last_seen         timestamptz not null default now(),
  meta              jsonb not null default '{}'::jsonb,
  unique (platform, username)
);
create index if not exists ix_channels_username on channels (username);

-- ------------------------------------------------------------------- posts ---
create table if not exists posts (
  id            uuid primary key default uuid_generate_v4(),
  external_id   text,
  platform      text not null default 'telegram',
  channel_id    uuid references channels(id) on delete set null,
  channel       text not null,
  timestamp     timestamptz not null default now(),
  text          text not null default '',
  media_path    text,
  media_type    text,
  media_sha256  text,
  permalink     text,
  views         integer,
  language      text,
  label         text,
  status        text not null default 'ok',
  ocr           jsonb not null default '{}'::jsonb,
  transcript    jsonb not null default '{}'::jsonb,
  qr            jsonb not null default '{}'::jsonb,
  text_hash     text,
  image_hash    text,
  audio_hash    text,
  created_at    timestamptz not null default now()
);
create index if not exists ix_posts_channel on posts (channel);
create index if not exists ix_posts_ts on posts (timestamp desc);
create index if not exists ix_posts_text_trgm on posts using gin (text gin_trgm_ops);
create index if not exists ix_posts_image_hash on posts (image_hash);
create index if not exists ix_posts_external on posts (external_id);

-- ------------------------------------------------------------- identifiers ---
create table if not exists identifiers (
  id          uuid primary key default uuid_generate_v4(),
  post_id     uuid not null references posts(id) on delete cascade,
  type        text not null,
  value       text not null,
  raw         text not null default '',
  source      text not null default 'text',
  confidence  double precision not null default 0.8,
  channel     text not null,
  timestamp   timestamptz not null default now(),
  extra       jsonb not null default '{}'::jsonb,
  constraint uq_identifier_post unique (post_id, type, value, source)
);
create index if not exists ix_identifiers_value on identifiers (value);
create index if not exists ix_identifiers_value_trgm on identifiers using gin (value gin_trgm_ops);
create index if not exists ix_identifiers_type on identifiers (type);
create index if not exists ix_identifiers_channel on identifiers (channel);

-- ------------------------------------------------------------------ alerts ---
create table if not exists alerts (
  id                  uuid primary key default uuid_generate_v4(),
  post_id             uuid not null unique references posts(id) on delete cascade,
  category            text not null,
  subcategories       jsonb not null default '{}'::jsonb,
  risk                text not null,
  confidence          double precision not null default 0,
  explanation         jsonb not null default '{}'::jsonb,
  identifier_summary  jsonb not null default '{}'::jsonb,
  cluster_id          uuid,
  status              text not null default 'new',
  analyst_notes       jsonb not null default '[]'::jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  check (status in ('new','under_review','confirmed','dismissed')),
  check (risk in ('HIGH','MEDIUM','LOW'))
);
create index if not exists ix_alerts_category on alerts (category);
create index if not exists ix_alerts_risk on alerts (risk);
create index if not exists ix_alerts_status on alerts (status);
create index if not exists ix_alerts_cluster on alerts (cluster_id);
create index if not exists ix_alerts_created on alerts (created_at desc);

-- ---------------------------------------------------------------- evidence ---
-- Immutable by policy: only INSERT/SELECT are granted to analysts (no UPDATE).
create table if not exists evidence (
  id           uuid primary key default uuid_generate_v4(),
  alert_id     uuid not null unique references alerts(id) on delete cascade,
  payload      jsonb not null,
  sha256       text not null,
  captured_at  timestamptz not null default now()
);
create index if not exists ix_evidence_sha on evidence (sha256);

-- --------------------------------------------------------------- audit_log ---
create table if not exists audit_log (
  id           uuid primary key default uuid_generate_v4(),
  alert_id     uuid references alerts(id) on delete cascade,
  actor        text not null default 'analyst',
  action       text not null,
  from_status  text,
  to_status    text,
  notes        text,
  at           timestamptz not null default now()
);
create index if not exists ix_audit_alert on audit_log (alert_id);

-- --------------------------------------------------------------- campaigns ---
create table if not exists campaigns (
  id                 uuid primary key default uuid_generate_v4(),
  title              text not null,
  category           text not null,
  post_ids           jsonb not null default '[]'::jsonb,
  channels           jsonb not null default '[]'::jsonb,
  shared_identifiers jsonb not null default '[]'::jsonb,
  time_span          jsonb not null default '{}'::jsonb,
  posting_frequency  double precision not null default 0,
  similarity         double precision not null default 0,
  signature          text not null default '',
  created_at         timestamptz not null default now()
);
create unique index if not exists uq_campaign_signature on campaigns (signature);

-- -------------------------------------------------------------- ingest_jobs ---
create table if not exists ingest_jobs (
  id           uuid primary key default uuid_generate_v4(),
  kind         text not null,
  status       text not null default 'running',
  total        integer not null default 0,
  done         integer not null default 0,
  message      text not null default '',
  params       jsonb not null default '{}'::jsonb,
  result       jsonb not null default '{}'::jsonb,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz
);

-- ------------------------------------------------------------ lexicon_terms ---
create table if not exists lexicon_terms (
  id          uuid primary key default uuid_generate_v4(),
  category    text not null,
  term        text not null,
  kind        text not null default 'slang',
  weight      double precision not null default 1.0,
  language    text not null default 'any',
  created_by  text not null default 'analyst',
  created_at  timestamptz not null default now(),
  unique (category, term, kind)
);

-- --------------------------------------------------------------- model_runs ---
create table if not exists model_runs (
  id          uuid primary key default uuid_generate_v4(),
  version     text not null,
  trained_at  timestamptz not null default now(),
  metrics     jsonb not null default '{}'::jsonb,
  confusion   jsonb not null default '[]'::jsonb,
  labels      jsonb not null default '[]'::jsonb,
  train_size  integer not null default 0,
  test_size   integer not null default 0,
  notes       text
);

-- =============================================================================
-- Row Level Security
--   analysts  : read everything, create lexicon terms, update alert workflow
--   admins    : everything analysts can do
--   anon      : no access (the API uses the service role / server-side auth)
-- =============================================================================
create or replace function is_analyst() returns boolean
language sql stable as $$
  select coalesce((auth.jwt() -> 'app_metadata' ->> 'role') in ('analyst', 'admin'), false);
$$;

create or replace function is_admin() returns boolean
language sql stable as $$
  select coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false);
$$;

do $$
declare t text;
begin
  foreach t in array array['channels','posts','identifiers','alerts','evidence','audit_log',
                           'campaigns','ingest_jobs','lexicon_terms','model_runs']
  loop
    execute format('alter table %I enable row level security', t);
    -- read: analysts and admins
    execute format('drop policy if exists %I on %I', t || '_select_analyst', t);
    execute format('create policy %I on %I for select to authenticated using (is_analyst())', t || '_select_analyst', t);
  end loop;
end $$;

-- evidence is append-only for analysts
drop policy if exists evidence_insert_analyst on evidence;
create policy evidence_insert_analyst on evidence for insert to authenticated with check (is_analyst());
drop policy if exists evidence_no_update on evidence;
create policy evidence_no_update on evidence for update to authenticated using (false);
drop policy if exists evidence_no_delete on evidence;
create policy evidence_no_delete on evidence for delete to authenticated using (is_admin());

-- alerts workflow update (status + notes) by analysts
drop policy if exists alerts_update_analyst on alerts;
create policy alerts_update_analyst on alerts for update to authenticated
  using (is_analyst()) with check (is_analyst());

-- audit log: analysts append, never rewrite
drop policy if exists audit_insert_analyst on audit_log;
create policy audit_insert_analyst on audit_log for insert to authenticated with check (is_analyst());
drop policy if exists audit_no_update on audit_log;
create policy audit_no_update on audit_log for update to authenticated using (false);

-- lexicon editor
drop policy if exists lexicon_write_analyst on lexicon_terms;
create policy lexicon_write_analyst on lexicon_terms for all to authenticated
  using (is_analyst()) with check (is_analyst());

-- posts/identifiers are written by the collector (service role bypasses RLS);
-- no insert policy is granted to browser sessions on purpose.

-- =============================================================================
-- Realtime: stream new alerts to the dashboard bell
-- =============================================================================
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    begin
      alter publication supabase_realtime add table alerts;
    exception when duplicate_object then null;
    end;
  end if;
end $$;

-- =============================================================================
-- Triggers
-- =============================================================================
create or replace function touch_alert_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists trg_alerts_touch on alerts;
create trigger trg_alerts_touch before update on alerts
  for each row execute function touch_alert_updated_at();

-- Subscriber-count style denormalised channel stats for the overview page.
create or replace view channel_stats as
select p.channel,
       count(*) as posts,
       count(a.id) as alerts,
       max(p.timestamp) as last_post,
       count(distinct i.value) filter (where i.type in ('phone','upi','wallet')) as seller_ids
from posts p
left join alerts a on a.post_id = p.id
left join identifiers i on i.post_id = p.id
group by p.channel;
