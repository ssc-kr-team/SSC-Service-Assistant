create extension if not exists pgcrypto;

create table if not exists products (
  part_number text primary key,
  manufacturer text,
  brand text,
  series text,
  status text,
  application text,
  package text,
  cct_k numeric,
  cri numeric,
  luminous_flux_lm numeric,
  efficacy_lm_w numeric,
  forward_voltage_typ_v numeric,
  test_current_ma numeric,
  macadam_step numeric,
  price_level text,
  lead_time_weeks numeric,
  replacement_for text,
  competitor_brand text,
  remark text
);

create table if not exists review_candidates (
  candidate_id text primary key,
  source_filename text,
  pdf_type text,
  candidate jsonb,
  raw_text_excerpt text,
  created_at timestamptz default now()
);

create table if not exists product_files (
  id uuid primary key default gen_random_uuid(),
  part_number text not null,
  file_type text not null,
  file_name text not null,
  storage_path text not null,
  description text,
  created_at timestamptz default now()
);
