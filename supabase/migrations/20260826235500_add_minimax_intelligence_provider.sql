alter table public.lead_radar_intelligence_settings
  drop constraint if exists lead_radar_intelligence_settings_provider_check;

alter table public.lead_radar_intelligence_settings
  add constraint lead_radar_intelligence_settings_provider_check
  check (provider in ('openai','minimax'));

create or replace function public.lead_radar_get_intelligence_secret(p_provider text)
returns text
language sql
security definer
set search_path = pg_catalog, public, vault
as $$
  select ds.decrypted_secret
  from vault.decrypted_secrets as ds
  where ds.name = case lower(coalesce(p_provider, ''))
    when 'openai' then 'lead_radar_openai_api_key'
    when 'minimax' then 'lead_radar_minimax_api_key'
    else null
  end
  order by ds.updated_at desc
  limit 1;
$$;

revoke all on function public.lead_radar_get_intelligence_secret(text) from public;
revoke all on function public.lead_radar_get_intelligence_secret(text) from anon, authenticated;
grant execute on function public.lead_radar_get_intelligence_secret(text) to service_role;

comment on function public.lead_radar_get_intelligence_secret(text) is
  'Returns one semantic-provider credential from Supabase Vault. Callable only by service_role.';
