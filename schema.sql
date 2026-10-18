-- Run in the Supabase SQL editor, also to upgrade an existing 10-vote study.
-- Streamlit calls the RPC with a server-only secret key (service_role).
begin;

create table if not exists public.pixel_batches (
    batch_id uuid primary key,
    participant_id uuid not null,
    batch_number integer not null check (batch_number > 0),
    dataset_version text not null check (length(dataset_version) = 64),
    sampler_version text not null,
    sampling_seed text not null,
    submitted_at timestamptz not null default now(),
    submission jsonb not null,
    unique (participant_id, batch_number),
    unique (batch_id, participant_id, batch_number)
);

create table if not exists public.pixel_votes (
    participant_id uuid not null,
    batch_id uuid not null,
    batch_number integer not null,
    comparison_number integer not null check (comparison_number > 0),
    batch_comparison_number integer not null check (batch_comparison_number between 1 and 15),
    dataset_version text not null check (length(dataset_version) = 64),
    sampler_version text not null,
    sampling_seed text not null,
    comparison_id text not null check (length(comparison_id) = 64),
    prompt_id text not null check (length(prompt_id) > 0),
    prompt text not null,
    target_resolution integer not null check (target_resolution > 0),
    left_method text not null check (left_method in ('SDPAP','AOP','APIP','FLUX','GPTImage','MYOS','PAP','PIA')),
    right_method text not null check (right_method in ('SDPAP','AOP','APIP','FLUX','GPTImage','MYOS','PAP','PIA')),
    left_image_id text not null,
    right_image_id text not null,
    left_path text not null,
    right_path text not null,
    left_sha256 text not null check (length(left_sha256) = 64),
    right_sha256 text not null check (length(right_sha256) = 64),
    selected_side text not null check (selected_side in ('left','right','tie')),
    winner text not null,
    answered_at timestamptz not null,
    submitted_at timestamptz not null default now(),
    pool_cycle integer not null check (pool_cycle > 0),
    is_repeat boolean not null,
    repeat_reason text not null,
    primary key (batch_id, batch_comparison_number),
    foreign key (batch_id, participant_id, batch_number)
        references public.pixel_batches (batch_id, participant_id, batch_number),
    unique (participant_id, comparison_number),
    unique (batch_id, comparison_id),
    check (comparison_number >= batch_comparison_number),
    check ((left_method = 'SDPAP') <> (right_method = 'SDPAP')),
    check (winner = case selected_side when 'left' then left_method when 'right' then right_method else 'tie' end),
    check ((is_repeat and pool_cycle > 1 and repeat_reason = 'pool_exhausted')
        or (not is_repeat and pool_cycle = 1 and repeat_reason = ''))
);

-- Preserve stored 10-vote batches while permitting new 15-vote submissions.
-- Cumulative numbering is now checked inside the serialized RPC, so a participant
-- can finish a legacy batch and continue under the new protocol.
alter table public.pixel_votes drop constraint if exists pixel_votes_batch_comparison_number_check;
alter table public.pixel_votes add constraint pixel_votes_batch_comparison_number_check
    check (batch_comparison_number between 1 and 15);
alter table public.pixel_votes drop constraint if exists pixel_votes_check;
alter table public.pixel_votes add constraint pixel_votes_check
    check (comparison_number >= batch_comparison_number);

create index if not exists pixel_votes_participant_pair_idx
    on public.pixel_votes (participant_id, comparison_id);

alter table public.pixel_batches enable row level security;
alter table public.pixel_votes enable row level security;
revoke all on public.pixel_batches, public.pixel_votes from public, anon, authenticated;
-- No participant can read the research tables through a browser API.
grant select, insert on public.pixel_batches, public.pixel_votes to service_role;

create or replace function public.submit_pixel_batch(p_batch jsonb, p_votes jsonb)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_batch_id uuid := (p_batch->>'batch_id')::uuid;
    v_participant_id uuid := (p_batch->>'participant_id')::uuid;
    v_batch_number integer := (p_batch->>'batch_number')::integer;
    v_payload jsonb := jsonb_build_object('p_batch', p_batch, 'p_votes', p_votes);
    v_existing jsonb;
    v_next integer;
    v_offset integer;
    v_size integer := case when p_batch->>'sampler_version' = 'balanced-reference-resolution-cycle-v3' then 15 else 10 end;
    v_vote jsonb;
    v_position bigint;
    v_was_seen boolean;
