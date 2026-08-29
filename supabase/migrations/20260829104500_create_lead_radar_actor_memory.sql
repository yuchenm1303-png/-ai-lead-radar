create table if not exists public.lead_radar_actor_memory (
  source text not null,
  author_id text not null,
  author_name text,
  observations integer not null default 0 check (observations >= 0),
  buyer_count integer not null default 0 check (buyer_count >= 0),
  provider_count integer not null default 0 check (provider_count >= 0),
  recruiter_count integer not null default 0 check (recruiter_count >= 0),
  learner_count integer not null default 0 check (learner_count >= 0),
  content_count integer not null default 0 check (content_count >= 0),
  unknown_count integer not null default 0 check (unknown_count >= 0),
  buy_count integer not null default 0 check (buy_count >= 0),
  sell_count integer not null default 0 check (sell_count >= 0),
  recruit_count integer not null default 0 check (recruit_count >= 0),
  non_transactional_count integer not null default 0 check (non_transactional_count >= 0),
  unknown_direction_count integer not null default 0 check (unknown_direction_count >= 0),
  max_buyer_probability integer not null default 0 check (max_buyer_probability between 0 and 100),
  last_role text not null default 'unknown' check (last_role in ('buyer','provider','recruiter','learner','content','unknown')),
  last_direction text not null default 'unknown' check (last_direction in ('buy','sell','recruit','non_transactional','unknown')),
  last_confidence integer not null default 0 check (last_confidence between 0 and 100),
  last_source_id text,
  metadata jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (source, author_id)
);

create index if not exists lead_radar_actor_memory_last_seen_idx
  on public.lead_radar_actor_memory (last_seen_at desc);
create index if not exists lead_radar_actor_memory_provider_idx
  on public.lead_radar_actor_memory (provider_count desc, observations desc);
create index if not exists lead_radar_actor_memory_buyer_idx
  on public.lead_radar_actor_memory (buyer_count desc, max_buyer_probability desc);

alter table public.lead_radar_actor_memory enable row level security;
revoke all on table public.lead_radar_actor_memory from anon, authenticated;

create or replace function public.lead_radar_record_actor_observation(
  p_source text,
  p_author_id text,
  p_author_name text,
  p_source_id text,
  p_actor_role text,
  p_direction text,
  p_buyer_probability integer,
  p_confidence integer
)
returns void
language plpgsql
security definer
set search_path = public
as $function$
declare
  v_source text := left(coalesce(nullif(trim(p_source), ''), 'unknown'), 40);
  v_author_id text := left(coalesce(trim(p_author_id), ''), 160);
  v_role text := case when p_actor_role in ('buyer','provider','recruiter','learner','content','unknown') then p_actor_role else 'unknown' end;
  v_direction text := case when p_direction in ('buy','sell','recruit','non_transactional','unknown') then p_direction else 'unknown' end;
  v_probability integer := greatest(0, least(100, coalesce(p_buyer_probability, 0)));
  v_confidence integer := greatest(0, least(100, coalesce(p_confidence, 0)));
begin
  if v_author_id = '' then
    return;
  end if;

  insert into public.lead_radar_actor_memory (
    source,
    author_id,
    author_name,
    observations,
    buyer_count,
    provider_count,
    recruiter_count,
    learner_count,
    content_count,
    unknown_count,
    buy_count,
    sell_count,
    recruit_count,
    non_transactional_count,
    unknown_direction_count,
    max_buyer_probability,
    last_role,
    last_direction,
    last_confidence,
    last_source_id,
    first_seen_at,
    last_seen_at
  ) values (
    v_source,
    v_author_id,
    nullif(left(coalesce(p_author_name, ''), 120), ''),
    1,
    case when v_role = 'buyer' then 1 else 0 end,
    case when v_role = 'provider' then 1 else 0 end,
    case when v_role = 'recruiter' then 1 else 0 end,
    case when v_role = 'learner' then 1 else 0 end,
    case when v_role = 'content' then 1 else 0 end,
    case when v_role = 'unknown' then 1 else 0 end,
    case when v_direction = 'buy' then 1 else 0 end,
    case when v_direction = 'sell' then 1 else 0 end,
    case when v_direction = 'recruit' then 1 else 0 end,
    case when v_direction = 'non_transactional' then 1 else 0 end,
    case when v_direction = 'unknown' then 1 else 0 end,
    v_probability,
    v_role,
    v_direction,
    v_confidence,
    nullif(left(coalesce(p_source_id, ''), 160), ''),
    now(),
    now()
  )
  on conflict (source, author_id) do update set
    author_name = coalesce(excluded.author_name, lead_radar_actor_memory.author_name),
    observations = lead_radar_actor_memory.observations + 1,
    buyer_count = lead_radar_actor_memory.buyer_count + excluded.buyer_count,
    provider_count = lead_radar_actor_memory.provider_count + excluded.provider_count,
    recruiter_count = lead_radar_actor_memory.recruiter_count + excluded.recruiter_count,
    learner_count = lead_radar_actor_memory.learner_count + excluded.learner_count,
    content_count = lead_radar_actor_memory.content_count + excluded.content_count,
    unknown_count = lead_radar_actor_memory.unknown_count + excluded.unknown_count,
    buy_count = lead_radar_actor_memory.buy_count + excluded.buy_count,
    sell_count = lead_radar_actor_memory.sell_count + excluded.sell_count,
    recruit_count = lead_radar_actor_memory.recruit_count + excluded.recruit_count,
    non_transactional_count = lead_radar_actor_memory.non_transactional_count + excluded.non_transactional_count,
    unknown_direction_count = lead_radar_actor_memory.unknown_direction_count + excluded.unknown_direction_count,
    max_buyer_probability = greatest(lead_radar_actor_memory.max_buyer_probability, excluded.max_buyer_probability),
    last_role = excluded.last_role,
    last_direction = excluded.last_direction,
    last_confidence = excluded.last_confidence,
    last_source_id = excluded.last_source_id,
    last_seen_at = now();
end;
$function$;

revoke all on function public.lead_radar_record_actor_observation(text,text,text,text,text,text,integer,integer) from public, anon, authenticated;
grant execute on function public.lead_radar_record_actor_observation(text,text,text,text,text,text,integer,integer) to service_role;

comment on table public.lead_radar_actor_memory is
  'Zero-provider-cost actor history accumulated from already observed posts and comments. Used only as a semantic prior; explicit current intent overrides history.';
