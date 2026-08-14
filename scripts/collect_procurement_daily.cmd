@echo off
REM 조달청 납품요구 일일 이어받기. data.go.kr 일 할당량 1,000회 제한 때문에
REM 18개월 전량을 한 번에 못 받는다. 자정 리셋 직후 실행해 하루치씩 진행한다.
REM 완료된 달은 data/processed/procurement_collection_progress.json 로 관리되므로
REM 몇 번을 돌려도 중복 수집되지 않는다.
cd /d C:\Users\user\TeamLex-ai
for /f "tokens=1,* delims==" %%a in ('findstr /b "DATA_GO_KR_SERVICE_KEY=" .env') do set DATA_GO_KR_SERVICE_KEY=%%b
.venv\Scripts\python.exe -X utf8 -m src.procurement.lead_time_collector --start 2024-01 --end 2025-06 >> outputs\procurement_collect.log 2>&1
