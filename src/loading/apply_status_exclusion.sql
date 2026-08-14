-- 재고 0 원인을 status에 반영한다 (ai#38).
--
-- 기본 실행은 검증 후 ROLLBACK한다.
-- 실제 반영:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v apply=1 \
--     -f src/loading/apply_status_exclusion.sql

\set ON_ERROR_STOP on
\if :{?apply}
\else
  \set apply 0
\endif

BEGIN;

CREATE TEMP TABLE _zsr_stage (
    institution_id     TEXT,
    standard_code      TEXT,
    zero_stock_reason  TEXT,
    recent3m           DOUBLE PRECISION,
    policy_version     TEXT
) ON COMMIT DROP;

\copy _zsr_stage FROM 'data/handoff/zero_stock_reason.csv' WITH (FORMAT csv, HEADER true)

-- 키 중복이나 빈 키가 있으면 실행을 즉시 중단한다.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM _zsr_stage
        WHERE institution_id IS NULL OR standard_code IS NULL
    ) OR EXISTS (
        SELECT 1
        FROM _zsr_stage
        GROUP BY institution_id, standard_code
        HAVING count(*) > 1
    ) OR EXISTS (
        SELECT 1
        FROM _zsr_stage
        WHERE policy_version <> 'inventory-status-v1.1-physical'
           OR policy_version IS NULL
    ) THEN
        RAISE EXCEPTION 'invalid or duplicate zero-stock handoff keys';
    END IF;
END
$$;

SELECT count(*) AS proposed_rows,
       coalesce(sum(i.order_recommendation), 0) AS proposed_removed_orders
FROM inventory i
JOIN _zsr_stage s
  ON i.institution_id = s.institution_id
 AND i.standard_code = s.standard_code
WHERE (
        s.zero_stock_reason = 'NOT_OPERATED'
        OR (
            s.zero_stock_reason = 'DATA_MISSING'
            AND s.recent3m <= 0
        )
      )
  AND i.status IN ('CRITICAL', 'BELOW_ROP', 'WATCH');

\if :apply
  -- 누적 감사 백업이다. 재실행해도 과거 백업을 DROP하지 않는다.
  CREATE TABLE IF NOT EXISTS inventory_status_change_audit (
      institution_id TEXT NOT NULL,
      standard_code TEXT NOT NULL,
      previous_status TEXT NOT NULL,
      previous_order_recommendation INTEGER NOT NULL,
      change_reason TEXT NOT NULL,
      backed_up_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );

  INSERT INTO inventory_status_change_audit (
      institution_id,
      standard_code,
      previous_status,
      previous_order_recommendation,
      change_reason
  )
  SELECT i.institution_id,
         i.standard_code,
         i.status,
         i.order_recommendation,
         s.zero_stock_reason
  FROM inventory i
  JOIN _zsr_stage s
    ON i.institution_id = s.institution_id
   AND i.standard_code = s.standard_code
  WHERE (
          s.zero_stock_reason = 'NOT_OPERATED'
          OR (
              s.zero_stock_reason = 'DATA_MISSING'
              AND s.recent3m <= 0
          )
        )
    AND i.status IN ('CRITICAL', 'BELOW_ROP', 'WATCH');

  UPDATE inventory i
  SET status = 'EXCLUDED',
      order_recommendation = 0,
      updated_at = now()
  FROM _zsr_stage s
  WHERE i.institution_id = s.institution_id
    AND i.standard_code = s.standard_code
    AND (
          s.zero_stock_reason = 'NOT_OPERATED'
          OR (
              s.zero_stock_reason = 'DATA_MISSING'
              AND s.recent3m <= 0
          )
        )
    AND i.status IN ('CRITICAL', 'BELOW_ROP', 'WATCH');

  COMMIT;
  \echo 'status exclusion committed'
\else
  ROLLBACK;
  \echo 'dry-run only; pass -v apply=1 to commit'
\endif
