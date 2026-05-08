# vol_est

이미지 기반 물품/이사 견적 실험용 Flask 애플리케이션입니다.
Ollama vision model을 호출해 이미지 속 물건을 추정하고, 웹 UI 또는 배치 CLI로 견적 결과를 생성합니다.

## 주요 기능

- 단일 이미지 물체 감지 및 부피 지표 계산: `/api/analyze`
- 이사 견적 웹 UI: `/move`
- 여러 이미지 기반 이사 품목 추정: `/move/api/estimate`
- CSV 등 외부 보조 데이터 기반 이사 견적: `/move/api/estimate_from_rmc`
- JSONL manifest 기반 배치 실행: `batch_move_estimate.py`

## 설치

Python 가상환경을 만든 뒤 의존성을 설치합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

필요 패키지는 `requirements.txt`에 정의되어 있습니다.

```text
flask
ollama
pillow
python-dotenv
reportlab
```

## Ollama 준비

이미지를 분석하려면 Ollama 서버와 vision model이 필요합니다.

```bash
ollama serve
ollama pull gemma4:e2b
```

기본 모델은 `gemma4:e2b`입니다.

환경변수는 `.env`로 조정할 수 있습니다.

```bash
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e2b
OLLAMA_TIMEOUT=180
MAX_IMAGE_EDGE=1280
MOVE_MAX_IMAGES=30
```

## 웹 UI 실행

```bash
python app.py
```

기본 주소:

- 메인 단일 이미지 UI: `http://127.0.0.1:5000/`
- 이사 견적 UI: `http://127.0.0.1:5000/move/`
- Ollama 연결 확인: `http://127.0.0.1:5000/api/health`

## 배치 평가 실행

웹 UI 없이 여러 케이스를 돌리려면 `batch_move_estimate.py`를 사용합니다.

```bash
python batch_move_estimate.py --input cases.jsonl --output results.jsonl
```

입력은 JSONL입니다. 한 줄이 하나의 테스트 케이스입니다.

```jsonl
{"case_id":"living_room_001","images":["images/living_001.jpg"],"extra":{"distance_km":3,"items":[]},"user_prompt":"큰 가구와 가전 위주로 견적"}
{"case_id":"manual_only","images":[],"extra":{"items":[{"name":"책상","volume_m3":0.4,"qty":1}],"distance_km":3},"user_prompt":""}
```

상대 경로는 `cases.jsonl` 파일 위치 기준으로 해석됩니다.

출력도 JSONL이며, 각 줄은 다음 형태입니다.

```json
{"case_id":"living_room_001","ok":true,"result":{}}
```

실패한 케이스는 `ok:false`와 `error`를 포함합니다.

## CSV 보조 데이터 배치 실행

`quote_csv_path`가 있는 케이스는 자동으로 CSV 보조 데이터 모드로 처리됩니다.
이 모드는 `/move/api/estimate_from_rmc`와 같은 흐름을 사용합니다.

```jsonl
{"case_id":"rmc_001","images":["images/room.jpg"],"quote_csv_path":"quotes/room_quote.csv","summary_json_path":"summaries/summary.json","viz_json_path":"summaries/viz.json","extra":{"distance_km":3},"user_prompt":"사진에 실제 보이는 품목만 포함"}
```

`summary_json_path`, `viz_json_path`는 선택입니다.
`quote_csv_path`만 있으면 이미지가 없는 경우에도 CSV 품목을 그대로 사용해 견적을 계산할 수 있습니다.

CSV 예시:

```csv
instance_id,label,volume_m3,rate_per_m3,handling_multiplier,min_charge,line_subtotal,currency,note
1,책상,0.4,33000,1,0,13200,KRW,test
2,의자,0.08,33000,1,0,2640,KRW,test
```

더 자세한 배치 테스트 준비 방법은 `batch_move_estimate_test_requirements.md`를 참고하세요.

## 주요 파일

- `app.py`: Flask 앱 진입점과 단일 이미지 분석 API
- `move_estimate.py`: 이사 견적 웹 API, 배치에서 재사용하는 견적 로직
- `move_vlm.py`: 이사 품목 추정을 위한 Ollama VLM 호출 및 응답 파싱
- `vlm_client.py`: 단일 이미지 물체 감지용 VLM 호출 및 응답 파싱
- `image_prep.py`: 이미지 디코딩, EXIF 보정, 리사이즈, PNG 변환
- `volume_calc.py`: 단일 이미지 감지 결과의 부피 지표 계산
- `move_pdf.py`: 견적 PDF 생성
- `batch_move_estimate.py`: JSONL 기반 배치 실행 CLI
- `batch_move_estimate_test_requirements.md`: 배치 테스트 준비 가이드

## 개발 메모

`move_estimate.py`의 웹 API는 Flask request 처리만 담당하고, 핵심 견적 로직은 배치에서도 재사용할 수 있는 함수로 분리되어 있습니다.

주요 재사용 함수:

- `estimate_move_from_files()`: 일반 이미지 기반 이사 견적
- `estimate_move_from_rmc_files()`: CSV 보조 데이터 기반 이사 견적
- `build_move_estimate_doc()`: 이미 전처리된 이미지 바이트 기반 문서 생성
- `build_move_estimate_from_rmc_doc()`: 이미 전처리된 이미지와 CSV 데이터 기반 문서 생성

## 현재 한계

- 배치 CLI는 예측 결과를 저장하지만, 실제값과 비교한 오차율/정확도 metric 계산은 아직 포함하지 않습니다.
- VLM 결과는 모델과 프롬프트, 이미지 품질에 따라 달라질 수 있습니다.
- CSV 보조 데이터 모드에서 이미지가 없으면 후보 CSV 품목을 필터링하지 않고 그대로 사용합니다.
