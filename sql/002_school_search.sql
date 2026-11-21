-- MIT: Skolo Saka NPC. Standalone public-directory search, extracted from the website.
create schema if not exists extensions;
-- School directory search at national scale (about 25,000 schools).
--
-- The trigram indexes on name, town and municipality (202609131500) could not be used by
-- search_school_directory: each search word was tested against all three columns inside a
-- NOT EXISTS over the words, with coalesce() around town and municipality, so every search read
-- the whole table. This adds one trigram index over the three columns together and rewrites the
-- filter so Postgres can use it:
--   * every search word must appear somewhere in "name town municipality" (same rule as before:
--     all words are required, each may match any of the three columns);
--   * the area filter keeps using the existing town and municipality indexes.
-- Results, ranking, paging and grants are unchanged. Safe to run more than once.

create extension if not exists pg_trgm with schema extensions;

create index if not exists schools_name_trgm_idx on public.schools using gin (name extensions.gin_trgm_ops);
create index if not exists schools_town_trgm_idx on public.schools using gin (town extensions.gin_trgm_ops);
create index if not exists schools_municipality_trgm_idx on public.schools using gin (municipality extensions.gin_trgm_ops);
create index if not exists schools_search_text_trgm_idx on public.schools using gin (
  (lower(name || ' ' || coalesce(town, '') || ' ' || coalesce(municipality, ''))) extensions.gin_trgm_ops
);

create or replace function public.search_school_directory(
  p_query text default '', p_area text default '',
  p_province text default null, p_level text default null,
  p_verified_only boolean default false, p_page integer default 0
) returns jsonb
language plpgsql stable security invoker set search_path = ''
-- A plan made for the actual words, every time. A reused generic plan cannot see whether a word was
-- given, so it would go back to reading the whole table.
set plan_cache_mode = force_custom_plan
as $$
declare
  term text := lower(trim(regexp_replace(regexp_replace(coalesce(p_query,''), '[,%()_*]', ' ', 'g'), '\s+', ' ', 'g')));
  area text := trim(regexp_replace(regexp_replace(coalesce(p_area,''), '[,%()_*]', ' ', 'g'), '\s+', ' ', 'g'));
  tokens text[];
  patterns text[];
  result jsonb;
begin
  if p_page is null or p_page < 0 or p_page > 10000 or length(term) > 200 or length(area) > 200 then
    raise exception 'Invalid search parameters' using errcode = '22023';
  end if;
  tokens := (string_to_array(term, ' '))[1:6];
  -- LIKE patterns for each word (the words were lower-cased above; the indexed text is lower-cased too).
  patterns := array(select '%' || t || '%' from unnest(tokens) t where t <> '');
  with matched as (
    select s.id,s.name,s.level,s.province,s.municipality,s.town,s.verified,
      case
        when term = '' then 0
        when lower(trim(s.name)) = term then 0
        when lower(s.name) like term || '%' then 1
        when lower(s.name) like '%' || term || '%' then 2
        when not exists (select 1 from unnest(tokens) t where s.name not ilike '%' || t || '%') then 3
        else 4
      end as relevance
    from public.schools s
    where (p_province is null or s.province = p_province)
      and (p_level is null or s.level::text = p_level)
      and (not coalesce(p_verified_only,false) or s.verified)
      and (area = '' or s.town ilike '%' || area || '%' or s.municipality ilike '%' || area || '%')
      -- The first word uses the combined index; the rest are checked on the rows it finds.
      and (term = '' or lower(s.name || ' ' || coalesce(s.town, '') || ' ' || coalesce(s.municipality, '')) like patterns[1])
      and (term = '' or lower(s.name || ' ' || coalesce(s.town, '') || ' ' || coalesce(s.municipality, '')) like all (patterns))
  ), paged as (
    select * from matched order by relevance,lower(name),id limit 30 offset p_page * 30
  )
  select jsonb_build_object(
    'total',(select count(*) from matched),
    'items',coalesce((select jsonb_agg(to_jsonb(p) - 'relevance' order by p.relevance,lower(p.name),p.id) from paged p),'[]'::jsonb)
  ) into result;
  return result;
end;
$$;

revoke all on function public.search_school_directory(text,text,text,text,boolean,integer) from public;
do $roles$
declare role_name text;
begin
  foreach role_name in array array['anon','authenticated','service_role'] loop
    if exists(select from pg_roles where rolname=role_name) then
      execute format('grant execute on function public.search_school_directory(text,text,text,text,boolean,integer) to %I',role_name);
    end if;
  end loop;
end
$roles$;
