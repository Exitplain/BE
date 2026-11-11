# 분리 이관 검증

- 원본: Jaehyeon-kr/dropspot_ `c52730f5413cfeffb8b28b82b3eaa033a8a99b58`.
- BE 기존 Django 파일 보존. Python 에이전트와 테스트는 BE, 화면·모의 쇼핑몰은 FE.
- 실행: `.venv/bin/python scripts/verify_harness.py --profile browser --timeout 900`.
- 최종 결과: **pass**, 46개 테스트, 실패 0개, 오류 0개, skip 0개.
- 로컬 verdict: `artifacts/harness/20261111T111359Z_2b36d198/verdict.json` (BE 기준, 실행 결과는 Git 제외).
- 코드·FE 자산 fingerprint: `ac3ea0b37926a91e2627d533690840abd3836f309c6dd940de2a8f4104bcaee1`.
- PC 1440px / 모바일 390px 화면 확인, Figma 배경·로고·아이콘 로컬 로드와 크기 확인.
- 4단계 준비 중 실행되지 않음, 모바일 최종 버튼 접근, 수정 후 입력 보존, PDF 다운로드, 세션 팝업 스크롤 복구 검증.

검증은 로컬 합성 사이트와 SDK mock 기준입니다. 실제 모델 API 호출·외부 고객 사이트·Skyvern 실서비스 연결·Django 운영 API 통합을 검증한 결과는 아닙니다.
