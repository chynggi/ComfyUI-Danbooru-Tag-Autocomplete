# ComfyUI Danbooru Tag Autocomplete — 설계 스펙

- 날짜: 2026-09-22
- 상태: 승인됨 (구현 계획 작성 대기)
- 프로젝트 라이선스: MIT

## 0. 확정된 결정

| 항목 | 결정 |
|---|---|
| 주 목적 | ComfyUI의 multiline STRING textarea(= CLIPTextEncode positive/negative 프롬프트 필드)에서 타이핑 시 Danbooru 태그 suggestion |
| upstream | HF 하이브리드: `hlibr/danbooru-tag-metadata-snapshot`(전체 태그 + deprecated + alias status, MIT) + `HDiffusion/historical-danbooru-tag-counts`(매일 갱신 count/alias) |
| 검색 위치 | 프론트엔드 단일 flat 인덱스 (binary search). 키입력당 서버 요청 0회 |
| 배포 | 런타임 아티팩트를 GitHub Release asset으로 배포, `data-*` 태그로 코드 릴리스와 분리 |
| 노드 | `Danbooru Tag Search` (검색 → STRING 출력) 1개 |
| custom tag DB | 런타임 오버레이 (사용자 로컬). 빌드 산출물에 포함하지 않는다 |
| 캐시 위치 | ComfyUI user 디렉터리 `user/danbooru-tag-autocomplete/` |
| repo slug | `chynggi/ComfyUI-Danbooru-Tag-Autocomplete` (env `DTA_REPO_SLUG`로 재정의 가능) |

## 1. 목표

지속적으로 최신화되는 Danbooru metadata를 안정적으로 공급받아, ComfyUI에서 빠르게 검색할 수 있는 **독립적인 tag database + autocomplete 시스템**을 만든다.

세부 목표:

1. HF의 최신 Danbooru tag dataset을 upstream으로 사용한다.
2. upstream raw 데이터와 ComfyUI 런타임 데이터를 명확히 분리한다.
3. tag / alias / category / deprecated / post_count metadata를 활용한다.
4. Illustrious / NoobAI / WAI 등 Danbooru 계열 모델 프롬프트 작성에 유용하게 한다.
5. 최신 ComfyUI 프론트엔드(frontend 1.53.6, Nodes 2.0 포함)와 호환한다.
6. 데이터 갱신을 코드 변경과 독립적으로 수행한다.
7. 다른 booru와 사용자 정의 tag DB를 추가할 수 있는 구조를 갖는다.

## 2. 비목표 (YAGNI)

- **fuzzy/오타 교정**: prefix + alias prefix + exact로 충분하다. 확장 지점만 남긴다.
- **중간 단어/부분 일치 검색**: 정렬 배열 기반 binary search로는 O(N) 스캔이 필요하다. 비목표로 두고, 필요한 경우 `words` 기반 보조 인덱스를 후속으로 추가한다.
- **co-occurrence / 관련 태그 추천**: Autocomplete-Plus가 103MB co-occurrence CSV를 무조건 로드해 탭 메모리 ~600MB를 만든 실패 사례가 있다. 구현하지 않는다.
- **wiki 본문 표시**: 사용하는 upstream에 wiki 필드가 없다.
- **e621 / gelbooru 등 다른 booru 실제 구현**: `SourceAdapter` 구조로 열어두기만 한다.
- **서버측 검색 API**: 프론트 단일 인덱스로 결정했다.
- **모델별 태그 지원 여부 추론**: 학습 데이터 기반 filtering은 하지 않는다. profile은 filtering과 추가 tag source 지정으로만 쓴다.

## 3. 조사 근거

### 3.1 ComfyUI 프론트엔드 (실측)

