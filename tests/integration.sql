\set ON_ERROR_STOP on
begin;
-- This suite is deliberately limited to an empty, disposable test database.
do $$ begin
  if exists(select from public.schools) then raise exception 'Integration tests require an empty disposable database'; end if;
end $$;
insert into public.schools(name,level,emis_number,province,town,verified)
  select 'Fixture School '||n,'primary',(900000000+n)::text,'Limpopo','Fixture Town',n in (1,2)
  from generate_series(1,1000) n;
create temporary table initial_ids as select emis_number,id,verified from public.schools;

do $test$
declare source jsonb; v_run_id uuid; report jsonb; expected text; original_id uuid;
begin
  source:=jsonb_build_object('key','dbe-test','release','2025-Q3','sha256',repeat('1',64),
    'url','https://www.education.gov.za/fictional-test.csv','licence','Fictional test data: MIT',
    'min_rows',1,'required_provinces','[]'::jsonb);
  v_run_id:=(school_directory.begin_import(source)->>'run_id')::uuid;
  -- A previously imported school can receive source updates while staying verified.
  insert into school_directory.observations(source_key,emis_number,school_id,last_seen_run,source_fields,last_applied_fields)
    select 'dbe-test',emis_number,id,v_run_id,'{}'::jsonb,
      jsonb_build_object('name',name,'level',level,'province',province,'town',town,'municipality',municipality)
    from public.schools where emis_number='900000001';
  perform school_directory.stage_import(v_run_id,(select jsonb_agg(jsonb_build_object(
    'emis_number',emis_number,'name',case when emis_number in ('900000001','900000002') then name||' renamed' else name end,
    'level',level,'province',province,'town',null,'municipality',null,'status','OPEN')) from public.schools));
  perform school_directory.stage_import(v_run_id,'[{"emis_number":"900001001","name":"New Fictional School","level":"primary","province":"Limpopo","status":"OPEN"}]');
  report:=school_directory.finish_import(v_run_id,false,null);
  if report->>'status'<>'previewed' or (report->>'insert')::int<>1 or (report->>'update')::int<>1 or (report->>'protected')::int<>1 then
    raise exception 'Unexpected preview: %',report;
  end if;
  if (select count(*) from public.schools)<>1000 then raise exception 'Preview changed schools'; end if;
  expected:=report->>'plan_hash';
  -- Stale previews cannot overwrite concurrent changes.
  update public.schools set name='Concurrent change' where emis_number='900000001';
  begin
    perform school_directory.finish_import(v_run_id,true,expected);
    raise exception 'Expected stale-preview rejection';
  exception when raise_exception then
    if sqlerrm not like 'Directory changed since preview%' then raise; end if;
  end;
  update public.schools set name='Fixture School 1' where emis_number='900000001';
  report:=school_directory.finish_import(v_run_id,false,null);
  report:=school_directory.finish_import(v_run_id,true,report->>'plan_hash');
  if report->>'status'<>'applied' then raise exception 'Application failed: %',report; end if;
  if exists(select from initial_ids i join public.schools s using(emis_number) where i.id<>s.id or i.verified<>s.verified) then
    raise exception 'Existing IDs or verification changed';
  end if;
  if (select town from public.schools where emis_number='900000001')<>'Fixture Town' then raise exception 'Missing source town erased local town'; end if;
  if (select name from public.schools where emis_number='900000002')<>'Fixture School 2' then raise exception 'Verified school overwritten'; end if;
  if (select verified from public.schools where emis_number='900001001') then raise exception 'New school marked verified'; end if;
  if (select count(*) from school_directory.changes where run_id=v_run_id)<>2 then raise exception 'Audit missing'; end if;
  if school_directory.begin_import(source)->>'status'<>'unchanged' then raise exception 'Same source was not idempotent'; end if;

  -- Duplicate EMIS numbers fail even across separate batches.
  source:=jsonb_set(source,'{sha256}',to_jsonb(repeat('2',64)));
  v_run_id:=(school_directory.begin_import(source)->>'run_id')::uuid;
  perform school_directory.stage_import(v_run_id,'[{"emis_number":"900000001","name":"Fixture","level":"primary","province":"Limpopo","status":"OPEN"}]');
  begin
    perform school_directory.stage_import(v_run_id,'[{"emis_number":"900000001","name":"Fixture","level":"primary","province":"Limpopo","status":"OPEN"}]');
    raise exception 'Expected duplicate EMIS rejection';
  exception when unique_violation then null; end;

  -- Public roles cannot run imports or read import staging.
  if exists(select from pg_roles where rolname='anon') then
    if has_schema_privilege('anon','school_directory','usage') or
       has_function_privilege('anon','school_directory.finish_import(uuid,boolean,text)','execute') then
      raise exception 'Anonymous import access';
    end if;
  end if;
end
$test$;
rollback;
select 'School directory integration checks passed' as result;
