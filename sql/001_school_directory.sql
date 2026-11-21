-- MIT: Skolo Saka NPC. No third-party school records are bundled with this schema.
-- PostgreSQL 15+. Safe on the existing Skolo Saka public.schools table.
-- Run as the database owner. Import routines are private, not public REST RPCs.
create schema if not exists school_directory;
revoke all on schema school_directory from public;

do $setup$
begin
  if to_regclass('public.schools') is null then
    create table public.schools (
      id uuid primary key default gen_random_uuid(),
      name text not null,
      level text not null check (level in ('primary','high','combined','other')),
      emis_number text unique,
      province text not null,
      municipality text,
      town text,
      verified boolean not null default false,
      created_at timestamptz not null default now()
    );
    alter table public.schools enable row level security;
    create policy schools_directory_read on public.schools for select using (true);
    if exists(select from pg_roles where rolname='anon') then
      grant select on public.schools to anon;
    end if;
    if exists(select from pg_roles where rolname='authenticated') then
      grant select on public.schools to authenticated;
    end if;
  end if;
end
$setup$;

create table if not exists school_directory.sources (
  source_key text primary key,
  source_url text not null,
  dataset_release text,
  source_sha256 text,
  last_checked_at timestamptz not null default now(),
  last_successful_sync_at timestamptz,
  record_count integer,
  licence_note text not null
);
create table if not exists school_directory.import_runs (
  id uuid primary key default gen_random_uuid(),
  source_key text not null references school_directory.sources(source_key),
  source jsonb not null,
  status text not null default 'staging' check(status in ('staging','previewed','review_required','applied','unchanged')),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  report jsonb
);
create table if not exists school_directory.staged_schools (
  run_id uuid not null references school_directory.import_runs(id) on delete cascade,
  emis_number text not null check(emis_number ~ '^[0-9]{9}$'),
  fields jsonb not null,
  primary key(run_id,emis_number)
);
create table if not exists school_directory.observations (
  source_key text not null references school_directory.sources(source_key),
  emis_number text not null,
  school_id uuid references public.schools(id) on delete restrict,
  last_seen_run uuid not null references school_directory.import_runs(id),
  source_fields jsonb not null,
  last_applied_fields jsonb,
  primary key(source_key,emis_number)
);
create table if not exists school_directory.changes (
  run_id uuid not null references school_directory.import_runs(id),
  school_id uuid not null references public.schools(id) on delete restrict,
  action text not null check(action in ('insert','update')),
  before_fields jsonb,
  after_fields jsonb not null,
  primary key(run_id,school_id)
);
alter table school_directory.sources enable row level security;
alter table school_directory.import_runs enable row level security;
alter table school_directory.staged_schools enable row level security;
alter table school_directory.observations enable row level security;
alter table school_directory.changes enable row level security;
revoke all on all tables in schema school_directory from public;

