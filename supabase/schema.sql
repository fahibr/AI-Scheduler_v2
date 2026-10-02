-- AI Door Scheduler — Supabase schema (users, regions, login audit)
-- Run in Supabase SQL Editor. Safe to re-run (IF NOT EXISTS / additive alters).

create extension if not exists "pgcrypto";

create table if not exists public.app_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  first_name text,
  last_name text,
  role text not null default 'user'
    check (role in ('user', 'admin')),
  is_active boolean not null default true,
  -- Regular users: exactly one region in regions[], access_all_regions = false
  -- Admins: access_all_regions = true (all) OR regions[] = granted subset
  access_all_regions boolean not null default false,
  regions text[] not null default '{}',
  created_at timestamptz not null default now(),
  created_by text,
  last_login_at timestamptz
);

-- Additive columns if an older schema already exists
alter table public.app_users
  add column if not exists first_name text;
alter table public.app_users
  add column if not exists last_name text;
alter table public.app_users
  add column if not exists access_all_regions boolean not null default false;
alter table public.app_users
  add column if not exists regions text[] not null default '{}';

create table if not exists public.login_events (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  logged_in_at timestamptz not null default now(),
  success boolean not null default true,
  detail text,
  user_agent text
);

create index if not exists login_events_email_idx
  on public.login_events (email);

create index if not exists login_events_logged_in_at_idx
  on public.login_events (logged_in_at desc);

create index if not exists app_users_email_idx
  on public.app_users (email);

-- Seed primary admin (all regions)
insert into public.app_users (
  email, first_name, last_name, role, is_active,
  access_all_regions, regions, created_by
)
values (
  'fahmi.ibrahim@assaabloy.com',
  'Fahmi',
  'Ibrahim',
  'admin',
  true,
  true,
  '{}',
  'seed'
)
on conflict (email) do update
  set role = 'admin',
      is_active = true,
      access_all_regions = true,
      regions = '{}',
      first_name = coalesce(public.app_users.first_name, excluded.first_name),
      last_name = coalesce(public.app_users.last_name, excluded.last_name);

alter table public.app_users enable row level security;
alter table public.login_events enable row level security;
