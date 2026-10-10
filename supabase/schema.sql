-- =====================================================================
--  Облік майна — серверна схема Supabase
--  Запустити ОДИН раз: Supabase → SQL Editor → New query → вставити → Run.
--  Повторний запуск безпечний (усе створюється «якщо ще немає»).
--
--  Ролі:
--    admin        — усе: користувачі, запрошення, категорії, речі
--    editor       — додає/змінює/видаляє речі у дозволених категоріях
--    storekeeper  — лише змінює стан, розташування й кількість (видача/повернення)
--    viewer       — лише перегляд
--  Доступ до категорії поширюється на всі її підкатегорії.
--  Реєстрація — лише за запрошенням. ПЕРШИЙ зареєстрований стає адміном.
-- =====================================================================

-- ---------------------------------------------------------------- таблиці
create table if not exists public.profiles (
    id             uuid primary key references auth.users (id) on delete cascade,
    email          text not null,
    full_name      text,
    role           text not null default 'viewer'
                   check (role in ('admin', 'editor', 'storekeeper', 'viewer')),
    all_categories boolean not null default false,
    active         boolean not null default true,
    created_at     timestamptz not null default now()
);

create table if not exists public.invitations (
    email          text primary key check (email = lower(email)),
    full_name      text,
    role           text not null default 'viewer'
                   check (role in ('admin', 'editor', 'storekeeper', 'viewer')),
    all_categories boolean not null default false,
    category_ids   uuid[] not null default '{}',
    invited_by     uuid references public.profiles (id) on delete set null,
    created_at     timestamptz not null default now()
);

create table if not exists public.categories (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    parent_id  uuid references public.categories (id) on delete set null,
    deleted    boolean not null default false,
    updated_at timestamptz not null default now(),
    updated_by uuid
);

create table if not exists public.category_access (
    user_id     uuid not null references public.profiles (id) on delete cascade,
    category_id uuid not null references public.categories (id) on delete cascade,
    primary key (user_id, category_id)
);

create table if not exists public.items (
    id            uuid primary key default gen_random_uuid(),
    name          text not null,
    category_id   uuid references public.categories (id) on delete set null,
    inventory_no  text,
    serial_no     text,
    location      text,
    quantity      integer not null default 1,
    price         numeric(14, 2),
    purchase_date date,
    status        text,
    notes         text,
    specs         jsonb not null default '[]'::jsonb,
    deleted       boolean not null default false,
    created_at    timestamptz not null default now(),
    created_by    uuid default auth.uid(),
    updated_at    timestamptz not null default now(),
    updated_by    uuid
);

create table if not exists public.photos (
    id         uuid primary key default gen_random_uuid(),
    item_id    uuid not null references public.items (id) on delete cascade,
    path       text not null,
    position   integer not null default 0,
    deleted    boolean not null default false,
    updated_at timestamptz not null default now(),
    updated_by uuid
);

create table if not exists public.item_history (
    id      bigserial primary key,
    item_id uuid not null references public.items (id) on delete cascade,
    user_id uuid,
    at      timestamptz not null default now(),
    action  text not null,
    changes jsonb
);

create index if not exists items_updated_idx      on public.items (updated_at);
create index if not exists items_category_idx     on public.items (category_id);
create index if not exists photos_item_idx        on public.photos (item_id);
create index if not exists photos_updated_idx     on public.photos (updated_at);
create index if not exists categories_updated_idx on public.categories (updated_at);
create index if not exists history_item_idx       on public.item_history (item_id, at desc);

-- ---------------------------------------------------------------- функції прав
create or replace function public.my_role()
returns text language sql stable security definer set search_path = public as $$
    select role from profiles where id = auth.uid() and active
$$;

create or replace function public.category_ancestors(cid uuid)
returns setof uuid language sql stable security definer set search_path = public as $$
    with recursive up (id, parent_id, depth) as (
        select id, parent_id, 0 from categories where id = cid
        union all
        select c.id, c.parent_id, up.depth + 1
        from categories c join up on c.id = up.parent_id
        where up.depth < 50
    )
    select id from up
$$;

