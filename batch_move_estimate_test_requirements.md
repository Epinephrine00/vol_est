# batch_move_estimate.py 테스트 준비 가이드

`/move` 웹 UI에서 하던 이사 견적 추론을 여러 케이스에 대해 한 번에 돌리려면 `batch_move_estimate.py`를 실행하면 됩니다.
이 스크립트는 JSONL manifest 파일을 입력으로 받아, 각 줄에 적힌 이미지 묶음과 추가 정보를 `/move/api/estimate`와 같은 처리 로직으로 실행한 뒤 결과를 JSONL 파일로 저장합니다.

## 1. 준비해야 할 것

### Python 실행 환경

프로젝트 의존성이 설치된 Python 환경이 필요합니다.

필요 패키지는 `requirements.txt` 기준입니다.

```bash
pip install -r requirements.txt
```

주의: 현재 프로젝트의 `venv/bin/python`이 깨진 symlink를 가리킬 수 있습니다. 실행이 안 되면 새 가상환경을 만드는 것이 가장 빠릅니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Ollama 실행 상태

이미지가 있는 케이스를 평가하려면 Ollama 서버가 켜져 있어야 합니다.

```bash
ollama serve
```

기본 모델은 코드상 `gemma4:e2b`입니다. 모델이 로컬에 없으면 미리 받아야 합니다.

```bash
ollama pull gemma4:e2b
```

다른 모델로 돌리고 싶으면 실행 시 `--model`을 지정합니다.

```bash
python batch_move_estimate.py --input cases.jsonl --output results.jsonl --model gemma4:e2b
```

### 테스트 이미지 파일

각 테스트 케이스에 사용할 방/물건 사진 파일들이 필요합니다.

지원되는 일반 이미지 형식:

- JPEG
- PNG
- WebP

이미지 경로는 절대 경로도 되고, manifest 파일 위치 기준의 상대 경로도 됩니다.

## 2. 입력 JSONL 파일 준비

입력 파일은 JSONL 형식입니다. 한 줄이 하나의 테스트 케이스입니다.

예: `cases.jsonl`

```jsonl
{"case_id":"living_room_001","images":["images/living_001.jpg","images/living_002.jpg"],"extra":{"customer_name":"테스트 고객","distance_km":3,"items":[]},"user_prompt":"거실에 보이는 큰 가구 위주로 견적을 내줘"}
{"case_id":"manual_only_001","images":[],"extra":{"items":[{"name":"책상","volume_m3":0.4,"qty":1},{"name":"의자","volume_m3":0.08,"qty":2}],"distance_km":5},"user_prompt":""}
```

### 필드 설명

`case_id`

결과를 구분하기 위한 케이스 ID입니다. 생략하면 `line_1`, `line_2`처럼 줄 번호 기반 ID가 자동으로 붙습니다.

`images`

이미지 경로 배열입니다. 여러 장을 넣을 수 있습니다. 빈 배열이면 VLM 이미지 분석을 건너뛰고 `extra.items`에 적힌 수동 항목만 계산합니다.

`extra`

웹 UI의 `extra_data`와 같은 역할입니다. 이사 조건과 수동 품목을 넣습니다.

자주 쓸 수 있는 값:

```json
{
  "customer_name": "테스트 고객",
  "move_date": "2026-05-08",
  "origin_address": "출발지",
  "dest_address": "도착지",
  "distance_km": 3,
  "origin_floor": 2,
  "dest_floor": 5,
  "elevator_origin": true,
  "elevator_dest": true,
  "special_notes": "엘리베이터 사용 가능",
  "items": [
    {"name": "책상", "volume_m3": 0.4, "qty": 1, "fragile": false}
  ]
}
```

`extra_json_path`

`extra`를 별도 JSON 파일로 관리하고 싶을 때 사용합니다.

```jsonl
{"case_id":"room_002","images":["images/room_002.jpg"],"extra_json_path":"extras/room_002.json","user_prompt":"보이는 가전과 큰 가구만 포함"}
```

`extra_json_path`와 `extra`를 둘 다 쓰면, 먼저 `extra_json_path` 내용을 읽고 그 위에 `extra` 값이 덮어씁니다.

`user_prompt`

웹 UI에서 입력하던 자연어 요청과 같습니다. VLM에게 어떤 품목을 집중해서 볼지 알려줄 때 사용합니다.

## 3. CSV 보조 데이터와 함께 돌리는 경우

