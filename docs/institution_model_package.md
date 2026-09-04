# 정보원 모델 제출자료 생성

보건의료정보부 요청자료를 원본 산출물에서 다시 생성하는 절차다. 대용량 CSV와 학습모델은 Git에 올리지 않고 로컬 제출 패키지에만 포함한다.

```powershell
.venv\Scripts\python.exe scripts\export_institution_model_package.py `
  --source-root C:\path\to\teamlex `
  --date 2026-09-04
```

생성 항목은 품목 표준화 결과, 주모델과 비교모델, 전체 모델 입력·출력, 평가지표, 관련 소스코드, 데이터사전과 SHA-256 목록이다. 전체 표는 UTF-8-SIG `csv.gz`로 생성하며 Excel 확인용 미리보기 CSV를 함께 제공한다.

`.pkl`은 신뢰된 환경에서만 불러와야 한다. 패키지 전달 전후에는 `06_명세/SHA256SUMS.csv`로 파일 무결성을 확인한다.
