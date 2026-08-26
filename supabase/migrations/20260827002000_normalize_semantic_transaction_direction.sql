alter table public.lead_radar_semantic_assessments
  drop constraint if exists lead_radar_semantic_assessments_transaction_direction_check;

alter table public.lead_radar_semantic_assessments
  add constraint lead_radar_semantic_assessments_transaction_direction_check
  check (transaction_direction in ('buy','sell','recruit','non_transactional','unknown','learn','discuss'));

comment on column public.lead_radar_semantic_assessments.transaction_direction is
  'Transaction direction. V3.2+ uses buy/sell/recruit/non_transactional/unknown; learn/discuss remain accepted only for historical rows.';
