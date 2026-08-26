alter view public.lead_radar_query_scheduler_stats set (security_invoker = true);

revoke all on table public.lead_radar_retrieval_settings from anon, authenticated;
revoke all on table public.lead_radar_query_runs from anon, authenticated;
revoke all on table public.lead_radar_query_observations from anon, authenticated;
revoke all on sequence public.lead_radar_query_runs_id_seq from anon, authenticated;
revoke all on sequence public.lead_radar_query_observations_id_seq from anon, authenticated;
revoke all on table public.lead_radar_query_scheduler_stats from anon, authenticated;

revoke all on function public.lead_radar_claim_scan_work(text, boolean) from public, anon, authenticated;
