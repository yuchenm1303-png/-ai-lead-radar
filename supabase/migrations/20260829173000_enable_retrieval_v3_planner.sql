update public.lead_radar_retrieval_settings
set manual_queries_per_scan = 3,
    updated_at = now()
where id = 1
  and manual_provider_calls_per_scan >= 3
  and manual_queries_per_scan < 3;

comment on table public.lead_radar_retrieval_settings is
  'Retrieval runtime budget. V3 uses up to three intent-diverse probes per manual scan without increasing the existing provider-call cap.';
