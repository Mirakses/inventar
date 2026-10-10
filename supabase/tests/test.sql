\set ON_ERROR_STOP 1
\set QUIET 1
-- хелпер: виконати як користувач
create or replace function public._as(e text) returns void language plpgsql security definer as $$
begin
  perform set_config('request.jwt.claims', json_build_object('sub', (select id from auth.users where email=e))::text, false);
end $$;
grant execute on function public._as(text) to authenticated;

-- 1. перший користувач — адмін
insert into auth.users(email, raw_user_meta_data) values ('boss@x.ua', '{"full_name":"Богдан"}');
do $$ begin assert (select role from profiles where email='boss@x.ua')='admin', 'first is admin'; end $$;

-- 2. без запрошення — відмова
do $$ begin
  insert into auth.users(email) values ('stranger@x.ua');
  raise exception 'FAIL: stranger registered';
exception when insufficient_privilege then null; end $$;
\echo OK 1-2 перший адмін, сторонні не реєструються

-- 3. адмін створює категорії й запрошення
select _as('boss@x.ua'); set role authenticated;
insert into categories(id,name) values ('00000000-0000-0000-0000-0000000000e1','Електроніка'),('00000000-0000-0000-0000-0000000000a1','Інструменти');
insert into categories(id,name,parent_id) values ('00000000-0000-0000-0000-0000000000c1','Мікроконтролери','00000000-0000-0000-0000-0000000000e1');
insert into invitations(email,full_name,role,category_ids) values
 ('ed@x.ua','Редактор','editor','{00000000-0000-0000-0000-0000000000e1}'),
 ('store@x.ua','Комірник','storekeeper','{00000000-0000-0000-0000-0000000000a1}'),
 ('view@x.ua','Глядач','viewer','{00000000-0000-0000-0000-0000000000c1}');
insert into items(id,name,category_id,price) values
 ('11111111-0000-0000-0000-000000000001','ESP32','00000000-0000-0000-0000-0000000000c1',250),
 ('11111111-0000-0000-0000-000000000002','Паяльник','00000000-0000-0000-0000-0000000000a1',2400),
 ('11111111-0000-0000-0000-000000000003','Raspberry','00000000-0000-0000-0000-0000000000e1',3900);
reset role;
insert into auth.users(email) values ('ed@x.ua'),('store@x.ua'),('view@x.ua');
do $$ begin
  assert (select count(*) from invitations)=0, 'invitations consumed';
  assert (select role from profiles where email='store@x.ua')='storekeeper';
  assert (select count(*) from category_access)=3;
end $$;
\echo OK 3 запрошення працюють

-- 4. видимість
select _as('ed@x.ua'); set role authenticated;
do $$ begin
  assert (select count(*) from items)=2, 'editor sees electronics subtree (ESP32+Raspberry): '||(select count(*) from items);
  assert (select count(*) from categories)=2, 'editor sees Електроніка+Мікроконтролери';
  assert (select count(*) from invitations)=0;
end $$;
select _as('view@x.ua');
do $$ begin
  assert (select count(*) from items)=1, 'viewer sees only ESP32';
  assert (select count(*) from categories)=2, 'viewer sees MCU + parent Електроніка (for tree)';
end $$;
do $$ declare n int; begin
  -- предок видимий у дереві, але речі в ньому — ні
  assert not exists(select 1 from items where name='Raspberry'), 'viewer must not see parent items';
end $$;
select _as('store@x.ua');
do $$ begin assert (select count(*) from items)=1 and (select name from items)='Паяльник'; end $$;
\echo OK 4 видимість за категоріями

-- 5. редактор: може змінювати свої, не чужі; не може переносити в чужу категорію
select _as('ed@x.ua');
do $$ declare n int; begin
  update items set price=300 where name='ESP32'; get diagnostics n=row_count; assert n=1,'editor edit own';
  update items set price=1 where name='Паяльник'; get diagnostics n=row_count; assert n=0,'editor cannot touch tools';
  begin
    update items set category_id='00000000-0000-0000-0000-0000000000a1' where name='ESP32';
    raise exception 'FAIL: editor moved item to foreign category';
  exception when insufficient_privilege then null; end;
  insert into items(name,category_id) values ('Arduino','00000000-0000-0000-0000-0000000000c1');
  begin
    insert into items(name,category_id) values ('Молоток','00000000-0000-0000-0000-0000000000a1');
    raise exception 'FAIL: editor inserted into foreign category';
  exception when insufficient_privilege then null; end;
  begin
    insert into categories(name) values ('Хак'); raise exception 'FAIL: editor created category';
  exception when insufficient_privilege then null; end;
  begin
    update profiles set role='admin' where email='ed@x.ua';
    get diagnostics n=row_count; assert n=0, 'FAIL: editor promoted self';
  end;