- 실행 환경: `ComfyUI/ComfyUI/.venv`, ComfyUI **0.37.0**, frontend **1.53.6**.
- 정적 웹 확장: 노드 모듈의 `WEB_DIRECTORY` 속성이 제어한다. `server.py`가 `/extensions/<모듈명>/*.js`를 자동 로드한다 (`nodes.py:2289-2292`, `server.py:356-368`, `server.py:1245-1247`). `.js`만 자동 로드되며 `.css`는 수동 로드다.
- **공식 autocomplete API는 없다.** 코어 Python 전체에 `autocomplete` 참조 0건이며, 프론트 번들에는 노드 검색창 / PrimeVue / Manager 검색용만 존재한다. 따라서 `ComfyWidgets.STRING` 후킹이 사실상 유일한 실용 경로다.
- `CLIPTextEncode`의 프롬프트 필드는 `"text": (IO.STRING, {"multiline": True, "dynamicPrompts": True, ...})` (`nodes.py:56,61`). multiline STRING은 DOM 위젯(`customtext`)이며 실제 `<textarea class="comfy-multiline-input">`가 `widget.element`에 있다.
- `widget.inputEl`은 deprecated 별칭이다. frontend 1.40.2에서 이것이 `undefined`가 되어 pysssss autocomplete가 깨진 이슈(#8852)가 있었다. 신규 코드는 `widget.element`를 쓴다.
- **Nodes 2.0에서도 DOM 위젯은 실제 DOM으로 마운트된다**(`WidgetDOM.vue`가 `widget.element`를 마운트). DOM textarea 후킹은 두 렌더러 모두에서 성립할 가능성이 높다(실기 검증 필요). 반대로 캔버스 `onDrawForeground` 기반은 Nodes 2.0에서 깨진다.
- 확장 `name`은 전역 유일이 강제되며 중복 시 throw한다. 고유 namespace가 필요하다.
- 노드 로더는 `<노드폴더>/__init__.py`를 패키지(submodule_search_locations 포함)로 exec하므로 상대 import가 가능하다 (`nodes.py:2255-2292`).
- `folder_paths.get_user_directory()`가 존재한다 (`folder_paths.py:146`).

### 3.2 기존 autocomplete 구현 비교

| 프로젝트 | 검색 위치 | 데이터 | 핵심 교훈 |
|---|---|---|---|
| ComfyUI-Autocomplete-Plus (MIT) | 프론트 FlexSearch 전량 | HF CSV 자동 다운로드 | co-occurrence 103MB 강제 로드 → 탭 600MB. 명확한 안티패턴 |
| ComfyUI-TagForge (MIT) | 백엔드 SQLite | 34MB CSV 동봉 | 서버 SQLite + 상위 N개 전송은 메모리/정렬을 동시에 만족. repo 비대가 단점 |
| comfy-ex-tagcomplete (MIT) | 백엔드 SQLite | A1111 CSV | Vue/Nodes 2.0 대응을 위해 MutationObserver를 추가 |
| pysssss Custom-Scripts (MIT) | 프론트 O(N) 선형 | 사용자 파일 | count 정렬 없음, Nodes 2.0에서 미작동(#517 open) |
| A1111 tagcomplete (MIT) | 프론트 정규식 | CSV 동봉 | 태그리스트 2024-12-22 정체, fuzzy 없음 |
| comfyui-suggest-dt | 프론트 O(N) | A1111 raw 다운로드 | **라이선스 없음 → 코드 재사용 금지**. 전역 MutationObserver 아이디어는 참고 |

공통 정답: alias는 검색 필드로 쓰되 **삽입은 canonical로 치환**, category는 배지로 표시, deprecated는 build 단계에서 처리.

### 3.3 upstream dataset 검증 (HF API + 실제 파일 파싱)

`deepghs/site_tags`는 "지속 업데이트" 전제가 틀렸다.

- HF API `lastModified`는 2025-11-17이지만 이는 README 자동 갱신이며, **데이터 커밋은 2025-09-25에서 중단**됐다.
- 정량 검증: `1girl` post_count = 6,947,596 (site_tags) vs 8,447,988 (2026-09-22 라이브 Danbooru) → 약 18% 낡음.
- 18개 사이트를 한 스키마로 담고 있어 다중 booru 템플릿으로는 가치가 있다. 본 프로젝트의 소스로는 쓰지 않는다.

실제 사용할 소스(직접 확인):

- `hlibr/danbooru-tag-metadata-snapshot` — 2026-04-08, MIT, `metadata.json`에 `row_count_tags: 1711015`, `row_count_tag_aliases: 59259`, `row_count_tag_implications: 49334`, `danbooru_base: https://danbooru.donmai.us`, `sources: ["danbooru tags API", "danbooru tag_aliases API", "danbooru tag_implications API"]`.
  - `tags.parquet` (59MB): `id, name, category, post_count, is_deprecated, words, source`
  - `tag_aliases.parquet` (2MB): `id, antecedent_name, consequent_name, status, reason, source`
  - `tag_implications.parquet` (1.2MB): `id, antecedent_name, consequent_name, status, reason, source`
  - `status` ∈ `active | retired | deleted`
  - `category` ∈ `0 general | 1 artist | 3 copyright | 4 character | 5 meta`
  - 주의: `metadata.json`의 `dataset_repo_id`는 `baton4ik/danbooru-tag-metadata-snapshot`로 적혀 있다(개명 전 이름). 실제 repo id는 `hlibr/...`이다.
  - 주의: LFS 파일이므로 `resolve/main/...`로 받아야 한다. `raw/main/...`은 포인터 텍스트를 반환한다.
- `HDiffusion/historical-danbooru-tag-counts` — 매일 자동, 파일명 `danbooru-YYYY-MM-DD.csv`(263개), **헤더 없음**, 컬럼 `tag,category,count,"alias1,alias2,..."`.
  - 2026-09-22 파일 확인: 125,215행 / 3,470,355 bytes, 첫 행 `1girl,0,8446417,"sole_female,1girls"` → 라이브 Danbooru 8,448,988과 하루 차이.
  - `count >= 50`만 포함하며 deprecated / id / timestamp / wiki가 없다.
  - 라이선스 불명확: LICENSE 파일이 없고 dataset card 태그만 `apache-2.0`이다. → 리스크로 취급한다(§14).

Danbooru 공식 API는 ground truth이지만 사용하지 않는다.

- Cloudflare가 TLS 핸드셰이크를 채점해 일반 `curl`은 차단된다(`SSLError 35`). 우회에는 브라우저 impersonation이 필요하다.
- rate limit은 전역 읽기 10 req/s, 장시간 세션 ~1 req/s 권장.
- ToS는 "제한을 우회하는 bot/script"를 금지한다. 따라서 browser impersonation 기반 우회는 공개 배포 프로젝트에 ToS 리스크가 있다.
- 참고: ToS는 tags를 "factual information"으로 규정해 데이터 자체의 저장·재배포 근거는 있다.

## 4. 아키텍처

```
HF upstream (hlibr + HDiffusion)
        │  build/  (GitHub Actions, 수동 실행 가능)
        ▼
 fetch → normalize → merge → alias 정규화 → profile filter → validate → artifact
        │
        ▼
 GitHub Release asset (data-*)  +  data/latest.json (repo에 커밋)
        │  런타임 (ComfyUI custom node)
        ▼
 user/danbooru-tag-autocomplete/{tags.bin.gz, metadata.json, custom_tags.csv}  ← 캐시
        │
        ├── GET /danbooru-tag-autocomplete/db      → 브라우저로 아티팩트 전송
        ├── GET /danbooru-tag-autocomplete/custom  → 사용자 custom tag JSON
        ├── GET /danbooru-tag-autocomplete/status  → 상태/data_version
        └── nodes.py (Danbooru Tag Search)          → Python에서 동일 인덱스 검색
                    │
                    ▼
        web/ 프론트엔드: flat 인덱스 binary search + custom 오버레이 + DOM 드롭다운
```

**불변식: upstream raw dataset을 런타임에서 읽지 않는다.** 런타임은 빌드된 아티팩트와 사용자 custom 파일만 다룬다.

## 5. 저장소 레이아웃

```
ComfyUI-Danbooru-Tag-Autocomplete/
├── __init__.py        # WEB_DIRECTORY, 라우트 등록, NODE_CLASS_MAPPINGS
├── nodes.py           # Danbooru Tag Search
├── artifact.py        # .bin 포맷 codec + TagIndex(검색) — 런타임/빌드/테스트 공유
├── store.py           # 캐시 경로, 아티팩트 다운로드/검증/로드, custom 파일 로드
├── routes.py          # /danbooru-tag-autocomplete/{db,status,custom}
├── web/
│   ├── dtautocomplete.js   # registerExtension 진입점, STRING 후킹, MutationObserver
│   ├── search.js           # flat index binary search + custom 오버레이
│   ├── dropdown.js         # DOM 드롭다운 + 키보드/마우스
│   └── caret.js            # 캐럿 좌표 계산 (MIT, textarea-caret-position 포팅)
├── build/
│   ├── sources/base.py         # SourceAdapter 인터페이스
│   ├── sources/hlibr.py
│   ├── sources/hdiffusion.py
│   ├── sources/danbooru_api.py # 미사용. 인터페이스 성립 증명용 어댑터
│   ├── fetch_upstream.py
│   ├── build_database.py
│   └── validate_database.py
├── profiles/{danbooru,illustrious,noobai,pony,wai}.yaml
├── data/
│   ├── latest.json    # CI가 갱신하는 커밋 대상 포인터
│   └── raw/           # 빌드 캐시 (gitignore)
├── generated/         # 로컬 빌드 산출물 (gitignore)
├── tests/
├── .github/workflows/update-data.yml
├── pyproject.toml
├── requirements.txt   # 런타임 의존성 없음. 주석만 둔다
└── README.md
```

`build/`는 런타임에서 import하지 않는다. 빌드 스크립트는 repo root를 `sys.path`에 넣어 `artifact.py`를 공유한다(포맷 정의 중복 금지).

`__init__.py`는 `nodes.py`, `routes.py`, `store.py`를 상대 import로 불러온다.

## 6. upstream 소스와 파이프라인

### 6.1 SourceAdapter 인터페이스

```python
class SourceAdapter(Protocol):
    id: str                    # 예: "hlibr/danbooru-tag-metadata-snapshot"
    def fetch(self, cache_dir: Path) -> FetchResult: ...
    def read(self, fetched: FetchResult) -> Iterator[TagRecord]: ...
```

- `FetchResult`는 로컬 파일 경로 목록, HF revision sha, 데이터 날짜를 담는다.
- `TagRecord = dataclass(name, category, post_count, is_deprecated, aliases: tuple[str, ...])`
- 어댑터는 **파일 schema를 이름으로 매핑**하고, 필수 컬럼이 없으면 조용히 넘어가지 않고 명확한 예외로 실패한다. upstream schema에 강결합하지 않는다.
- `danbooru_api.py`는 이번에 실행하지 않지만, Danbooru API 응답을 `TagRecord`로 변환하는 구현을 두어 인터페이스가 실제로 성립함을 보인다. CI에서 실행하지 않는다.

### 6.2 merge 우선순위 (빌드)

```
HDiffusion (최신 count/alias)  >  hlibr (전체 집합, deprecated, alias status)
```

1. hlibr의 전체 태그로 기준 집합을 만든다(category, is_deprecated 포함).
2. HDiffusion에서 같은 이름의 태그가 있으면 `post_count`를 덮어쓰고 alias를 합집합으로 추가한다.
3. HDiffusion에만 있는 태그(2026-04-08 이후 신규)는 추가하며 `is_deprecated=False`로 둔다.

HDiffusion은 `count >= 50`만 담으므로 **저빈도 태그의 최신 count는 hlibr 값(2026-04-08)을 유지**한다. 이 한계를 README와 metadata에 명시한다.

사용자 custom tag는 빌드에 포함하지 않는다. 런타임 오버레이로만 동작한다(§13.2).

### 6.3 alias 정규화

- 수집: hlibr `tag_aliases` 중 `status == "active"`인 행(그 외 status는 제외), HDiffusion CSV의 alias 필드.
- 정규화: 소문자화, `_`와 공백 통일, 앞뒤 공백 제거. `alias == canonical`이면 버린다.
- 체인 해소: alias → canonical → … 를 최대 8단계 따라가 최종 canonical로 접는다. 순환이면 버리고 validate가 보고한다.
- 대상이 없는 alias는 버리고 validate가 개수를 보고한다.
- deprecated canonical 충돌: canonical이 `is_deprecated`이면서 동시에 다른 alias의 대상인 경우, chain을 따라 non-deprecated canonical로 접는다. 접을 수 없으면 해당 alias를 버리고 validate가 보고한다.

### 6.4 profile filter와 임계값

- profile yaml: `name`, `threshold`(기본 25), `exclude_categories`, `exclude_deprecated`(기본 true), `extra_sources`(기본 빈 목록).
- `post_count >= threshold`인 태그만 이름 목록에 넣는다.
- deprecated 태그는 `exclude_deprecated`면 이름 목록에서 제외하되 alias 매핑은 유지한다.
- **alias-only 엔트리**: 남은 alias의 canonical이 임계값 미달로 이름 목록에 없으면, 그 canonical을 이름 목록에 추가한다(alias 검색이 유효한 태그로 이어지도록). 이 엔트리도 정렬·인덱싱 대상이다.
- category 0/1/3/4/5 외의 값은 validate에서 실패시킨다.

### 6.5 정규화 세부

- `post_count`는 음수를 0으로 clamp한다. 상한 sanity 검사는 validate가 담당한다.
- 저장되는 태그 이름과 alias는 항상 소문자다. 대소문자 무시는 쿼리를 소문자화해서 얻는다.
- 정렬 키는 저장된 이름의 UTF-8 byte 열, byte-wise 오름차순이다. 브라우저는 `names` blob의 byte를 직접 비교하므로 Python과 완전히 같은 순서를 얻는다.
- 이름은 UTF-8로 인코딩한다. 인코딩 실패는 validate가 실패시킨다.

## 7. 런타임 아티팩트 포맷 (version 1)

모든 다중 바이트 정수는 little-endian이다.

### 7.1 `tags.bin` 헤더 (64 bytes)

| offset | size | field |
|---|---|---|
| 0 | 4 | magic = `b"DTA1"` |
| 4 | 2 | format_version = 1 |
| 6 | 2 | flags (bit0 = has_aliases) |
| 8 | 4 | n_tags |
| 12 | 4 | n_aliases |
| 16 | 4 | threshold |
| 20 | 4 | off_names |
| 24 | 4 | off_name_offsets |
| 28 | 4 | off_category |
| 32 | 4 | off_post_count |
| 36 | 4 | off_tag_flags |
| 40 | 4 | off_alias_names |
| 44 | 4 | off_alias_offsets |
| 48 | 4 | off_alias_target |
| 52 | 4 | len_names |
| 56 | 4 | len_alias_names |
| 60 | 4 | reserved = 0 |

### 7.2 섹션 (인코더가 쓰는 순서, 각 섹션 시작은 4-byte 정렬)

| 섹션 | 내용 |
|---|---|
| `names` | `len_names` bytes. 태그 이름을 구분자 없이 이어붙인 UTF-8 |
| `name_offsets` | `(n_tags + 1) * uint32`. `names` 안의 byte offset |
| `category` | `n_tags * uint8` |
| `post_count` | `n_tags * uint32` |
| `tag_flags` | `ceil(n_tags / 8)` bytes. 태그 인덱스 i의 bit은 `i % 8`, bit0 = deprecated |
| `alias_names` | `len_alias_names` bytes. alias 문자열을 이어붙인 UTF-8 |
| `alias_offsets` | `(n_aliases + 1) * uint32` |
| `alias_target` | `n_aliases * uint32`. 태그 배열 인덱스 |

### 7.3 불변식

- `names`는 byte-wise 오름차순이며 중복이 없다.
- `alias_names`는 byte-wise 오름차순이며 중복이 없다.
- `name_offsets`와 `alias_offsets`는 단조 비감소, 첫 값 0, 마지막 값 = 해당 blob 길이.
- 모든 `alias_target[i] < n_tags`.
- 헤더의 모든 offset은 4의 배수다.
- 모든 섹션 범위는 버퍼 안에 있고 서로 겹치지 않는다.
- 위 불변식은 `artifact.py`의 decoder가 검증하고, 위반 시 예외를 낸다.

### 7.4 `metadata.json`

릴리스 asset이자 캐시 파일이다.

```json
{
  "format_version": 1,
  "data_version": "2026.09.22",
  "built_at": "2026-09-22T18:00:00Z",
  "profile": "danbooru",
  "threshold": 25,
  "counts": { "tags": 0, "aliases": 0, "deprecated": 0 },
  "sources": [
    { "id": "hlibr/danbooru-tag-metadata-snapshot", "revision": "<sha>", "data_date": "2026-04-08" },
    { "id": "HDiffusion/historical-danbooru-tag-counts", "revision": "<sha>", "data_date": "2026-09-22" }
  ],
  "artifact": { "file": "tags.bin.gz", "sha256": "<hex>", "size": 0, "raw_size": 0 }
}
```

### 7.5 `data/latest.json` (repo에 커밋, CI가 갱신)

```json
{
  "data_version": "2026.09.22",
  "profile": "danbooru",
  "sha256": "<tags.bin.gz의 sha256>",
  "size": 0,
  "url": "https://github.com/chynggi/ComfyUI-Danbooru-Tag-Autocomplete/releases/download/data-2026.09.22/tags.bin.gz"
}
```

### 7.6 압축과 배포 파일

- 릴리스 asset은 `tags.bin.gz`와 `metadata.json` 두 개다.
- gzip은 `mtime=0`으로 결정적으로 생성한다(같은 입력이면 같은 sha256). CI 멱등성에 필요하다.
- 브라우저는 `fetch(url).then(r => r.arrayBuffer())`로 받는다. `Content-Encoding: gzip`은 브라우저가 투명하게 해제한다.
- Python은 `gzip.decompress`로 메모리에서 읽는다. 파일을 풀어 쓰지 않는다.

## 8. 검색 알고리즘

### 8.1 토큰 추출

- 커서 위치 앞쪽에서 마지막 구분자(`\n`, `,`, `;`) 이후를 현재 토큰으로 본다.
- 토큰을 trim하고 소문자화한 뒤 `_`와 공백을 `_`로 통일한다.
- 토큰이 비면 드롭다운을 닫는다. 1글자부터 검색한다.

### 8.2 검색과 정렬

main 인덱스의 이름/alias 배열과 custom 오버레이의 이름/alias 배열을 각각 binary search한다.

1. **exact**: 정규화한 이름이 토큰과 정확히 같으면 rank 0.
2. **name prefix**: 이름이 토큰으로 시작하면 rank 1. tie-break은 이름 길이 오름차순, 그다음 `post_count` 내림차순.
3. **alias prefix**: alias가 토큰으로 시작하면 canonical로 매핑해 rank 2. canonical 이름으로 중복을 제거하고 `post_count` 내림차순.

최종 정렬: rank → (rank 1은 이름 길이) → `post_count` 내림차순 → 이름 오름차순. 결과를 `limit`(기본 32)까지 자른다. 단순 `post_count` 내림차순을 쓰지 않는다.

- custom과 main에 같은 이름이 있으면 custom 항목만 남긴다(custom 우선).
- 결과에서 canonical 이름 기준으로 중복을 제거한다.

### 8.3 deprecated / alias 상태 표시

- `exclude_deprecated`가 참이면 deprecated 태그는 결과에서 제외한다.
- 사용자가 query에 deprecated 태그를 입력했고 그 태그가 alias 대상이면 후보에 `deprecated → canonical`을 표시한다.
- alias로 매칭된 후보는 `alias_name → canonical`을 표시하고, 삽입 시 canonical로 치환한다.

### 8.4 공유

main 인덱스 + custom 오버레이를 합쳐 검색하는 로직은 `artifact.py`의 `TagIndex`가 담당하며, 노드와 테스트가 이를 쓴다. `web/search.js`가 같은 알고리즘을 구현하고, `tests/test_search.py`의 고정 fixture로 Python 결과와 일치함을 검증한다(브라우저 검색은 수동 체크리스트로 보완).

## 9. 프론트엔드

### 9.1 진입점과 후킹

`web/dtautocomplete.js`가 `import { app } from "../../scripts/app.js";`와 `import { ComfyWidgets } from "../../scripts/widgets.js";`를 쓴다(pysssss autocomplete가 쓰는 검증된 경로).

```js
const STRING = ComfyWidgets.STRING;
ComfyWidgets.STRING = function (node, inputName, inputData) {
  const r = STRING.apply(this, arguments);
  if (inputData?.[1]?.multiline && r?.widget) attach(r.widget);
  return r;
};
```

- `attach(widget)`는 `widget.element`(textarea)를 얻어 textarea당 1개 인스턴스를 만들고 `WeakMap`으로 중복을 막는다.
- **MutationObserver fallback**: `textarea.comfy-multiline-input`이 추가되면 붙인다. Nodes 2.0의 Vue 경로에서 위 후킹이 누락되는 경우를 덮는다.
- `widget.element`가 없으면 조용히 건너뛴다(`inputEl`은 쓰지 않는다).
- 확장 `name`은 `danbooruTagAutocomplete`.

### 9.2 드롭다운

- `div.dtautocomplete`를 만들어 `document.body`에 append하고, 캐럿 좌표 계산(`caret.js`)으로 위치를 잡는다.
- 항목 표시: 태그명, category 배지(general/artist/copyright/character/meta 색상 구분), post count(`1.2M`/`82K` 형식), alias/deprecated 상태.
- 마우스 hover로 항목 선택, click으로 삽입.

### 9.3 키보드

- `ArrowUp` `ArrowDown` `PageUp` `PageDown`: 항목 이동.
- `Tab` `Enter`: 삽입. 설정으로 각각 켜고 끌 수 있다.
- `Escape`: 닫기.
- **드롭다운이 닫혀 있으면 아무 키도 가로채지 않는다.** 프롬프트 입력을 절대 방해하지 않는다.

### 9.4 삽입

- 현재 토큰 범위를 교체한다. alias/deprecated면 canonical 이름으로 삽입한다.
- replacement 뒤 다음 문자가 구분자나 공백이 아니면 `", "`를 덧붙인다. 기존 콤마와 공백은 보존한다.
- `document.execCommand("insertText", false, text)`를 먼저 시도한다(undo 보존). 실패하면 `setRangeText` + `InputEvent("input")`로 대체한다.
- `_` 유지가 기본이며, 설정으로 공백 치환을 선택할 수 있다.

### 9.5 설정 (ComfyUI settings)

| id | 기본값 | 설명 |
|---|---|---|
| `DanbooruTagAutocomplete.Enabled` | true | 전체 on/off |
| `DanbooruTagAutocomplete.SuggestionCount` | 32 | 드롭다운 항목 수 |
| `DanbooruTagAutocomplete.InsertOnTab` | true | Tab 삽입 |
| `DanbooruTagAutocomplete.InsertOnEnter` | false | Enter 삽입 |
| `DanbooruTagAutocomplete.ReplaceUnderscores` | false | 공백으로 삽입 |
| `DanbooruTagAutocomplete.ShowPostCount` | true | post count 표시 |
| `DanbooruTagAutocomplete.Categories` | 전체 | 표시할 category |
| `DanbooruTagAutocomplete.ForceEnableWithOtherAutocomplete` | false | 다른 autocomplete 확장과 동시 활성 |

### 9.6 DB 로딩과 오류 표시

- 프론트는 시작 시 `/status`를 호출한다.
- `ready`면 `/db`에서 아티팩트를 받아 인덱스를 만든다.
- `downloading`이면 2초 간격으로 폴링하고, 60초를 넘기면 중단하고 오류를 표시한다.
- `missing` 또는 `error`면 ComfyUI toast로 명확한 오류와 원인을 표시하고, 콘솔에 상세 로그를 남긴다. autocomplete는 비활성 상태로 남는다.
- 인덱스 로딩이나 검색 중 예외가 나면 드롭다운만 비활성화하고 에러를 1회 로깅한다.

### 9.7 다른 확장과의 공존

- `app.extensions`에 `autocompleter`를 이름에 포함한 다른 확장이 있으면 기본적으로 자체 비활성화하고 안내 로그를 남긴다(중복 드롭다운 방지). `ForceEnableWithOtherAutocomplete` 설정으로 강제 활성화할 수 있다.
- 전역 namespace(`danbooruTagAutocomplete.*`)로 설정/확장 이름 충돌을 피한다.

## 10. 서버 런타임과 업데이트

### 10.1 `__init__.py`

- `WEB_DIRECTORY = "./web"`
- `NODE_CLASS_MAPPINGS = {"DanbooruTagSearch": DanbooruTagSearch}`, `NODE_DISPLAY_NAME_MAPPINGS = {"DanbooruTagSearch": "Danbooru Tag Search"}`
- `routes.py`의 라우트를 `PromptServer.instance.routes`에 등록한다.

### 10.2 `store.py`

- 캐시 디렉터리: `os.path.join(folder_paths.get_user_directory(), "danbooru-tag-autocomplete")`.
- 파일: `tags.bin.gz`, `metadata.json`, `custom_tags.csv` 또는 `custom_tags.json`.
- `status()`: `ready | downloading | missing | error`와 `data_version`, `error` 메시지를 반환한다.
- `ensure_download()`: 캐시가 없으면 백그라운드 스레드에서 `data/latest.json`을 받아 `data_version`을 확인하고, `tags.bin.gz`를 내려받아 sha256을 검증한 뒤 임시 파일 → `os.replace`로 원자적 이동한다. 동시 호출은 lock으로 1회만 수행한다.
- 실패 정책: `ensure_download()`는 실패하면 상태를 `error`로 기록하고 **자동 재시도하지 않는다**. `/status`가 몇 번 호출되어도 추가 시도가 없어야 한다(폴링 폭주 방지). 재시도는 ComfyUI 재시작 또는 명시적 재시도 요청에서만 일어난다.
- 기존 캐시가 유효하면 다운로드 실패와 무관하게 `ready`를 유지한다.
- `requests`를 쓴다(ComfyUI의 기존 의존성). 런타임에 새 의존성을 추가하지 않는다.
- `load_custom()`: custom 파일을 파싱해 메모리 오버레이를 만든다. mtime이 바뀌면 다시 읽는다. 파싱 실패는 사용자에게 보고하고 오버레이를 비운다.
- Python 검색용 아티팩트는 `TagIndex`가 lazy하게 로드한다. 노드를 쓰지 않으면 메모리를 점유하지 않는다.

### 10.3 `routes.py`

| route | 동작 |
|---|---|
| `GET /danbooru-tag-autocomplete/status` | `store.status()`를 JSON으로 반환. 캐시가 없고 아직 시도하지 않았으면 `ensure_download()`를 킥오프하고 `downloading`을 반환 |
| `GET /danbooru-tag-autocomplete/db` | 캐시의 `tags.bin.gz`를 `web.FileResponse`로 반환, `Content-Encoding: gzip`, `ETag`/`Last-Modified` 부여. 없으면 404 + JSON 오류 |
| `GET /danbooru-tag-autocomplete/custom` | custom 오버레이를 JSON으로 반환. 없으면 빈 목록 |

### 10.4 오프라인 동작

- 캐시가 있으면 네트워크 없이 계속 동작한다. 업데이트 확인만 실패하고 조용히 넘어간다.
- 캐시가 없고 네트워크도 없으면 `error` 상태와 명확한 메시지("태그 데이터베이스가 없습니다. 네트워크 연결 후 ComfyUI를 재시작하세요")를 표시한다.

## 11. 빌드와 검증

### 11.1 `fetch_upstream.py`

- `--source`, `--cache data/raw`, `--out generated/raw`를 받는다.
- HF API로 파일 목록/revision을 조회하고, 필요한 파일만 내려받아 sha256과 함께 기록한다.
- 이미 같은 revision이 캐시에 있으면 다시 받지 않는다.

### 11.2 `build_database.py`

- `--profile`(기본 `danbooru`), `--out generated`를 받는다.
- §6의 파이프라인을 수행하고 `generated/tags.bin.gz`, `generated/metadata.json`을 만든다.
- `data_version`은 사용한 소스 데이터 날짜의 최댓값이다. 같은 날짜로 다시 빌드해야 하면(예: hlibr 스냅샷 교체) `2026.09.22.1`처럼 `.N` 접미사를 붙인다. N은 기존 `data/latest.json` 값을 보고 증가시킨다.
- 산출물 생성 후 `validate_database.py`를 내부 호출하고, 실패하면 산출물을 지우고 예외를 낸다.

### 11.3 `validate_database.py`

검사 항목:

1. `tags.bin`의 magic / format_version / 섹션 offset 정렬과 범위
2. `name_offsets`, `alias_offsets`의 단조성과 경계
3. 이름 정렬 순서 위반과 중복
4. alias 정렬 순서 위반과 중복
5. `alias_target` 범위 위반
6. 빈 태그 / 공백만 있는 태그
7. 잘못된 category 값(0/1/3/4/5 외)
8. invalid UTF-8
9. alias cycle
10. alias → 존재하지 않는 태그
11. deprecated canonical 충돌
12. 비정상적으로 큰 `post_count`(상한 50,000,000 초과)
13. schema 변경(소스 어댑터에서 필수 컬럼 누락)
14. `metadata.json`과 `tags.bin`의 `n_tags`/`n_aliases` 불일치, sha256 불일치
15. custom 파일 형식 오류(경고로 보고하며 릴리스를 막지 않는다)

실패 시 종료 코드 1을 반환한다. CI는 실패 시 릴리스하지 않는다.

## 12. CI와 릴리스

`.github/workflows/update-data.yml`:

- 트리거: `schedule` (매일 18:00 UTC = 03:00 KST), `workflow_dispatch`
- 단계: checkout → python 설정 → `requests`, `pyarrow` 설치 → `fetch_upstream.py` → `build_database.py` → `validate_database.py` → 산출물 `sha256`을 `data/latest.json`과 비교 → 같으면 여기서 종료 → Release `data-<data_version>` 생성/갱신 + `tags.bin.gz`, `metadata.json` 업로드 → `data/latest.json` 커밋
- gzip이 결정적이므로 같은 소스면 `sha256`이 같다. 이 비교가 유일한 변경 감지 수단이다.
- `permissions: contents: write`
- 데이터 릴리스(`data-*`)와 코드 릴리스(`v*`, 수동)를 분리한다.
- 빌드 의존성(`pyarrow`)은 CI에서만 쓴다. 노드 런타임 의존성은 0이다.

## 13. 프로파일과 custom tag DB

### 13.1 profile yaml

```yaml
name: danbooru
threshold: 25
exclude_categories: []
exclude_deprecated: true
extra_sources: []
```

- `danbooru`가 기본이며, `illustrious`/`noobai`/`pony`/`wai`는 같은 DB에 filtering과 추가 tag source를 지정하는 용도다.
- `extra_sources`는 기본 소스(hlibr + HDiffusion) 위에 덮어쓸 추가 `SourceAdapter` id 목록이다. 기본값은 빈 목록이며, 다른 booru를 추가하는 확장 지점이다.
- 모델별 태그 지원 여부를 추론하지 않는다. 학습 데이터 기반 filtering은 확장 지점으로만 남긴다.

### 13.2 custom tag DB (런타임 오버레이)

- 위치: `user/danbooru-tag-autocomplete/custom_tags.csv` 또는 `custom_tags.json`.
- CSV 포맷: `tag,category,post_count,alias`. JSON 포맷: `[{"tag": "...", "category": 0, "post_count": 0, "alias": ["..."]}]`.
- 행의 의미:
  - `alias` 열이 비면 그 행은 **태그 선언**이다. `category`와 `post_count`를 사용한다.
    예: `example_tag,general,0,`
  - `alias` 열이 비어 있지 않으면 그 행은 **별칭 선언**이다. 행의 `tag`는 태그가 아니라 별칭 이름이고, 열의 값이 canonical 대상이다. 대상이 여러 개면 첫 번째만 쓰고 경고한다.
    예: `my_old_tag,general,0,example_tag` → `my_old_tag`가 `example_tag`로 연결된다.
  - 같은 이름이 태그로도 별칭으로도 선언되면 태그 선언이 이기고 별칭 선언은 버린다.
  - 대상이 custom 행에도 main 인덱스에도 없으면 그 별칭은 경고와 함께 버린다. 대상이 main 인덱스에 있으면 그 항목을 오버레이로 복사해 `alias_target`이 가리킬 수 있게 한다.
- `category`는 Danbooru 숫자(`0/1/3/4/5`)와 이름(`general/artist/copyright/character/meta`)을 모두 받는다. 범위 밖 숫자는 경고 후 `general`로 떨어지고, 숫자로도 이름으로도 해석할 수 없으면 오류다.
- 빌드 산출물에 포함되지 않는다. 노드는 파일을 읽어 메모리 오버레이로, 프론트는 `/custom` 응답으로 받아 검색 시 병합한다.
- custom 항목이 main 인덱스와 같은 이름이면 custom이 이긴다(custom 우선).
- 형식 오류는 오버레이를 비우고 사용자에게 보고한다. main 검색은 계속 동작한다.

## 14. 라이선스

| 대상 | 라이선스 | 비고 |
|---|---|---|
| 이 프로젝트 | MIT | |
| `hlibr/danbooru-tag-metadata-snapshot` | MIT | Danbooru API에서 생성. README에 출처 표기 |
| `HDiffusion/historical-danbooru-tag-counts` | **불명확** | LICENSE 파일 없음, card 태그만 apache-2.0 |
| Danbooru tag 데이터 자체 | ToS상 "factual information" | 태그는 저작권 비보호로 규정. wiki 본문은 사용하지 않음 |

- 프로젝트 라이선스는 MIT로 한다.
- HDiffusion은 출처와 라이선스가 불명확하므로 **리스크로 취급**한다. 소스 URL을 config로 교체 가능하게 만들고, 이 소스가 사라지거나 제거 요청이 오면 `hlibr`만으로 빌드가 성립하도록 폴백을 둔다. README에 출처와 한계를 명시한다.
- 불확실한 데이터를 런타임 패키지에 재배포하지 않는다. 런타임은 빌드된 아티팩트를 배포하며, raw dataset은 배포물에 포함하지 않는다.

## 15. 테스트

pytest:

| 파일 | 검증 |
|---|---|
| `test_tokenizer.py` | 토큰 추출(구분자, 커서 중간, 후행 구분자), `_`/공백 통일, 소문자화, CJK |
| `test_search.py` | exact 우선, prefix 정렬, alias 매핑과 중복 제거, deprecated 제외, custom 우선, rank/tie-break |
| `test_artifact.py` | encode→decode 왕복, 정렬 불변식, 4-byte 정렬, 손상 입력 거부, 결정적 gzip |
| `test_build.py` | merge 우선순위(HDiffusion > hlibr), alias chain/cycle, alias-only 포함, threshold, category/bad row 실패 |
| `test_validate.py` | §11.3의 각 규칙이 위조 아티팩트에서 실제로 실패하는지 |
| `test_custom.py` | CSV/JSON custom 파싱, main과의 이름 충돌 시 custom 우선, 형식 오류 처리 |
| `test_benchmark.py` | 합성 1,710,000 태그 아티팩트로 decode 시간과 1,000회 질의 p95를 측정/기록. CI에서 질의 p95 < 50ms를 단언 |

프론트엔드 수동 체크리스트(README에 기재):

- 최신 프론트엔드(1.53.6)에서 동작
- CLIPTextEncode positive/negative 필드에서 suggestion 표시
- multiline STRING을 쓰는 다른 노드
- 여러 노드 인스턴스
- 커서 중간 삽입과 콤마/공백 보존
- Tab/Enter/Escape/방향키/마우스
- Nodes 2.0 on/off
- pysssss autocomplete 동시 활성
- custom tag 파일 반영
- DB 없음 / 오프라인 상태의 오류 표시

## 16. 마일스톤

| | 내용 | 검증 |
|---|---|---|
| M1 | `artifact.py` + `build/`(sources, fetch, build, validate) + 전용 테스트 | 실데이터로 `tags.bin.gz` 생성, validate 통과, benchmark 통과 |
| M2 | `store.py` + `routes.py` + `nodes.py` + `web/` | 로컬 ComfyUI에서 CLIPTextEncode 필드 suggestion 동작, 노드 문자열 출력, custom 반영 |
| M3 | `update-data.yml` + profiles + README | CI 드라이런으로 아티팩트 릴리스, 수동 체크리스트 전 항목 |

## 17. 가정과 미확인 사항

- repo slug는 `chynggi/ComfyUI-Danbooru-Tag-Autocomplete`로 확정했다(2026-09-22 공개 repo 생성). GitHub Actions와 릴리스 URL이 이 값에 의존한다.
- Nodes 2.0 환경에서 전역 textarea 후킹이 실제로 동작하는지는 실기 검증이 필요하다. MutationObserver fallback을 그 대비책으로 둔다.
- HDiffusion dataset이 계속 갱신되는지, 라이선스가 명확해지는지는 보장할 수 없다. 소스 교체 가능 구조와 폴백으로 대응한다.
- `hlibr`는 2026-04-08 단일 스냅샷이다. 저빈도 태그의 count와 deprecated 정보는 그 시점 기준이다.
- HDiffusion은 `count >= 50`만 담으므로 count 50 미만 태그의 최신 count는 갱신되지 않는다.
- 서버측 검색이 없으므로 브라우저 인덱스 구축 시간은 태그 수에 비례한다. 기본 threshold 25에서 실제 측정값을 README에 기록한다.
