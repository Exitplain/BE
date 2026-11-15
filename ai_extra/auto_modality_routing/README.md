# 모달리티 분류기 (간단 버전) — 보류셋 평가

화면 스크린샷이 **text / image / canvas / media_ui** 중 무엇이 주된 내용인지 분류해 라우팅하기 위한 분류기의 실험이다. 규칙 기반 기준선보다 나은지 확인하는 것이 목적이며, 이 폴더는 구현된 서비스가 아니라 실험 스크립트다.

## 설계
- 라벨은 합성 페이지의 **구성으로 정해진다** (DOM 규칙에서 만들지 않는다). 클래스마다 레이아웃 템플릿 3~4개.
- 학습에는 일부 템플릿만 쓰고, **학습에 없던 템플릿(unseen)** 을 따로 보류한다. 같은 템플릿의 새 샘플(seen)도 따로 평가한다.
- OOD(빈 화면·단색·거의 빈 페이지·반복 무늬)는 분류하지 않고 abstain해야 한다.
- 비교: 다수 클래스 기준선, 깊이 3 결정트리(규칙형 기준선), CatBoost. LightGBM은 이 머신의 Python에 없어 쓰지 않았다(설치하지 않음).
- 실행 Python: `/opt/anaconda3/bin/python` (3.8; numpy, PIL, sklearn, catboost). 렌더링은 로컬 Chrome headless, 임시 user-data-dir 사용, 네트워크 접근 없음(`file://`).

## 사전 등록한 기준 (실행 전에 작성, 결과를 보고 바꾸지 않는다)
| 항목 | 기준 |
|---|---|
| seen 보류 macro-F1 (CatBoost) | ≥ 0.90 |
| **unseen 템플릿 보류 macro-F1 (CatBoost)** | ≥ 0.80 |
| CatBoost vs 깊이 3 결정트리, unseen macro-F1 | CatBoost가 +0.02 이상 높아야 "도입 가치 있음" |
| OOD abstain 비율 (τ=0.6) | ≥ 0.80 |
| in-dist(seen) abstain 비율 (τ=0.6) | ≤ 0.15 |
| 같은 시드 재학습 시 예측 동일 | 100% |
| 학습·보류 이미지 해시 중복 | 0건 |

판정은 pass / fail / blocked로 기록하며, 기준 미달이면 그대로 fail로 보고한다.

## 결과 run1 (2026-11-15) — **fail**

데이터: train 165 / val 44 / test_seen 88 / test_unseen 60 / ood 30, 시드 11·22·33·44·55 (`gen_pages.py`, 렌더는 Chrome 버전에 따라 미세하게 달라질 수 있음). 증거: `results/run1_catboost_vs_tree.json`. 재현:
```
PY=/opt/anaconda3/bin/python
for s in "train 165 11" "val 44 22" "test_seen 88 33" "test_unseen 60 44" "ood 30 55"; do set -- $s; $PY gen_pages.py DATA --split $1 --n $2 --seed $3; $PY shoot.py DATA/$1; done
$PY train_eval.py DATA --out results/run1.json --tau 0.6 --min-seen-f1 0.90 --min-unseen-f1 0.80 --min-margin-over-tree 0.02 --min-ood-abstain 0.80 --max-seen-abstain 0.15
```

| 기준 (사전 등록) | 결과 | 판정 |
|---|---|---|
| seen macro-F1 ≥ 0.90 | 0.966 | pass |
| **unseen macro-F1 ≥ 0.80** | **0.256** | **FAIL** |
| CatBoost가 결정트리보다 unseen에서 +0.02 | −0.146 (트리 0.402) | **FAIL** |
| OOD abstain ≥ 0.80 | 0.933 | pass |
| seen abstain ≤ 0.15 | 0.034 | pass |
| 같은 시드 재학습 동일 | True | pass |
| 해시 중복 0건 | 0 | pass |

### 해석
- **새 레이아웃에서 일반화되지 않았다.** unseen 템플릿별 정확도(CatBoost): poster 1.00, dashboard 0.13, playlist 0.13, **chart 0.00**. canvas 차트 15장이 전부 text로, 색 카드 대시보드가 image로, 재생 목록이 text로 분류됐다.
- 원인은 모델 튜닝이 아니라 **라벨 정의**에 가깝다. 이 라벨은 "무슨 기술로 그려졌나"(canvas/video/img)인데, 스크린샷의 픽셀은 그것을 결정하지 않는다. 차트를 그린 canvas는 텍스트 있는 흰 페이지와, 재생 버튼이 붙은 목록은 텍스트 목록과 화면상 거의 같다.
- seen의 0.966은 **생성기의 흔적을 배운 결과일 가능성이 크다.** 중요도 상위가 `color_entropy`, `n_colors_1pct`, `hue_entropy`(합성 사진의 입자 노이즈·흐림과 평면 UI를 가르는 특징)다. 실제 페이지에서의 성능으로 읽으면 안 된다.
- 결정트리의 OOD abstain은 0.00이다(잎 확률이 1.0이라 abstain에 쓸 수 없음). CatBoost는 확률이 낮아 abstain이 동작했지만, OOD 30장 전부 argmax가 media_ui였다.
- unseen 템플릿은 4개×15장이라 분산이 크다. 다만 chart 0/15 같은 결과는 표본 문제로 보기 어렵다.

### 이 결과가 말하는 것
- **스크린샷만으로 하는 모달리티 분류는 이 설정에서 기준선보다 낫지 않았고 신뢰할 수 없다.** 라우팅의 1차 근거는 DOM·네트워크의 결정적 신호(`<audio>`/`<video>`/`<canvas>` 요소, 재생 이벤트, 미디어 요청)여야 하고, ML은 DOM을 못 읽을 때의 폴백으로도 abstain 중심으로만 의미가 있다.
- LightGBM은 이 머신에 없어 시험하지 않았다. 같은 특징·같은 분할에서 모델만 바꾸는 것으로는 위 원인이 바뀌지 않을 것으로 보지만, 확인한 것은 아니다.

### 하지 않은 것 / 한계
- 기준을 실행 후에 바꾸지 않았다. 추가 튜닝(특징 추가, 하이퍼파라미터)도 하지 않았다. 하면 unseen이 보류셋이 아니게 된다.
- 실제 웹페이지가 아닌 합성 페이지만 썼다. 합성 라벨의 순환성(라벨=생성 구성) 때문에 실제 정확도를 주장할 수 없다.
- 클래스 4개, 템플릿 15개, 한 사람이 설계한 생성기다.
