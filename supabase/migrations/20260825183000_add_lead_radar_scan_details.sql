alter table public.lead_radar_scan_runs
  add column if not exists details jsonb not null default '{}'::jsonb;

comment on column public.lead_radar_scan_runs.details is
  'Non-secret collector audit metadata such as query stats, duplicate counts, and authenticated workflow identity.';
