-- 재고 0 원인을 status 에 반영 — 미운영·데이터누락을 판정 대상에서 제외 (ai#38)
--
-- 2026-07-28 운영 DB 에 적용한 SQL 이다. 판정 규칙과 EXCLUDED 값 선택 근거는
-- src/loading/compute_status_exclusion.py 의 docstring 을 참조한다.
--
--   NOT_OPERATED                 → 제외 (전 기간 정상출고 합이 0, 수요 자체가 없음)
--   DATA_MISSING & recent3m <= 0 → 제외 (정합성 위반 + 최근 수요 없음)
--   DATA_MISSING & recent3m >  0 → 유지 (발주 검토 대상)
--   TRUE_STOCKOUT                → 유지 (실제 결품)
--
-- 적용 결과: 63,952행 갱신, 발주권고 1,193,632개 제거.
--   CRITICAL 128,250 → 64,320 / EXCLUDED 63,952 신설
--   파생 알림 대상(CRITICAL+BELOW_ROP, DORMANT 제외) 221,227 → 101,787
--
-- 사용법:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f src/loading/apply_status_exclusion.sql
--   ※ 아래 \copy 경로는 data/handoff/zero_stock_reason.csv 기준(레포 루트에서 실행).

\set ON_ERROR_STOP on
BEGIN;

-- 1) handoff 적재 (recent3m 이 DB 에 없으므로 CSV 에서 가져온다)
DROP TABLE IF EXISTS _zsr_stage;
CREATE TABLE _zsr_stage (
    institution_id     TEXT,
    standard_code      TEXT,
    zero_stock_reason  TEXT,
    recent3m           DOUBLE PRECISION
);
\copy _zsr_stage FROM 'data/handoff/zero_stock_reason.csv' WITH (FORMAT csv, HEADER true)

-- 2) 되돌리기용 백업 — 변경 대상 행의 기존 status/발주권고를 그대로 보관
DROP TABLE IF EXISTS _bak_status_zsr_20260728;
CREATE TABLE _bak_status_zsr_20260728 AS
SELECT i.institution_id, i.standard_code, i.status, i.order_recommendation,
       now() AS backed_up_at
FROM inventory i
JOIN _zsr_stage s
  ON i.institution_id = s.institution_id
 AND i.standard_code  = s.standard_code
WHERE (s.zero_stock_reason = 'NOT_OPERATED'
       OR (s.zero_stock_reason = 'DATA_MISSING' AND s.recent3m <= 0))
  AND i.status IN ('CRITICAL', 'BELOW_ROP', 'WATCH');

-- 3) 반영
UPDATE inventory i
SET status = 'EXCLUDED', order_recommendation = 0
FROM _zsr_stage s
WHERE i.institution_id = s.institution_id
  AND i.standard_code  = s.standard_code
  AND (s.zero_stock_reason = 'NOT_OPERATED'
       OR (s.zero_stock_reason = 'DATA_MISSING' AND s.recent3m <= 0))
  AND i.status IN ('CRITICAL', 'BELOW_ROP', 'WATCH');

-- 4) 검증
SELECT status, count(*), sum(order_recommendation) AS orders
FROM inventory GROUP BY 1 ORDER BY 2 DESC;

SELECT zero_stock_reason, status, count(*), sum(order_recommendation) AS orders
FROM inventory WHERE zero_stock_reason IS NOT NULL
GROUP BY 1, 2 ORDER BY 1, 2;

DROP TABLE _zsr_stage;
COMMIT;

-- 되돌리기
--   UPDATE inventory i
--   SET status = b.status, order_recommendation = b.order_recommendation
--   FROM _bak_status_zsr_20260728 b
--   WHERE i.institution_id = b.institution_id AND i.standard_code = b.standard_code;
