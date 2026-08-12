-- =====================================================================
-- LegalX Shorts — Production Supabase PostgreSQL Migration
-- Execute this file in your Supabase SQL Editor once you have project credentials.
-- =====================================================================

CREATE TYPE shorts_content_type AS ENUM ('judgment_summary', 'rights_explainer');
CREATE TYPE shorts_category     AS ENUM ('cyber', 'traffic', 'posco', 'consumer', 'cheque_ni_act');

CREATE TABLE IF NOT EXISTS shorts_cards (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content_type        shorts_content_type NOT NULL DEFAULT 'judgment_summary',
  category            shorts_category NOT NULL,
  title               TEXT NOT NULL,
  question            TEXT NOT NULL,
  direct_answer       TEXT NOT NULL,
  explanation         TEXT NOT NULL,
  card_text           TEXT NOT NULL,
  case_reference      TEXT NOT NULL,
  suggested_questions JSONB NOT NULL DEFAULT '[]',
  source_url          TEXT,
  source_tid          TEXT UNIQUE,
  content_hash        TEXT NOT NULL,
  is_published        BOOLEAN NOT NULL DEFAULT false, -- Staged for Human Review by default!
  -- NULL until a reviewer approves the card. It was NOT NULL DEFAULT now(),
  -- which meant every unreviewed card carried a publication timestamp — the
  -- column the feed orders by — so "published_at" actually meant "created_at".
  published_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- A published card must have a publication time; an unpublished one must not.
  CONSTRAINT published_at_matches_state CHECK (
    (is_published AND published_at IS NOT NULL)
    OR (NOT is_published AND published_at IS NULL)
  )
);

-- Fast Query Indexes
-- These match the feed's actual ORDER BY (published_at DESC, id DESC) so
-- keyset pagination is an index scan rather than a sort.
CREATE INDEX IF NOT EXISTS idx_shorts_cards_category     ON shorts_cards(category, published_at DESC, id DESC)     WHERE is_published = true;
CREATE INDEX IF NOT EXISTS idx_shorts_cards_content_type ON shorts_cards(content_type, published_at DESC, id DESC) WHERE is_published = true;
CREATE INDEX IF NOT EXISTS idx_shorts_cards_source_tid   ON shorts_cards(source_tid)                     WHERE source_tid IS NOT NULL;
-- Reviewer queue lookup (list_staged_cards).
CREATE INDEX IF NOT EXISTS idx_shorts_cards_staged       ON shorts_cards(created_at DESC)                WHERE is_published = false;

-- Row Level Security (RLS)
--
-- IMPORTANT: RLS is bypassed entirely by the service_role key. These policies
-- only protect clients that connect with the anon key (or a user JWT). The
-- backend therefore uses SUPABASE_ANON_KEY for the public feed read path and
-- reserves SUPABASE_SERVICE_KEY for ingestion writes — see
-- app/lib/supabase_client.py. If every query runs as service_role, everything
-- below is decoration.
ALTER TABLE shorts_cards ENABLE ROW LEVEL SECURITY;
-- Also apply policies to the table owner, so a mistakenly-owner-authenticated
-- connection does not silently skip them.
ALTER TABLE shorts_cards FORCE ROW LEVEL SECURITY;

-- 1. Public Read Policy: App users can ONLY read cards where is_published = true
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'shorts_cards' AND policyname = 'Public published read policy'
  ) THEN
    CREATE POLICY "Public published read policy" ON shorts_cards FOR SELECT
      TO anon, authenticated
      USING (is_published = true);
  END IF;
END $$;

-- 2. Reviewer Authorization Policy: ONLY authenticated users with role = 'reviewer' or 'admin' can view & approve cards
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'shorts_cards' AND policyname = 'Reviewer read staged policy'
  ) THEN
    -- Reviewers additionally need to SELECT unpublished rows; the public
    -- policy above only exposes published ones, so without this a reviewer
    -- could never see the queue they are meant to approve.
    CREATE POLICY "Reviewer read staged policy" ON shorts_cards FOR SELECT
      TO authenticated
      USING (
        (auth.jwt() -> 'app_metadata' ->> 'role') IN ('reviewer', 'admin')
        OR (auth.jwt() -> 'user_metadata' ->> 'role') IN ('reviewer', 'admin')
      );
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE tablename = 'shorts_cards' AND policyname = 'Reviewer approve policy'
  ) THEN
    -- WITH CHECK was omitted. Postgres then reuses USING for the new row,
    -- which happens to be equivalent here, but stating it explicitly means the
    -- policy keeps working if USING is ever narrowed to specific rows.
    CREATE POLICY "Reviewer approve policy" ON shorts_cards FOR UPDATE
      TO authenticated
      USING (
        (auth.jwt() -> 'app_metadata' ->> 'role') IN ('reviewer', 'admin')
        OR (auth.jwt() -> 'user_metadata' ->> 'role') IN ('reviewer', 'admin')
      )
      WITH CHECK (
        (auth.jwt() -> 'app_metadata' ->> 'role') IN ('reviewer', 'admin')
        OR (auth.jwt() -> 'user_metadata' ->> 'role') IN ('reviewer', 'admin')
      );
  END IF;
END $$;

-- =====================================================================
-- MIGRATION for databases created before the review-gate fixes.
-- Safe to re-run. Skip entirely on a fresh install (the DDL above covers it).
-- =====================================================================

-- 1. published_at must be nullable so unreviewed cards carry no publish time.
ALTER TABLE shorts_cards ALTER COLUMN published_at DROP NOT NULL;
ALTER TABLE shorts_cards ALTER COLUMN published_at DROP DEFAULT;

-- 2. Clear the misleading timestamps already written to unreviewed rows.
UPDATE shorts_cards SET published_at = NULL WHERE is_published = false;

-- 3. Backfill any published row that somehow has no timestamp, then add the
--    constraint (added last so the data is already consistent).
UPDATE shorts_cards SET published_at = created_at
  WHERE is_published = true AND published_at IS NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'published_at_matches_state'
  ) THEN
    ALTER TABLE shorts_cards ADD CONSTRAINT published_at_matches_state CHECK (
      (is_published AND published_at IS NOT NULL)
      OR (NOT is_published AND published_at IS NULL)
    );
  END IF;
END $$;

-- 4. Replace the old indexes with the (published_at, id) keyset-friendly ones.
DROP INDEX IF EXISTS idx_shorts_cards_category;
DROP INDEX IF EXISTS idx_shorts_cards_content_type;
CREATE INDEX IF NOT EXISTS idx_shorts_cards_category     ON shorts_cards(category, published_at DESC, id DESC)     WHERE is_published = true;
CREATE INDEX IF NOT EXISTS idx_shorts_cards_content_type ON shorts_cards(content_type, published_at DESC, id DESC) WHERE is_published = true;
