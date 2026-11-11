# 검증 체크리스트

체크 상태는 손으로 성공 표시하는 대신 최신 `verdict.json`의 `checks`에서 확인한다. 이 파일은 증거 대응표다.

| 확인 항목 | SPEC ID | 실제 테스트·증거 |
|---|---|---|
| 실제 브라우저 정상 과업·세션 입력·쿠키·저장소 분리 | EXEC-01 | `test_real_browser_success_and_isolation` |
| 잘못된 target이 기술 실패로 분리 | EXEC-02 | `test_invalid_target_is_failure_not_dropout` |
| 취소·시간 제한 구분 | EXEC-03 | `test_cancellation_and_time_budget` |
| 마지막 허용 행동 성공 판정 | EXEC-04 | `test_last_action_can_succeed` |
| ID 경로 조작·중복 패널 거부 | CONTRACT-01 | `test_rejects_path_traversal_and_duplicate_ids` |
| 문제·수정 버전의 필드 예산 규칙 | DIAG-01 | `test_problem_and_fixed` |
| 화면·스키마가 모델 SDK 요청에 전달 | MODEL-MOCK-01 | `test_model_adapter_passes_screenshot_and_validates_action` |
| 키 없음이 명시적 실패 | MODEL-AUTH-01 | `test_missing_auth_is_not_silently_replaced` |
| hover·select·check/uncheck·press·wait와 잘못된 행동 인자 차단 | ACTION-01 | `test_extended_form_actions` |
| 뒤로·앞으로·새로고침·양방향 스크롤 | ACTION-02 | `test_navigation_and_bidirectional_scroll` |
| 반찬로드 개선 경로 완료·강제 가입 회귀 | SHOP-01 | `test_storefront_fixed_flow_and_signup_regression` |
| 배송비 노출 시점·CTA 초기 화면 노출 조건 | SHOP-02 | `test_storefront_visible_fee_and_cta_conditions` |
| 모바일 주문·가로 넘침·완료 페이지 직접 접근 방지 | SHOP-03 | `test_storefront_mobile_layout_and_completion_guard` |
| SaaS에서 진단·화면 근거·변경 비교·실행 기록 복원 | SAAS-01 | `test_console_real_run_evidence_and_comparison` |
| SaaS 사이트·설정 저장과 모바일 레이아웃 | SAAS-02 | `test_console_project_settings_and_mobile` |
| 미허용 실행 대상·타 origin 요청 거부 | SAAS-03 | `test_console_rejects_unapproved_targets_and_foreign_origin` |
| 50개 타일·재생/일시정지·선택·필터와 실제 진단 비혼합 | SAAS-04 | `test_console_fifty_agent_replay_is_separate_from_real_runs` |
| 실제 모델 GUI 호출·정상 완료 | MODEL-LIVE-01 | model 프로필 `model_runs/**/summary.json`·스텝·화면 |

확장 테스트가 목록에 있다는 것만으로 통과를 의미하지 않는다. 현재 소스의 최신 gate 결과를 확인한다. 스크린샷은 별도 명령 없이 observation 단계에서 자동 수집한다.

모든 테스트의 통과·실패·skip은 `pytest.xml`, 출력은 `execution.log`에 남는다. 개별 테스트의 스텝 파일은 pytest 임시 경로일 수 있으므로 장기 증거가 필요하면 별도 CLI 실행 결과를 보존한다.

아래 항목은 현재 자동 gate 범위 밖이며, 통과했다고 표시하지 않는다.

- 실제 모델이 반찬로드 프론트의 전체 주문을 완료하는 E2E. 현재 model gate는 단순 `--demo normal` 과업이다.
- 일반 쇼핑몰에서의 문제별 Precision·Recall과 정상 경로 오탐률.
- 시각적 CTA·배송비의 자동 탐지와 사람 검수 일치도.
- 배포 간 신규·기존·해결 이슈 대응 정확도.
- 실제 고객 행동과의 일치도, 실제 전환율 예측.
- 공개 URL 실행 환경의 SSRF 방어·실결제 차단·운영 자원 격리.

- LIVE-01~04: Skyvern 브리지의 고정 대상·자격증명 경계·명시적 실패·증거 보존을 기본 browser 게이트에 포함한다.
- 설치 환경의 실제 영상/입력/세션 인계는 `scripts/verify_skyvern_live.py`의 별도 verdict로 확인한다.

- PERSONA-01~03: 입력 예산에 따른 차이·중복 실행 방지·고객 대상 주입 차단·근거 경로 비노출.
- 실제 대시보드는 최대 동시 3개, 전체 과업 선택 시 총 12개 독립 Skyvern 세션의 상태·스크린샷·행동 기록·선택한 라이브 화면을 표시한다. 종료 후에는 저장한 마지막 화면으로 전환한다.

- FRUST-01: 점수 항별 근거·중복 가산·재방문·초기 가설 표시.
- ALIGN-01~03: 사람 데이터 없는 경우 미검증/null, 기술 실패와 원인 미상 분모 처리, 중복·오래된 실행 입력 거부, 로컬 UI 저장 흐름.
- 자동 테스트의 사람 관측은 임시 경로의 synthetic fixture이며 고객 대시보드 실제 기록에 넣지 않는다.

- TASK-01~03: 여러 과업의 정상 대조, 서로 다른 실제 관측 중단 단계, 12개 세션 할당, 탐색/비용 신호의 가산·민감도 검증.
- 전체 패널에서 과업×페르소나 표와 카드, 선택한 세션의 과업 목표 및 근거가 연결되는지 실제 Skyvern 설치에서도 확인한다.

- REPORT-01~03: 완료 리포트·위치별 그룹·당시 근거·누락 표시·기술 실패 분리·이전 실행 조회·Markdown 다운로드.
- 새 페르소나 실행의 리포트를 기존 50개 UI 재생 수치나 예전 2명 실행의 JSON 내보내기와 혼합하지 않는다.

- VISUAL-01~02: 실제 요소 좌표와 캡처 정렬, 입력/배송비 bbox, 미확보 사유, 확대 뷰어, 생성 목업의 적용 전 구분.
- 이미지는 크게 보되 근거가 없는 요소에 박스를 만들지 않는다. 생성 목업을 실제 개선 효과의 증거로 사용하지 않는다.

- DESIGN-01: 상태 필터·실제 표시 건수·중단 목록 클릭·선택 세션의 관측 여정 연결.
- 디자인 검토는 `.taste-skill`의 방향 시트·전후 screenshot·inspect 결과와 별도로 기록하며 사용자 선호가 일반 스킬 규칙보다 우선한다.

- SETUP-01~03: 읽기 전용 기능 탐색, 확인된 과업만 실행, 최종 확인 전 실행 없음, 연령 고정관념으로 가중치를 바꾸지 않음.
- EXPLAIN-01~02: 카드 팝업의 근거 화면·질문 답변·저장·미관측 이유 표시·Escape 복귀. 규칙 답변을 LLM 추론이나 실제 고객 심리로 표현하지 않는다.

- PDF-01~02: 완료 실행을 선택해 PDF 다운로드, 한글/요약/근거/한계 포함, 미완료·잘못된 ID 거부, 동시 생성 제한, UI 버튼 복구.
