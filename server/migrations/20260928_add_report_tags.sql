-- Apply to the existing Supabase database before enabling report organization.
alter table reports add column if not exists tags text[] not null default '{}';