begin
    if jsonb_typeof(p_batch) is distinct from 'object'
       or jsonb_typeof(p_votes) is distinct from 'array' then
        raise exception 'Invalid batch payload';
    end if;
    if jsonb_array_length(p_votes) <> v_size or v_batch_id is null
       or (p_batch ? 'batch_size' and (p_batch->>'batch_size')::integer is distinct from v_size)
       or v_participant_id is null or v_batch_number is null or v_batch_number < 1 then
        raise exception 'A batch must contain every vote required by its protocol and valid identifiers';
    end if;

    -- Serialize submissions for one anonymous participant. Different participants
    -- remain independent; simultaneous retries cannot double insert a batch.
    perform pg_advisory_xact_lock(hashtextextended(v_participant_id::text, 0));
    select submission into v_existing from public.pixel_batches where batch_id = v_batch_id;
    if found then
        if v_existing = v_payload then
            return v_batch_id;
        end if;
        raise exception 'Batch ID already exists with a different payload';
    end if;

    select coalesce(max(batch_number), 0) + 1 into v_next
        from public.pixel_batches where participant_id = v_participant_id;
    if v_next <> v_batch_number then
        raise exception 'Batch number is not the next submission for this participant';
    end if;
    select coalesce(max(comparison_number), 0) into v_offset
        from public.pixel_votes where participant_id = v_participant_id;

    insert into public.pixel_batches
        (batch_id, participant_id, batch_number, dataset_version, sampler_version, sampling_seed, submission)
    values (v_batch_id, v_participant_id, v_batch_number, p_batch->>'dataset_version',
            p_batch->>'sampler_version', p_batch->>'sampling_seed', v_payload);

    for v_vote, v_position in
        select value, ordinality from jsonb_array_elements(p_votes) with ordinality
    loop
        if (v_vote->>'participant_id')::uuid is distinct from v_participant_id
           or (v_vote->>'batch_id')::uuid is distinct from v_batch_id
           or (v_vote->>'batch_number')::integer is distinct from v_batch_number
           or (v_vote->>'batch_comparison_number')::bigint is distinct from v_position
           or (v_vote->>'comparison_number')::bigint is distinct from (v_offset::bigint + v_position) then
            raise exception 'Vote identifiers or numbering do not match the batch';
        end if;
        if v_size = 15 and (
            (v_vote->>'target_resolution')::integer is distinct from
                (case (v_position - 1) % 3 when 0 then 48 when 1 then 64 else 128 end)
            or v_vote->>'selected_side' not in ('left', 'right')) then
            raise exception 'New batches require 48, 64, 128 in order and a winning image';
        end if;
        select exists (select 1 from public.pixel_votes
            where participant_id = v_participant_id and comparison_id = v_vote->>'comparison_id')
            into v_was_seen;
        if (v_vote->>'is_repeat')::boolean is distinct from v_was_seen then
            raise exception 'Incorrect repeat metadata';
        end if;

        insert into public.pixel_votes (
            participant_id, batch_id, batch_number, comparison_number, batch_comparison_number,
            dataset_version, sampler_version, sampling_seed,
            comparison_id, prompt_id, prompt, target_resolution, left_method, right_method,
            left_image_id, right_image_id, left_path, right_path, left_sha256, right_sha256,
            selected_side, winner, answered_at, pool_cycle, is_repeat, repeat_reason
        ) values (
            v_participant_id, v_batch_id, v_batch_number,
            (v_vote->>'comparison_number')::integer, v_position::integer,
            p_batch->>'dataset_version', p_batch->>'sampler_version', p_batch->>'sampling_seed',
            v_vote->>'comparison_id', v_vote->>'prompt_id', v_vote->>'prompt',
            (v_vote->>'target_resolution')::integer, v_vote->>'left_method', v_vote->>'right_method',
            v_vote->>'left_image_id', v_vote->>'right_image_id', v_vote->>'left_path', v_vote->>'right_path',
            v_vote->>'left_sha256', v_vote->>'right_sha256', v_vote->>'selected_side', v_vote->>'winner',
            (v_vote->>'answered_at')::timestamptz, (v_vote->>'pool_cycle')::integer,
            (v_vote->>'is_repeat')::boolean, v_vote->>'repeat_reason'
        );
    end loop;
    return v_batch_id;
end;
$$;

revoke all on function public.submit_pixel_batch(jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.submit_pixel_batch(jsonb, jsonb) to service_role;
notify pgrst, 'reload schema';
commit;
