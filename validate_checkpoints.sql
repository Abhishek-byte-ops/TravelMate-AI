-- ===========================================================
-- TravelMate AI — checkpoint validation queries
--   psql "$DATABASE_URL" -f validate_checkpoints.sql
--
-- Where state lives:
--   checkpoints.channel_values (jsonb) -> plain strings/ints, inlined
--   checkpoint_blobs.blob (msgpack)    -> complex objects (messages, lists)
-- ===========================================================

-- 1. Threads, size and last activity. One row per conversation.
SELECT thread_id,
       COUNT(*) AS checkpoints,
       pg_size_pretty(SUM(pg_column_size(checkpoint))::bigint) AS meta_size,
       MAX(checkpoint ->> 'ts') AS last_write
FROM checkpoints
GROUP BY thread_id
ORDER BY checkpoints DESC;


-- 2. VALIDATES THE itinerary FIX.
--    Expect itinerary > 0 and itenary = 0. Before the fix, itinerary_agent
--    wrote to a channel the schema never declared, so it was silently
--    dropped and the final answer got an empty itinerary.
SELECT COUNT(*) FILTER (WHERE checkpoint -> 'channel_values' ? 'itinerary') AS has_itinerary,
       COUNT(*) FILTER (WHERE checkpoint -> 'channel_values' ? 'itenary')   AS has_old_misspelling,
       COUNT(*) AS total_checkpoints
FROM checkpoints;


-- 3. Size of each inlined channel on the newest checkpoint of every thread.
--    flight_results and itinerary should both carry real content.
SELECT thread_id,
       LENGTH(checkpoint -> 'channel_values' ->> 'user_query')     AS query_chars,
       LENGTH(checkpoint -> 'channel_values' ->> 'flight_results') AS flight_chars,
       LENGTH(checkpoint -> 'channel_values' ->> 'itinerary')      AS itinerary_chars,
       checkpoint -> 'channel_values' ->> 'llm_calls'              AS llm_calls
FROM (
  SELECT DISTINCT ON (thread_id) thread_id, checkpoint
  FROM checkpoints ORDER BY thread_id, checkpoint_id DESC
) newest
ORDER BY itinerary_chars DESC NULLS LAST;


-- 4. VALIDATES THE NOISE CLEANUP.
--    'messages' is the conversation history, msgpack in checkpoint_blobs.
--    Per turn it should now grow by about one question + one answer.
--    Threads created before the cleanup will look much larger.
SELECT thread_id,
       COUNT(*) AS history_versions,
       pg_size_pretty(MAX(LENGTH(blob))::bigint) AS newest_history
FROM checkpoint_blobs
WHERE channel = 'messages'
GROUP BY thread_id
ORDER BY MAX(LENGTH(blob)) DESC;


-- 5. Which channels are msgpack blobs rather than inlined jsonb.
--    hotel_results appears here because the MCP tool returns a list,
--    not the str the schema annotation claims.
SELECT channel,
       COUNT(*) AS versions,
       pg_size_pretty(SUM(LENGTH(blob))::bigint) AS total
FROM checkpoint_blobs
GROUP BY channel
ORDER BY SUM(LENGTH(blob)) DESC;


-- 6. Table sizes, to decide whether to prune.
SELECT relname AS table_name,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC;


-- 7. Prune a thread you no longer need. Blobs and writes cascade.
-- DELETE FROM checkpoints WHERE thread_id = 'test_user';
