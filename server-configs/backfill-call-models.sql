-- Give calls made before migration 028 the models they ran on.
--
--   docker exec -i postgres psql -U aivoice -d aivoice < backfill-call-models.sql
--
-- Safe to re-run: it only fills columns that are still NULL, so a call that
-- recorded its own models is never overwritten by this guess.
--
--
-- WHAT THIS ASSUMES, AND WHAT THE ASSUMPTION COSTS
--
-- The campaign's CURRENT model is written onto its old calls. That is an
-- assertion, not a record - if a campaign changed model at some point, calls
-- from before the change get the wrong one.
--
-- Worth doing anyway, because the alternative was worse and the error is small:
--
--   TTS   tts-rt-v1 $0.73039/h vs tts-rt-v2 $0.74154/h   1.5% apart
--   STT   only ever stt-rt-v5 on this deployment
--   LLM   only ever gpt-4.1-mini
--
-- A 1.5% error on the TTS leg, against showing Rs 0.00 for it. Refusing to
-- choose looked like rigour and was just a wrong number wearing a straight
-- face.
--
-- New calls record their own models and do not rely on any of this.

UPDATE calls c
   SET llm_model_used = coalesce(c.llm_model_used, ac.llm_model),
       stt_model_used = coalesce(c.stt_model_used, ac.stt_model),
       tts_model_used = coalesce(c.tts_model_used, ac.tts_model)
  FROM agent_config ac
 WHERE ac.campaign_id = c.campaign_id
   AND (c.llm_model_used IS NULL
     OR c.stt_model_used IS NULL
     OR c.tts_model_used IS NULL);

-- Calls with no campaign, which cannot be resolved this way. Left alone: the
-- console will say their model is unknown, which is true.
SELECT count(*) AS calls_without_a_campaign
  FROM calls WHERE campaign_id IS NULL AND tts_model_used IS NULL;

SELECT coalesce(llm_model_used, '(unknown)') AS llm,
       coalesce(stt_model_used, '(unknown)') AS stt,
       coalesce(tts_model_used, '(unknown)') AS tts,
       count(*) AS calls
  FROM calls
 GROUP BY 1, 2, 3
 ORDER BY calls DESC;