create or replace function school_directory.canonical_fields(p jsonb)
returns jsonb language sql immutable security invoker set search_path=''
as $fn$
  select jsonb_object_agg(k, case when v='null'::jsonb then 'null'::jsonb
    else to_jsonb(lower(regexp_replace(btrim(v#>>'{}'),'\s+',' ','g'))) end)
  from jsonb_each(p) as fields(k,v)
$fn$;

create or replace function school_directory.begin_import(p_source jsonb,p_run_id uuid default gen_random_uuid())
returns jsonb language plpgsql security invoker set search_path=''
as $fn$
declare prior school_directory.sources; result jsonb;
begin
  perform pg_advisory_xact_lock(hashtext('school-directory'));
  if p_source->>'key' !~ '^[a-z0-9-]{1,60}$'
     or p_source->>'release' !~ '^20[0-9]{2}-Q[1-4]$'
     or p_source->>'sha256' !~ '^[a-f0-9]{64}$'
     or p_source->>'url' !~ '^https://(www\.)?education\.gov\.za/'
     or coalesce(p_source->>'licence','')='' then
    raise exception 'Invalid source provenance';
  end if;
  if not (p_source ?& array['key','release','sha256','url','licence']) then
    raise exception 'Required source provenance is missing';
  end if;
  select * into prior from school_directory.sources where source_key=p_source->>'key';
  insert into school_directory.sources(source_key,source_url,licence_note)
    values(p_source->>'key',p_source->>'url',p_source->>'licence')
    on conflict(source_key) do update set last_checked_at=now();
  if prior.source_sha256=p_source->>'sha256' and prior.dataset_release=p_source->>'release'
     and prior.last_successful_sync_at is not null then
    result:=jsonb_build_object('run_id',p_run_id,'status','unchanged','release',prior.dataset_release,'records',prior.record_count);
    insert into school_directory.import_runs(id,source_key,source,status,completed_at,report)
      values(p_run_id,p_source->>'key',p_source,'unchanged',now(),result);
    return result;
  end if;
  insert into school_directory.import_runs(id,source_key,source)
    values(p_run_id,p_source->>'key',p_source);
  return jsonb_build_object('run_id',p_run_id,'status','staging');
end
$fn$;

create or replace function school_directory.stage_import(p_run_id uuid,p_rows jsonb)
returns jsonb language plpgsql security invoker set search_path=''
as $fn$
declare n integer;
begin
  perform 1 from school_directory.import_runs where id=p_run_id and status='staging' for update;
  if not found then raise exception 'Import run is not accepting rows'; end if;
  if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows) not between 1 and 1000 then
    raise exception 'An import batch must contain 1 to 1000 rows';
  end if;
  if exists(select 1 from jsonb_array_elements(p_rows) r where
    jsonb_typeof(r) is distinct from 'object'
    or coalesce(r->>'emis_number','') !~ '^[0-9]{9}$'
    or length(coalesce(r->>'name','')) not between 1 and 300
    or coalesce(r->>'level','') not in ('primary','high','combined','other')
    or coalesce(r->>'province','') not in ('Eastern Cape','Free State','Gauteng','KwaZulu-Natal','Limpopo','Mpumalanga','Northern Cape','North West','Western Cape')
    or coalesce(r->>'status','') not in ('OPEN','CLOSED','OPERATIONAL','NON-OPERATIONAL','PENDING OPEN')
    or length(coalesce(r->>'town',''))>200 or length(coalesce(r->>'municipality',''))>200
    or exists(select 1 from jsonb_object_keys(r) as k where k not in ('emis_number','name','level','province','town','municipality','status','old_emis','new_emis'))
  ) then raise exception 'Invalid or unexpected school fields'; end if;
  insert into school_directory.staged_schools(run_id,emis_number,fields)
    select p_run_id,r->>'emis_number',r from jsonb_array_elements(p_rows) r;
  get diagnostics n=row_count;
  if (select count(*) from school_directory.staged_schools where run_id=p_run_id)>100000 then
    raise exception 'Import exceeds 100000 schools';
  end if;
  return jsonb_build_object('staged',n);
end
$fn$;

create or replace function school_directory.finish_import(p_run_id uuid,p_apply boolean default false,p_expected_plan text default null)
returns jsonb language plpgsql security invoker set search_path=''
as $fn$
declare
  run school_directory.import_runs;
  source school_directory.sources;
  total_rows integer; existing_count integer; additions integer; updates integer;
  protected_count integer; reviews integer; missing integer; plan_hash text; v_report jsonb;
  reasons jsonb:='[]'::jsonb;
begin
  perform pg_advisory_xact_lock(hashtext('school-directory'));
  select * into run from school_directory.import_runs where id=p_run_id for update;
  if not found then raise exception 'Import run does not exist'; end if;
  if run.status in ('applied','unchanged') then return run.report; end if;
  select * into source from school_directory.sources where source_key=run.source_key for update;
  select count(*) into total_rows from school_directory.staged_schools where run_id=p_run_id;
  if total_rows<coalesce((run.source->>'min_rows')::integer,20000) or total_rows>100000 then
    raise exception 'National row-count validation failed';
  end if;
  if exists(select 1 from jsonb_array_elements_text(coalesce(run.source->'required_provinces','[]'::jsonb)) province
    where not exists(select 1 from school_directory.staged_schools s where s.run_id=p_run_id and s.fields->>'province'=province)) then
    raise exception 'National province coverage validation failed';
  end if;
  select count(*) into existing_count from public.schools where emis_number is not null;

  create temporary table if not exists directory_import_plan (
    emis_number text primary key, school_id uuid, action text, old_fields jsonb, new_fields jsonb
  ) on commit drop;
  truncate pg_temp.directory_import_plan;
  insert into pg_temp.directory_import_plan
  with candidates as (
    select incoming.emis_number,s.id,s.verified,prior.last_applied_fields,
      jsonb_build_object('name',s.name,'level',s.level,'province',s.province,'town',s.town,'municipality',s.municipality) as old_fields,
      jsonb_build_object('name',incoming.fields->>'name','level',incoming.fields->>'level','province',incoming.fields->>'province',
        'town',coalesce(incoming.fields->>'town',s.town),'municipality',coalesce(incoming.fields->>'municipality',s.municipality)) as new_fields,
      incoming.fields->>'status' as status,
      exists(select 1 from public.schools previous where
        (previous.emis_number=incoming.fields->>'old_emis' and previous.emis_number<>incoming.emis_number)
        or (previous.emis_number=incoming.fields->>'new_emis' and previous.emis_number<>incoming.emis_number)) as renumber
    from school_directory.staged_schools incoming
    left join public.schools s on s.emis_number=incoming.emis_number
    left join school_directory.observations prior on prior.source_key=run.source_key and prior.emis_number=incoming.emis_number
    where incoming.run_id=p_run_id
  )
  select emis_number,id,
    case when renumber and id is null then 'review'
      when status not in ('OPEN','OPERATIONAL') and id is not null then 'review'
      when status not in ('OPEN','OPERATIONAL') then 'inactive'
      when id is null then 'insert'
      when school_directory.canonical_fields(old_fields)=school_directory.canonical_fields(new_fields) then 'unchanged'
      when last_applied_fields is null or
        school_directory.canonical_fields(last_applied_fields)<>school_directory.canonical_fields(old_fields) then 'protected'
      else 'update' end,
    case when id is null then null else old_fields end,new_fields
  from candidates;

  select count(*) filter(where action='insert'),count(*) filter(where action='update'),
    count(*) filter(where action='protected'),count(*) filter(where action='review')
    into additions,updates,protected_count,reviews from pg_temp.directory_import_plan;
  select count(*) into missing from public.schools s where s.emis_number is not null and
    not exists(select 1 from school_directory.staged_schools incoming where incoming.run_id=p_run_id and incoming.emis_number=s.emis_number);
  select md5(coalesce(jsonb_agg(jsonb_build_array(p.emis_number,p.school_id,p.action,p.old_fields,p.new_fields) order by p.emis_number)::text,'[]')
    || run.source::text) into plan_hash from pg_temp.directory_import_plan p;
  if existing_count=0 then reasons:=reasons||'"empty_database_requires_review"'::jsonb; end if;
  if additions+updates>greatest(100,existing_count*0.10) then reasons:=reasons||'"more_than_10_percent_changes"'::jsonb; end if;
  if missing>greatest(25,existing_count*0.05) then reasons:=reasons||'"more_than_5_percent_missing"'::jsonb; end if;
  if reviews>0 then reasons:=reasons||'"closures_or_emis_changes_require_review"'::jsonb; end if;
  if source.dataset_release is not null and run.source->>'release'<source.dataset_release then
    reasons:=reasons||'"source_release_is_older_than_last_import"'::jsonb;
  end if;
  v_report:=jsonb_build_object('run_id',p_run_id,'release',run.source->>'release','records',total_rows,
    'existing_records',existing_count,'insert',additions,'update',updates,'protected',protected_count,
    'review',reviews,'missing',missing,'plan_hash',plan_hash,'reasons',reasons,
    'status',case when jsonb_array_length(reasons)>0 then 'review_required' else 'previewed' end);
  update school_directory.import_runs set status=v_report->>'status',report=v_report where id=p_run_id;
  if not p_apply or jsonb_array_length(reasons)>0 then return v_report; end if;
  if p_expected_plan is distinct from plan_hash then
    raise exception 'Directory changed since preview; produce a fresh preview';
  end if;

  -- Every live write is in this transaction. Existing ids, EMIS numbers and verified flags never change.
  with changed as (
    update public.schools s set
      name=(jsonb_populate_record(null::public.schools,p.new_fields)).name,
      level=(jsonb_populate_record(null::public.schools,p.new_fields)).level,
      province=(jsonb_populate_record(null::public.schools,p.new_fields)).province,
      town=(jsonb_populate_record(null::public.schools,p.new_fields)).town,
      municipality=(jsonb_populate_record(null::public.schools,p.new_fields)).municipality
    from pg_temp.directory_import_plan p where p.action='update' and s.id=p.school_id
    returning s.id
  ) select count(*) into updates from changed;
  insert into public.schools(name,level,emis_number,province,town,municipality)
    select r.name,r.level,p.emis_number,r.province,r.town,r.municipality
    from pg_temp.directory_import_plan p cross join lateral jsonb_populate_record(null::public.schools,p.new_fields) r
    where p.action='insert';
  insert into school_directory.changes(run_id,school_id,action,before_fields,after_fields)
    select p_run_id,s.id,p.action,p.old_fields,p.new_fields
    from pg_temp.directory_import_plan p join public.schools s on s.emis_number=p.emis_number
    where p.action in ('insert','update');
  insert into school_directory.observations(source_key,emis_number,school_id,last_seen_run,source_fields,last_applied_fields)
    select run.source_key,p.emis_number,s.id,p_run_id,incoming.fields,
      case when p.action in ('insert','update','unchanged') then p.new_fields else null end
    from pg_temp.directory_import_plan p
    join school_directory.staged_schools incoming on incoming.run_id=p_run_id and incoming.emis_number=p.emis_number
    left join public.schools s on s.emis_number=p.emis_number
    on conflict(source_key,emis_number) do update set school_id=excluded.school_id,last_seen_run=excluded.last_seen_run,
      source_fields=excluded.source_fields,last_applied_fields=coalesce(excluded.last_applied_fields,school_directory.observations.last_applied_fields);
  update school_directory.sources set source_url=run.source->>'url',dataset_release=run.source->>'release',
    source_sha256=run.source->>'sha256',last_successful_sync_at=now(),record_count=total_rows,
    licence_note=run.source->>'licence' where source_key=run.source_key;
  v_report:=v_report||jsonb_build_object('status','applied','update',updates);
  update school_directory.import_runs set status='applied',completed_at=now(),report=v_report where id=p_run_id;
  delete from school_directory.staged_schools where run_id=p_run_id;
  return v_report;
end
$fn$;

revoke all on all functions in schema school_directory from public;
do $grants$
begin
  if exists(select from pg_roles where rolname='service_role') then
    grant usage on schema school_directory to service_role;
    grant select,insert,update,delete on all tables in schema school_directory to service_role;
    grant execute on all functions in schema school_directory to service_role;
  end if;
end
$grants$;