-- бачить речі цієї категорії (доступ дано на неї або на будь-якого предка)
create or replace function public.can_see_category(cid uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select case
        when my_role() is null then false
        when my_role() = 'admin' then true
        when (select all_categories from profiles where id = auth.uid()) then true
        when cid is null then false
        else exists (
            select 1 from category_access a
            where a.user_id = auth.uid()
              and a.category_id in (select category_ancestors(cid)))
    end
$$;

-- категорія показується в дереві: є доступ, або вона — предок доступної
create or replace function public.category_visible(cid uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select can_see_category(cid) or (
        my_role() is not null and exists (
            select 1 from category_access a
            where a.user_id = auth.uid()
              and cid in (select category_ancestors(a.category_id))))
$$;

create or replace function public.can_edit_category(cid uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select coalesce(my_role() = 'admin' or (my_role() = 'editor' and can_see_category(cid)), false)
$$;

create or replace function public.can_see_item(iid uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select coalesce((select can_see_category(category_id) from items where id = iid), false)
$$;

create or replace function public.can_edit_item(iid uuid)
returns boolean language sql stable security definer set search_path = public as $$
    select coalesce((select can_edit_category(category_id) from items where id = iid), false)
$$;

-- шлях фото в сховищі: "<item_id>/<photo_id>.jpg"
create or replace function public.photo_item_id(path text)
returns uuid language sql immutable as $$
    select case when split_part(path, '/', 1) ~* '^[0-9a-f-]{36}$'
                then split_part(path, '/', 1)::uuid end
$$;

-- ---------------------------------------------------------------- тригери
create or replace function public.touch()
returns trigger language plpgsql as $$
begin
    new.updated_at := now();
    new.updated_by := auth.uid();
    return new;
end $$;

create or replace function public.guard_storekeeper()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    if my_role() = 'storekeeper' and
       (new.name, new.category_id, new.inventory_no, new.serial_no, new.price,
        new.purchase_date, new.notes, new.specs, new.deleted)
       is distinct from
       (old.name, old.category_id, old.inventory_no, old.serial_no, old.price,
        old.purchase_date, old.notes, old.specs, old.deleted)
    then
        raise exception 'Комірник може змінювати лише стан, розташування й кількість'
            using errcode = '42501';
    end if;
    return new;
end $$;

create or replace function public.log_item()
returns trigger language plpgsql security definer set search_path = public as $$
declare
    diff jsonb := '{}'::jsonb;
    o jsonb;
    n jsonb;
    k text;
begin
    if tg_op = 'INSERT' then
        insert into item_history (item_id, user_id, action) values (new.id, auth.uid(), 'created');
        return new;
    end if;
    o := to_jsonb(old);
    n := to_jsonb(new);
    for k in select jsonb_object_keys(n) loop
        if k not in ('updated_at', 'updated_by') and (o -> k) is distinct from (n -> k) then
            diff := diff || jsonb_build_object(k, jsonb_build_array(o -> k, n -> k));
        end if;
    end loop;
    if diff <> '{}'::jsonb then
        insert into item_history (item_id, user_id, action, changes)
        values (new.id, auth.uid(),
                case when new.deleted and not old.deleted then 'deleted' else 'updated' end,
                diff);
    end if;
    return new;
end $$;

-- новий користувач: перший → адмін; інші — лише за запрошенням
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
    inv invitations;
begin
    perform pg_advisory_xact_lock(4242);
    if not exists (select 1 from profiles) then
        insert into profiles (id, email, full_name, role, all_categories)
        values (new.id, lower(new.email), new.raw_user_meta_data ->> 'full_name', 'admin', true);
        return new;
    end if;

    select * into inv from invitations where email = lower(new.email);
    if inv.email is null then
        raise exception 'INVITE_REQUIRED: реєстрація лише за запрошенням'
            using errcode = '42501';
    end if;

    insert into profiles (id, email, full_name, role, all_categories)
    values (new.id, lower(new.email),
            coalesce(nullif(new.raw_user_meta_data ->> 'full_name', ''), inv.full_name),
            inv.role, inv.all_categories);
    insert into category_access (user_id, category_id)
    select new.id, c.id from categories c where c.id = any (inv.category_ids)
    on conflict do nothing;
    delete from invitations where email = inv.email;
    return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
    for each row execute function public.handle_new_user();

drop trigger if exists z_touch on public.categories;
create trigger z_touch before insert or update on public.categories
    for each row execute function public.touch();
drop trigger if exists z_touch on public.items;
create trigger z_touch before insert or update on public.items
    for each row execute function public.touch();
drop trigger if exists z_touch on public.photos;
create trigger z_touch before insert or update on public.photos
    for each row execute function public.touch();
drop trigger if exists a_guard on public.items;
create trigger a_guard before update on public.items
    for each row execute function public.guard_storekeeper();
drop trigger if exists log_item on public.items;
create trigger log_item after insert or update on public.items
    for each row execute function public.log_item();

-- ---------------------------------------------------------------- RLS
alter table public.profiles        enable row level security;
alter table public.invitations     enable row level security;
alter table public.categories      enable row level security;
alter table public.category_access enable row level security;
alter table public.items           enable row level security;
alter table public.photos          enable row level security;
alter table public.item_history    enable row level security;

-- профілі: колеги бачать одне одного; змінює лише адмін
drop policy if exists profiles_select on public.profiles;
create policy profiles_select on public.profiles for select to authenticated
    using (my_role() is not null);
drop policy if exists profiles_update on public.profiles;
create policy profiles_update on public.profiles for update to authenticated
    using (my_role() = 'admin') with check (my_role() = 'admin');

-- запрошення: лише адмін
drop policy if exists invitations_all on public.invitations;
create policy invitations_all on public.invitations for all to authenticated
    using (my_role() = 'admin') with check (my_role() = 'admin');

-- доступ до категорій: свої рядки бачить кожен, змінює адмін
drop policy if exists access_select on public.category_access;
create policy access_select on public.category_access for select to authenticated
    using (user_id = auth.uid() or my_role() = 'admin');
drop policy if exists access_write on public.category_access;
create policy access_write on public.category_access for all to authenticated
    using (my_role() = 'admin') with check (my_role() = 'admin');

-- категорії: видимі за доступом, змінює адмін
drop policy if exists categories_select on public.categories;
create policy categories_select on public.categories for select to authenticated
    using (category_visible(id));
drop policy if exists categories_insert on public.categories;
create policy categories_insert on public.categories for insert to authenticated
    with check (my_role() = 'admin');
drop policy if exists categories_update on public.categories;
create policy categories_update on public.categories for update to authenticated
    using (my_role() = 'admin') with check (my_role() = 'admin');

-- речі
drop policy if exists items_select on public.items;
create policy items_select on public.items for select to authenticated
    using (can_see_category(category_id));
drop policy if exists items_insert on public.items;
create policy items_insert on public.items for insert to authenticated
    with check (can_edit_category(category_id));
drop policy if exists items_update on public.items;
create policy items_update on public.items for update to authenticated
    using (can_edit_category(category_id)
           or (my_role() = 'storekeeper' and can_see_category(category_id)))
    with check (can_edit_category(category_id)
           or (my_role() = 'storekeeper' and can_see_category(category_id)));
drop policy if exists items_delete on public.items;
create policy items_delete on public.items for delete to authenticated
    using (my_role() = 'admin');

-- фото (записи)
drop policy if exists photos_select on public.photos;
create policy photos_select on public.photos for select to authenticated
    using (can_see_item(item_id));
drop policy if exists photos_insert on public.photos;
create policy photos_insert on public.photos for insert to authenticated
    with check (can_edit_item(item_id));
drop policy if exists photos_update on public.photos;
create policy photos_update on public.photos for update to authenticated
    using (can_edit_item(item_id)) with check (can_edit_item(item_id));

-- історія: лише читання (пише тригер)
drop policy if exists history_select on public.item_history;
create policy history_select on public.item_history for select to authenticated
    using (can_see_item(item_id));

-- ---------------------------------------------------------------- сховище фото
insert into storage.buckets (id, name, public)
values ('photos', 'photos', false)
on conflict (id) do nothing;

drop policy if exists inventar_photos_read on storage.objects;
create policy inventar_photos_read on storage.objects for select to authenticated
    using (bucket_id = 'photos' and public.can_see_item(public.photo_item_id(name)));
drop policy if exists inventar_photos_insert on storage.objects;
create policy inventar_photos_insert on storage.objects for insert to authenticated
    with check (bucket_id = 'photos' and public.can_edit_item(public.photo_item_id(name)));
drop policy if exists inventar_photos_update on storage.objects;
create policy inventar_photos_update on storage.objects for update to authenticated
    using (bucket_id = 'photos' and public.can_edit_item(public.photo_item_id(name)))
    with check (bucket_id = 'photos' and public.can_edit_item(public.photo_item_id(name)));
drop policy if exists inventar_photos_delete on storage.objects;
create policy inventar_photos_delete on storage.objects for delete to authenticated
    using (bucket_id = 'photos' and public.can_edit_item(public.photo_item_id(name)));