`quote_csv_path`를 넣으면 `batch_move_estimate.py`는 자동으로 CSV 보조 데이터 모드로 실행합니다.
이 모드는 웹의 `/move/api/estimate_from_rmc` 흐름과 같은 역할입니다.

```jsonl
{"case_id":"rmc_001","images":["images/room_001.jpg"],"quote_csv_path":"quotes/room_001_quote.csv","summary_json_path":"summaries/room_001_summary.json","viz_json_path":"summaries/room_001_viz.json","extra":{"distance_km":3,"items":[]},"user_prompt":"사진에 실제 보이는 품목만 포함"}
```

### CSV 보조 데이터 모드에서 추가로 쓰는 필드

`quote_csv_path`

필수입니다. 이 필드가 있으면 일반 `/move/api/estimate` 모드가 아니라 CSV 보조 데이터 모드로 처리됩니다.
CSV 안의 후보 품목을 읽고, 이미지가 있으면 VLM으로 사진에 실제 보이는 후보만 남긴 뒤 견적을 계산합니다.

`summary_json_path`

선택입니다. RMC나 외부 파이프라인이 만든 summary JSON이 있을 때 넣습니다.
없으면 `{}`로 처리됩니다.

`viz_json_path`

선택입니다. 시각화 또는 instance 요약 JSON이 있을 때 넣습니다.
없으면 결과의 `assist_data.visualization`이 비어 있을 수 있습니다.

`assist_mode`

선택입니다. 기본값은 `compare`입니다. 현재 허용 값은 `compare`, `merge`입니다.
웹 엔드포인트의 `mode` 값과 같은 역할을 하며, 지금 구현에서는 결과 문서의 `integration.mode`에 기록됩니다.

### quote CSV 형식

현재 파서는 첫 줄을 헤더로 보고, 두 번째 줄부터 다음 순서의 컬럼을 읽습니다.

```csv
instance_id,label,volume_m3,rate_per_m3,handling_multiplier,min_charge,line_subtotal,currency,note
1,책상,0.4,33000,1,0,13200,KRW,manual quote item
2,의자,0.08,33000,1,0,2640,KRW,manual quote item
```

필수로 의미 있게 들어가야 하는 값은 `instance_id`, `label`, `volume_m3`입니다.
`volume_m3`가 비어 있거나 0 이하이면 해당 줄은 최종 후보 품목에서 제외됩니다.

### CSV 보조 데이터 모드의 동작

이미지가 있는 경우:

- `quote_csv_path`의 품목들이 후보 목록이 됩니다.
- VLM이 이미지를 보고 후보 중 실제 보이는 품목만 `visible`로 남깁니다.
- 사진에서 새로 발견한 품목과 CSV에서 유지된 품목, `extra.items` 수동 품목을 병합합니다.
- 최종 `result.assist_data.quote_filter`에 어떤 품목이 유지/제외됐는지 기록됩니다.

이미지가 없는 경우:

- VLM 필터링을 건너뜁니다.
- `quote_csv_path`의 품목을 그대로 사용합니다.
- `result.assist_data.quote_filter.filter_applied`는 `false`가 됩니다.

### CSV 보조 데이터 모드 최소 예시

`cases_rmc.jsonl`

```jsonl
{"case_id":"quote_only","images":[],"quote_csv_path":"quotes/sample_quote.csv","extra":{"distance_km":3},"user_prompt":""}
```

`quotes/sample_quote.csv`

```csv
instance_id,label,volume_m3,rate_per_m3,handling_multiplier,min_charge,line_subtotal,currency,note
1,책상,0.4,33000,1,0,13200,KRW,test
```

실행:

```bash
python batch_move_estimate.py --input cases_rmc.jsonl --output results_rmc.jsonl
```

## 4. 실행 방법

기본 실행:

```bash
python batch_move_estimate.py --input cases.jsonl --output results.jsonl
```

가상환경을 만들었다면:

```bash
source .venv/bin/activate
python batch_move_estimate.py --input cases.jsonl --output results.jsonl
```

다른 모델 지정:

```bash
python batch_move_estimate.py --input cases.jsonl --output results.jsonl --model gemma4:e2b
```

## 5. 결과 파일 형식

출력도 JSONL입니다. 입력 한 줄당 결과 한 줄이 생성됩니다.
배치 결과는 평가용 요약만 저장하므로 웹 UI용 이미지 미리보기인 `previews_base64`는 포함하지 않습니다.

성공 예:

