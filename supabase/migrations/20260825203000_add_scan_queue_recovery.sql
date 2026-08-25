create or replace function public.lead_radar_claim_scan_request(p_run_id text)
returns table (
  id bigint,
  query_override text,
  max_queries integer,
  requested_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.lead_radar_scan_requests r
  set status = 'failed',
      finished_at = now(),
      updated_at = now(),
      error_text = 'Collector timeout recovery',
      result = jsonb_build_object('recovered', true, 'reason', 'running_timeout')
  where r.status = 'running'
    and r.started_at is not null
    and r.started_at < now() - interval '20 minutes';

  return query
  with candidate as (
    select r.id
    from public.lead_radar_scan_requests r
    where r.status = 'queued'
    order by r.requested_at asc
    for update skip locked
    limit 1
  ), claimed as (
    update public.lead_radar_scan_requests r
    set status = 'running',
        started_at = now(),
        updated_at = now(),
        github_run_id = nullif(left(coalesce(p_run_id, ''), 120), '')
    from candidate c
    where r.id = c.id
    returning r.id, r.query_override, r.max_queries, r.requested_at
  )
  select c.id, c.query_override, c.max_queries, c.requested_at
  from claimed c;
end;
$$;

revoke all on function public.lead_radar_claim_scan_request(text) from public, anon, authenticated;
