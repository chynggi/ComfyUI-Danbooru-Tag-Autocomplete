# ComfyUI Danbooru Tag Autocomplete

[English](README.en.md) | **한국어**

ComfyUI용 Danbooru 태그 자동완성 확장입니다. 태그 데이터는 매일 스스로 갱신되는 Hugging Face 태그
메타데이터를 기반으로 합니다. 프롬프트 입력란에 타이핑하면 일치하는 태그가 캐럿 아래에 나타나고,
`Tab`을 누르면 태그가 삽입됩니다.

- 설계: `docs/specs/2026-09-22-tag-autocomplete-design.md`
- 계획: `docs/plans/`
- 남은 작업: `docs/next-steps.md`

## 설치

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/chynggi/ComfyUI-Danbooru-Tag-Autocomplete
```

ComfyUI를 재시작합니다. 처음 실행하면 확장이 태그 데이터베이스(약 2.3 MB)를 ComfyUI 사용자 디렉터리의
`danbooru-tag-autocomplete/` 아래에 내려받고, 이후에는 오프라인에서도 그대로 재사용합니다. 설치할
런타임 의존성도, 빌드 단계도 없습니다. 다운로드가 차단되면 배너가 그 이유를 알려 주며, 프롬프트
입력란은 추천 없이 평소처럼 동작합니다.

## 설정

ComfyUI 설정의 `DanbooruTagAutocomplete` 그룹에 있습니다.

| 설정 | 기본값 | 설명 |
|---|---|---|
| `Enable tag autocomplete` | 켜짐 | 추천 기능 전체를 끕니다 |
| `Suggestion count` | 32 | 목록에 표시할 추천 개수이며, 한 번에 약 10개가 보입니다 |
| `Insert with Tab` | 켜짐 | 강조된 태그를 `Tab`으로 삽입합니다 |
| `Insert with Enter` | 꺼짐 | `Enter`로 삽입합니다. 끄면 `Enter`는 줄바꿈으로 남습니다 |
| `Insert spaces instead of underscores` | 꺼짐 | `blue_hair` 대신 `blue hair`로 씁니다 |
| `Show post counts` | 켜짐 | 각 태그의 게시물 수를 표시합니다 |
| `Category to show` | `all` | 목록을 한 카테고리로 제한합니다 |
| `Enable alongside another autocomplete` | 꺼짐 | 다른 자동완성 확장이 설치되어 있어도 켜진 상태를 유지합니다 |

## 태그 데이터 갱신 방식

이 저장소의 `data/latest.json`에는 현재 릴리스와 그 `sha256`이 기록되어 있습니다. 캐시된
데이터베이스가 없으면 확장은 이 포인터를 읽고, 포인터가 가리키는 릴리스에서 `tags.bin.gz`를
내려받아 해시를 검증한 뒤 ComfyUI 사용자 디렉터리에 캐시합니다. 예약된 워크플로가 매일 오전 3시(KST)에
업스트림으로부터 다시 빌드하고, 데이터가 바뀌었을 때만 새 `data-<version>` 릴리스를 게시한 다음,
그에 맞는 `data/latest.json`을 저장소에 커밋합니다. 따라서 새 태그 데이터를 게시하는 데 코드 업데이트가
필요하지 않습니다.

설치본은 이미 가진 데이터베이스를 계속 사용하며, 포인터를 스스로 다시 읽지 않습니다. 더 새로운 태그를
받으려면 ComfyUI 사용자 디렉터리의 `danbooru-tag-autocomplete/`를 삭제하고 페이지를 새로고침하세요.
그러면 현재 릴리스를 내려받습니다. 게시된 지 5분이 안 된 포인터는 `raw.githubusercontent.com`에서
아직 캐시된 값으로 제공될 수 있으니, 데이터가 오래돼 보이면 잠시 기다렸다가 다시 시도하세요.

데이터 출처는 태그 목록·카테고리·별칭을 위한 `hlibr/danbooru-tag-metadata-snapshot`과, 일별 게시물 수를
위한 `HDiffusion/historical-danbooru-tag-counts`입니다. 두 출처 모두 각 릴리스의 `metadata.json`에
리비전과 함께 기록됩니다.

## 프로필

프로필은 `profiles/` 아래의 YAML 파일로, 아티팩트에 무엇을 담을지 결정합니다.

```yaml
name: danbooru
threshold: 25            # 게시물 수가 이보다 적은 태그는 제외
exclude_categories: []   # 제외할 카테고리 번호: 0 일반, 1 작가, 3 저작권, 4 캐릭터, 5 메타
exclude_deprecated: true # 사용 중단된 태그는 제외하되 그 별칭은 유지
extra_sources: []        # 기본 출처 위에 병합할 출처 id
```

기본값은 `danbooru`입니다. Illustrious, NoobAI, Pony, WAI 프롬프트는 모두 Danbooru 태그 어휘로
작성되므로 프로필 하나로 모두 다룰 수 있습니다. 모델별 프로필은 자체 태그 출처를 가져오지 않는 한 기본값을
반복할 뿐입니다. 더 좁은 프로필(더 높은 임계값, 또는 메타 태그 제외 등)을 만들려면
`profiles/danbooru.yaml`을 복사해 수정한 뒤 `--profile profiles/<name>.yaml`로 빌드하세요.
`extra_sources`는 다른 booru를 위한 확장 지점입니다. 새 출처는 `build/sources/` 아래의 클래스로
작성하고 `build/fetch_upstream.py`에 등록합니다.

## 사용자 정의 태그

ComfyUI 사용자 디렉터리의 `danbooru-tag-autocomplete/` 아래에 `custom_tags.csv`를 두세요. 마지막
열이 비어 있는 행은 태그를 선언합니다. 마지막 열에 이름이 들어 있는 행은 해당 행의 이름을 그중 첫 번째
이름의 별칭으로 선언합니다. 예를 들어 `my_old,general,0,my_tag`는 `my_old`를 입력하면 `my_tag`를
추천한다는 뜻입니다. 같은 구조의 JSON 파일도 대신 사용할 수 있습니다. 사용자 정의 항목은 내려받은
데이터베이스보다 우선합니다.

## 태그 데이터베이스 직접 빌드하기

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest pyarrow pyyaml requests aiohttp
.venv/bin/python build/fetch_upstream.py --cache data/raw
.venv/bin/python build/build_database.py --profile profiles/danbooru.yaml --out generated
.venv/bin/python build/validate_database.py --artifact generated/tags.bin.gz --metadata generated/metadata.json
```