```json
{"case_id":"manual_only_001","ok":true,"result":{"volume_m3":0.56,"total_ex_tax":138480,"currency":"KRW","lines":[...],"quote":{"volume_m3":0.56,"total_ex_tax":138480}}}
```

실패 예:

```json
{"case_id":"living_room_001","ok":false,"status_code":502,"error":{"error":"Cannot connect to Ollama. Is the daemon running?"}}
```

중요하게 볼 값:

- `ok`: 케이스 성공 여부
- `result.volume_m3`: 계산된 총 부피
- `result.total_ex_tax`: 계산된 세전 견적 금액
- `result.currency`: 통화
- `result.lines`: 최종 품목 목록
- `result.photo_items`: 사진에서 VLM이 추출한 품목
- `result.vlm_summary_ko`: VLM 요약 문장
- `result.assist_data.quote_filter`: CSV 보조 데이터 모드에서 후보 품목 필터링 결과
- `result.quote`: 요금 계산 상세
- `error`: 실패한 경우 원인

## 6. 실제 평가를 위해 권장하는 폴더 구조

예시:

```text
eval_cases/
  cases.jsonl
  images/
    living_001.jpg
    living_002.jpg
    room_002.jpg
  extras/
    room_002.json
  quotes/
    room_002_quote.csv
  summaries/
    room_002_summary.json
  results.jsonl
```

`cases.jsonl`이 `eval_cases/` 안에 있으면 이미지 상대 경로는 `eval_cases/` 기준으로 해석됩니다.

```jsonl
{"case_id":"living_001","images":["images/living_001.jpg","images/living_002.jpg"],"extra":{"distance_km":3,"items":[]},"user_prompt":"큰 가구와 가전 위주"}
```

## 7. 이미지 없이 먼저 확인하는 최소 테스트

Ollama 없이 스크립트 입출력만 확인하려면 이미지 없는 케이스를 만들면 됩니다.

`cases_smoke.jsonl`

```jsonl
{"case_id":"manual_only","images":[],"extra":{"items":[{"name":"책상","volume_m3":0.4,"qty":1}],"distance_km":3},"user_prompt":""}
```

실행:

```bash
python batch_move_estimate.py --input cases_smoke.jsonl --output results_smoke.jsonl
```

이 경우 사진 분석은 건너뛰고 `extra.items`만으로 견적이 계산됩니다.

## 8. 자주 막히는 지점

### `ModuleNotFoundError: No module named 'flask'`

의존성이 설치되지 않은 Python으로 실행한 것입니다.

```bash
pip install -r requirements.txt
```

또는 가상환경을 활성화한 뒤 다시 실행합니다.

### `Cannot connect to Ollama`

이미지가 있는 케이스인데 Ollama 서버가 꺼져 있거나 `OLLAMA_HOST`가 다릅니다.

```bash
ollama serve
```

필요하면 `.env`에 `OLLAMA_HOST`를 맞춥니다.

### 이미지 경로 오류

`images`에 적은 상대 경로는 현재 터미널 위치가 아니라 `cases.jsonl` 파일 위치 기준입니다.

### `quote_csv_path`를 넣었는데 일반 모드처럼 보임

`quote_csv_path` 필드 이름이 정확한지 확인합니다. 이 필드가 있어야 CSV 보조 데이터 모드가 자동으로 선택됩니다.

### `quote_csv does not contain any billable line items`

CSV의 `volume_m3` 값이 비어 있거나 0 이하이면 후보 품목으로 쓰이지 않습니다. `instance_id`, `label`, `volume_m3` 컬럼이 올바른 순서로 들어갔는지 확인합니다.

### 결과는 나오지만 품목이 비어 있음

이미지에서 물건이 잘 보이지 않거나 VLM이 인식하지 못한 경우입니다. `user_prompt`를 더 구체적으로 쓰거나, 사진을 더 선명하게 준비해 다시 돌립니다.

## 9. 현재 버전의 한계

현재 `batch_move_estimate.py`는 일반 이미지 추론과 CSV 보조 데이터 기반 추론 결과를 여러 케이스에 대해 저장하는 용도입니다. 실제값과 비교해서 오차율, 평균 절대 오차, 정확도 요약 등을 계산하는 평가지표 기능은 아직 포함되어 있지 않습니다.

실제값 비교까지 하려면 추후 JSONL에 `ground_truth` 필드를 추가하고, 별도 metric 계산 스크립트 또는 `batch_move_estimate.py` 확장을 붙이면 됩니다.
