create extension if not exists pgcrypto;

alter table if exists product_files
  add column if not exists manufacturer text,
  add column if not exists package text,
  add column if not exists cct_k numeric,
  add column if not exists cri numeric,
  add column if not exists luminous_flux_lm numeric,
  add column if not exists efficacy_lm_w numeric,
  add column if not exists forward_voltage_typ_v numeric,
  add column if not exists test_current_ma numeric;

create table if not exists product_files (
  id uuid primary key default gen_random_uuid(),
  part_number text not null,
  file_type text not null,
  file_name text not null,
  storage_path text not null,
  description text,
  manufacturer text,
  package text,
  cct_k numeric,
  cri numeric,
  luminous_flux_lm numeric,
  efficacy_lm_w numeric,
  forward_voltage_typ_v numeric,
  test_current_ma numeric,
  created_at timestamptz default now()
);