## 테스트

```bash
.venv/bin/python -m pytest -q                  # 지연 시간 게이트를 포함한 전체 테스트
.venv/bin/python -m pytest -m "not slow" -q    # 170만 태그 세트를 빌드하는 게이트는 건너뜀
.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s
```

게이트는 `generated/tags.bin.gz`가 있으면 실제 아티팩트를 측정하고, 여기에 더해 약 171만 개 태그로
이루어진 합성 세트 두 개로 구조적 비용을 스트레스 테스트합니다. 이는 실제로 배포되는 약 194,000개
태그의 대략 9배 규모입니다. 업데이트 워크플로가 테스트보다 빌드를 먼저 하는 이유가 여기에 있습니다.

## 개발용 오버라이드

| 변수 | 효과 |
|---|---|
| `DTA_LOCAL_ARTIFACT` | 다운로드 대신 이 `tags.bin.gz`(옆에 `metadata.json` 포함)를 사용합니다 |
| `DTA_LATEST_URL` | 저장소 대신 이 URL에서 포인터를 읽습니다 |
| `DTA_REPO_SLUG` | 커밋된 포인터를 읽을 저장소를 변경합니다 |

## 브라우저 체크리스트

브라우저 동작은 설계에 명시된 대로 수동으로 검증합니다. ComfyUI를 실행하고 `CLIPTextEncode` 노드를
추가한 다음, 아래 항목을 순서대로 확인하세요.

- 긍정 프롬프트에 `1girl, blue_h`를 입력하면 `blue_hair`, 그다음 `blue_hairband`로 시작하는 목록이
  나타난다.
- 목록에 카테고리 배지, 게시물 수, 그리고 별칭 일치 항목에는 `← alias`가 표시된다.
- `↑`/`↓`로 강조 항목이 이동하며 양 끝에서 순환하고, `PageUp`/`PageDown`은 10행씩 이동한다.
- `Tab`은 강조된 태그를 삽입하고 `Escape`는 목록을 닫는다.
- `Enter` 삽입이 꺼져 있으면(기본값) `Enter`는 줄바꿈을 추가하고 목록이 닫힌다. 새 줄은 빈 토큰으로
  시작하기 때문이다.