end $$;
\echo OK 5 редактор

-- 6. комірник
select _as('store@x.ua');
do $$ declare n int; begin
  update items set status='Позичено', location='Петро', quantity=0 where name='Паяльник';
  get diagnostics n=row_count; assert n=1,'storekeeper status change';
  begin
    update items set price=1 where name='Паяльник'; raise exception 'FAIL: storekeeper changed price';
  exception when insufficient_privilege then null; end;
  begin
    update items set deleted=true where name='Паяльник'; raise exception 'FAIL: storekeeper deleted';
  exception when insufficient_privilege then null; end;
  begin
    insert into items(name,category_id) values ('X','00000000-0000-0000-0000-0000000000a1');
    raise exception 'FAIL: storekeeper inserted';
  exception when insufficient_privilege then null; end;
end $$;
-- upsert комірнику заборонено (INSERT-перевірка), тому клієнт робить звичайний PATCH
do $$ begin
  insert into items(id,name,status) values ('11111111-0000-0000-0000-000000000002','Паяльник','У ремонті')
    on conflict (id) do update set status=excluded.status;
  raise exception 'UNEXPECTED: upsert passed';
exception when insufficient_privilege then null; end $$;
update items set status='У ремонті' where id='11111111-0000-0000-0000-000000000002';
-- редактор: upsert існуючого рядка працює
select _as('ed@x.ua');
insert into items(id,name,category_id,price) values ('11111111-0000-0000-0000-000000000001','ESP32','00000000-0000-0000-0000-0000000000c1',260)
  on conflict (id) do update set price=excluded.price;
select _as('store@x.ua');
\echo OK 6 комірник

-- 7. глядач нічого не змінює
select _as('view@x.ua');
do $$ declare n int; begin
  update items set status='x'; get diagnostics n=row_count; assert n=0,'viewer cannot update';
  begin insert into items(name,category_id) values('x','00000000-0000-0000-0000-0000000000c1'); raise exception 'FAIL viewer insert';
  exception when insufficient_privilege then null; end;
end $$;
\echo OK 7 глядач

-- 8. фото та сховище
select _as('ed@x.ua');
insert into photos(item_id,path) values ('11111111-0000-0000-0000-000000000001','11111111-0000-0000-0000-000000000001/p1.jpg');
insert into storage.objects(bucket_id,name) values ('photos','11111111-0000-0000-0000-000000000001/p1.jpg');
do $$ begin
  begin insert into storage.objects(bucket_id,name) values ('photos','11111111-0000-0000-0000-000000000002/p.jpg');
        raise exception 'FAIL: editor uploaded to foreign item';
  exception when insufficient_privilege then null; end;
  begin insert into storage.objects(bucket_id,name) values ('photos','garbage/p.jpg');
        raise exception 'FAIL: bad path accepted';
  exception when insufficient_privilege then null; end;
end $$;
select _as('store@x.ua');
do $$ begin assert (select count(*) from storage.objects)=0,'storekeeper cannot see electronics photos';
             assert (select count(*) from photos)=0; end $$;
select _as('view@x.ua');
do $$ begin assert (select count(*) from storage.objects)=1,'viewer sees ESP32 photo'; end $$;
\echo OK 8 фото

-- 9. історія і touch
select _as('boss@x.ua');
do $$ begin
  assert (select count(*) from item_history where item_id='11111111-0000-0000-0000-000000000002' and action='updated')>=2;
  assert (select changes ? 'status' from item_history where item_id='11111111-0000-0000-0000-000000000002' order by id desc limit 1);
  assert (select updated_by from items where name='Паяльник')=(select id from profiles where email='store@x.ua');
end $$;
select _as('store@x.ua');
do $$ begin assert (select count(*) from item_history where item_id='11111111-0000-0000-0000-000000000001')=0,'no foreign history'; end $$;
\echo OK 9 історія

-- 10. деактивація
reset role;
update profiles set active=false where email='ed@x.ua';
select _as('ed@x.ua'); set role authenticated;
do $$ begin assert (select count(*) from items)=0,'deactivated sees nothing'; end $$;
-- анонім
reset role; select set_config('request.jwt.claims','',false); set role anon;
do $$ begin assert (select count(*) from items)=0; end $$;
\echo OK 10 деактивація й анонім
\echo ALL TESTS PASSED
