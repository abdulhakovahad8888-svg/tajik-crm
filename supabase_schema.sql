-- ══════════════════════════════════════════════
-- TAJIK AI CRM — Supabase Schema
-- Запусти это в Supabase → SQL Editor
-- ══════════════════════════════════════════════

-- BUSINESSES (клиенты-бизнесы на платформе)
create table businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  owner_email text unique not null,
  plan text default 'growth' check (plan in ('start','growth','pro','enterprise')),
  telegram_bot_token text,
  telegram_manager_chat_id text,
  instagram_access_token text,
  instagram_page_id text,
  whatsapp_token text,
  bot_active boolean default true,
  created_at timestamptz default now()
);

-- LEADS (лиды)
create table leads (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references businesses(id) on delete cascade,
  name text not null,
  phone text,
  product text,
  channel text check (channel in ('telegram','instagram','whatsapp','site')),
  channel_user_id text,  -- telegram chat_id / instagram user_id
  status text default 'new' check (status in ('new','work','pay','done','lost')),
  amount numeric default 0,
  city text,
  ai_summary text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- MESSAGES (история переписки)
create table messages (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id) on delete cascade,
  business_id uuid references businesses(id) on delete cascade,
  role text check (role in ('user','bot','manager')),
  content text not null,
  channel text,
  created_at timestamptz default now()
);

-- COMMENTS (заметки менеджера)
create table comments (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid references leads(id) on delete cascade,
  manager_name text,
  content text not null,
  created_at timestamptz default now()
);

-- BOT KNOWLEDGE (база знаний бота)
create table bot_knowledge (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references businesses(id) on delete cascade,
  question text not null,
  answer text not null,
  category text default 'general',
  active boolean default true,
  created_at timestamptz default now()
);

-- ── ИНДЕКСЫ ──
create index idx_leads_business on leads(business_id);
create index idx_leads_status on leads(status);
create index idx_leads_channel on leads(channel);
create index idx_messages_lead on messages(lead_id);

-- ── ROW LEVEL SECURITY ──
alter table leads enable row level security;
alter table messages enable row level security;
alter table comments enable row level security;
alter table bot_knowledge enable row level security;

-- ── REALTIME (для живых обновлений в CRM) ──
alter publication supabase_realtime add table leads;
alter publication supabase_realtime add table messages;

-- ── DEMO DATA (тестовый бизнес) ──
insert into businesses (name, owner_email, plan, bot_active)
values ('Avicena Life Demo', 'avicena@demo.com', 'pro', true);

-- TRIGGER: обновлять updated_at при изменении лида
create or replace function update_updated_at()
returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

create trigger leads_updated_at
  before update on leads
  for each row execute function update_updated_at();