- `blue_hair`를 선택하면 `1girl, blue_hair, `가 입력되고 캐럿이 새 구분자 뒤에 놓인다.
- `1girl, blue_h, solo` 중간에서 선택하면 기존 쉼표와 공백은 그대로 유지된다.
- 줄바꿈되는 긴 프롬프트에서, 목록이 입력란의 왼쪽 위나 고정된 위치가 아니라 문단의 **첫** 줄과
  **마지막** 줄 모두에서 캐럿 위치에 나타난다.
- 높은 프롬프트 입력란 안에서 스크롤해 캐럿이 보이는 첫 줄에 있지 않아도, 목록은 여전히 캐럿 옆에
  나타난다.
- 페이지 자체를 스크롤한 뒤에도 목록은 여전히 캐럿 옆에 나타난다.
- 부정 프롬프트 입력란과 여러 줄 문자열 입력을 가진 다른 노드도 똑같이 동작한다.
- 두 개의 `CLIPTextEncode` 노드를 번갈아 사용해도 서로 간섭하지 않는다.
- 프롬프트 입력란이 있는 노드를 삭제하고 새 노드를 추가해도, 새 입력란에서 추천이 계속 나타난다.
- 일치하는 항목이 없는 평범한 문장을 입력하면 입력란은 이전과 완전히 같게 동작한다. 가로채기도, 키
  누락도, 깜빡임도 없다.
- Nodes 2.0(`Modern Node Design`)을 켠 상태에서도 같은 항목을 통과한다.
- `Suggestion count`를 올리면 더 많은 행이, 내리면 더 적은 행이 표시된다.
- `Insert spaces instead of underscores`를 켜면 `blue_hair`를 선택했을 때 `blue hair`가 입력된다.
- `Show post counts`를 끄면 행에 게시물 수가 표시되지 않는다.
- `Category to show`를 `character`로 설정하면 캐릭터 태그만 남고, `all`로 되돌리면 나머지도 돌아온다.
- 설정에서 `Enable tag autocomplete`를 끄면 목록이 전혀 나타나지 않는다.
- 다른 자동완성 확장이 활성화되어 있으면 이 확장은 꺼진 상태를 유지하고 그 이유를 로그에 남긴다.
  `Enable alongside another autocomplete`를 켜면 나타난다.
- `user/danbooru-tag-autocomplete/`를 삭제하고 페이지를 새로고침하면 새로 다운로드가 시작되고,
  완료되면 추천이 다시 나타난다.
- 다운로드가 차단된 상태(오프라인)에서는 닫을 수 있는 배너가 이유를 알려 주고, 입력은 계속 가능하다.
- `my_tag,general,0,`과 `my_old,general,0,my_tag`가 담긴
  `user/danbooru-tag-autocomplete/custom_tags.csv`가 있으면 `my_old`가 `my_tag`를 추천한다.

## 라이선스

MIT.

태그 데이터는 두 개의 업스트림 데이터셋으로부터 빌드됩니다. 각 릴리스의 `metadata.json`에는 사용한
모든 출처가 해당 출처의 리비전 및 데이터 날짜와 함께 기록됩니다.

| 출처 | 라이선스 | 비고 |
|---|---|---|
| `hlibr/danbooru-tag-metadata-snapshot` | MIT | Danbooru API로부터 생성됨 |
| `HDiffusion/historical-danbooru-tag-counts` | **불분명** | LICENSE 파일이 없으며, 데이터셋 카드에 `apache-2.0` 태그만 있음 |

두 번째 출처는 위험 요소로 간주하세요. 이 데이터셋이 사라지거나 철회되면
`build/fetch_upstream.py`의 `SOURCE_ORDER`에서 제거하면 되며, 그러면 빌드는 `hlibr`만으로 실행되고
아티팩트에는 출처가 하나 줄어든 상태로 기록됩니다. 출처 저장소를 환경 변수로 바꾸는 방법은 없으므로,
이는 설정 변경이 아니라 코드 변경입니다.

원본 데이터셋은 재배포하지 않습니다. 릴리스에는 빌드된 아티팩트만 포함되며, 내려받은 원본 파일은
gitignore된 캐시에만 남습니다. Danbooru 태그 어휘 자체는 저작권이 있는 콘텐츠가 아니라 사실 정보로
취급하며, 위키 텍스트는 사용하지 않습니다.
