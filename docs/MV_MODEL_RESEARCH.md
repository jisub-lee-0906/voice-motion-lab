# MV 일러스트 모델 조사 — 2026-10-17

이번 단계는 공식 문서·모델 카드·파일 메타데이터·공식 워크플로우 JSON의 읽기 전용 조사다. 모델 다운로드, 패키지 설치, 추론, 서버 API 호출, 프로세스 변경을 하지 않았다. 작성한 파일은 이 문서뿐이다. 기존 Studio의 모델·환경·작업은 조사하거나 변경하지 않았다.

## 결정

**첫 검증은 FLUX.2 [klein] 4B distilled FP8로 원본 캐릭터 기준 이미지 한 장을 만들고, 같은 이미지를 참조해 세 컷을 편집하는 방식이 가장 현실적이다.** 16GB 전용 GPU에서 시도할 근거가 있고, T2I와 다중 참조 편집이 같은 모델 계열에 있으며, 공식 Comfy 그래프가 공개되어 있다. 성공한 그림 세 장을 먼저 확인한 뒤 MV 렌더러를 연결한다. 현재 절차적 도형을 더 움직이거나 CPU LCM 계열 실험을 반복하는 방법으로 화질 문제를 해결했다고 간주하지 않는다.

**Qwen-Image-Edit-2511은 두 번째 후보이지만 현재 32GB 공유 RAM/16GB VRAM 장비의 첫 시도로 권하지 않는다.** 캐릭터 편집의 장점은 분명하나 공식 FP8 모델만 20.53GB이고 전체 필수 가중치는 약 30.17GB이다. 이 보고서에서는 후보를 이 두 계열로 제한한다.

두 모델 모두 이 PC에서의 품질·최대 메모리·생성 시간은 아직 검증하지 않았다. “공식 예제가 있다”와 “우리의 세 컷이 통과했다”는 별개다.

## 1. 우선 후보: FLUX.2 [klein] 4B distilled FP8

### 기능과 근거

제작사 BFL은 이 모델에 T2I·이미지 편집·다중 이미지 참조 기능을 제공한다. 공식 카드에는 약 13GB VRAM에서 사용 가능하다는 설명과 GPU 추론 중 모델을 CPU로 옮기는 `enable_model_cpu_offload()` 예제가 있다. 이것은 CPU만으로 빠르게 생성된다는 뜻이 아니다. [BFL 원본 모델 카드](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/blob/e7b7dc27f91deacad38e78976d1f2b499d76a294/README.md)

Comfy 공식 가이드는 distilled 4-step 및 다중 참조 편집을 안내하며 RTX 5090 기준 distilled 약 8.4GB VRAM/1.2초, base 약 9.2GB/17초를 표기한다. BFL의 약 13GB와 조건이 같다고 단정할 수 없고, RTX 5070 Ti에서 같은 속도나 메모리를 약속할 수 없다. [Comfy 가이드](https://docs.comfy.org/tutorials/flux/flux-2-klein)

### 고정할 파일과 크기

아래 숫자는 공식 Hugging Face API의 LFS 크기와 SHA256이다. GB는 10진수이며 VRAM 사용량이 아니다. 필수 세 파일 합계는 **12,451,817,860 bytes = 12.452GB**이고, 문서·라이선스·출력·환경 설치 공간은 별도다.

| 용도 | 공식 저장소 / 고정 revision | 파일 | bytes |
|---|---|---|---:|
| diffusion | `black-forest-labs/FLUX.2-klein-4b-fp8` / `5b4408e59397a4a37ccb46afe426d8ed86379441` | `flux-2-klein-4b-fp8.safetensors` | 4,070,624,520 |
| text encoder | `Comfy-Org/flux2-klein-4B` / `5f526678002e43af5551dadb73ce2e8c91b43afe` | `split_files/text_encoders/qwen_3_4b.safetensors` | 8,044,982,048 |
| VAE | 동일 Comfy 저장소·revision | `split_files/vae/flux2-vae.safetensors` | 336,211,292 |

공식 고정 다운로드 위치:

- [FP8 diffusion](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/5b4408e59397a4a37ccb46afe426d8ed86379441/flux-2-klein-4b-fp8.safetensors) — SHA256 `97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6`
- [Qwen3 4B encoder](https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/5f526678002e43af5551dadb73ce2e8c91b43afe/split_files/text_encoders/qwen_3_4b.safetensors) — `6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a`
- [VAE](https://huggingface.co/Comfy-Org/flux2-klein-4B/resolve/5f526678002e43af5551dadb73ce2e8c91b43afe/split_files/vae/flux2-vae.safetensors) — `868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3`

**라이선스:** BFL FP8 모델과 Comfy 재패키징 카드 모두 Apache-2.0이며 ungated이다. BFL 저장소에는 실제 `LICENSE.md`가 있다. TE의 원본 [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B)도 Apache-2.0이다. 설치를 구현할 때 BFL 원본 [LICENSE](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/blob/5b4408e59397a4a37ccb46afe426d8ed86379441/LICENSE.md), 각 모델 카드와 provenance를 함께 보존한다. 여기서는 파일을 다운로드하지 않았다. 9B의 비상업 라이선스를 4B에 혼동하지 않는다.

### 이미 공개된 공식 Comfy 그래프

`Comfy-Org/workflow_templates`는 MIT이며 조사 시점 commit은 `04a508b31bec1818e48122ca0761c1de4140a234`다. 다음 JSON을 실제로 읽어 노드와 설정을 확인했다.

- [T2I 그래프](https://github.com/Comfy-Org/workflow_templates/blob/04a508b31bec1818e48122ca0761c1de4140a234/templates/image_flux2_klein_text_to_image.json) — SHA256 `cd4a29b0be6f71a7391f2f023e69dfc3b47d7da9f2aa215b4c8b8e17c2eea107`
- [4B distilled 참조 편집 그래프](https://github.com/Comfy-Org/workflow_templates/blob/04a508b31bec1818e48122ca0761c1de4140a234/templates/image_flux2_klein_image_edit_4b_distilled.json) — `7f1ead62d1456d485e95fb432fb492d2783f5ec9cfeefefe4ef71bfb448ba1a3`

핵심은 `UNETLoader`, `CLIPLoader(type=flux2)`, `VAELoader`, `CLIPTextEncode`, `Flux2Scheduler`, `CFGGuider`, `KSamplerSelect`, `SamplerCustomAdvanced`, `VAEDecode`다. 편집 그래프에는 `VAEEncode`와 `ReferenceLatent`가 실제 연결되어 있고 두 참조 입력 예제가 있다. UUID 모양의 노드들은 JSON의 subgraph 정의이므로 API 전송 전에 풀거나 API 형식으로 내보내야 한다. GUI JSON을 그대로 SDK에 제출하지 않는다.

편집 예제 기본값은 공식 FP8 diffusion, Euler, **4 steps / CFG 1**, 약 1MP 입력이다. T2I 예제에는 base와 distilled 경로가 함께 있고 BF16 파일명도 있으므로, 우선 시험에서는 **distilled 경로만 선택하고 공식 FP8 파일명으로 명시적으로 바꾼다.** base의 20 steps / CFG 5를 distilled와 섞지 않는다.

프로젝트의 독립 Comfy 소스 commit `bdc63e72f5ab29fde448c161db3057827148c0ef`에는 `Flux2` 지원과 `Flux2Scheduler` 코드가 존재함을 파일 읽기로 확인했다. 이 사실만으로 현재 CPU 환경에서 FP8 GPU 실행이 된다고 볼 수 없다. 전체 그래프의 누락 노드·CUDA 연산·출력은 별도 검증 대상이다.

### 첫 실행 레시피 — 아직 실행하지 않음

1. 기존 작업이 자연스럽게 끝나 자원이 확보되었는지 확인한다. 다른 작업의 모델을 unload하거나 서버를 중단하지 않는다. 총 16GB 중 약 13GB가 이미 사용 중이면 이 레시피를 실행하지 않는다.
2. 기존 `.comfy-venv`의 CPU ASR 환경을 CUDA로 바꾸지 않는다. 프로젝트 안의 **별도 GPU 환경·실행 설정·캐시·포트·input/output/temp**를 준비하고, RTX 5070 Ti와 호환되는 CUDA PyTorch 빌드인지 작은 연산으로 검증한다. 이 환경 추가는 이 문서에서 수행하지 않았다.
3. 보수적인 첫 자원 목표는 free VRAM 약 14GB와 free RAM 약 16GB 이상이다. 이는 우리 시험의 출발 조건이며 공식 최소 요구나 성공 보장이 아니다. 실제 peak VRAM/RAM을 기록하고 부족하면 실패를 보고한다.
4. 원본 캐릭터 기준 그림을 1024×1024, batch 1로 생성한다. 독자적인 헤어 실루엣·의상·상징·색상 여섯 가지를 고정하고, 셀 음영·단정한 윤곽·한정된 색·인쇄 질감·강한 구도로 미술 방향을 적는다. 가사·로고는 이미지 모델에 그리게 하지 않는다.
5. 기준 그림을 첫 참조로 계속 사용해 wide / medium action / close-up 세 컷을 **각각** 1024×576, batch 1로 편집한다. 두 번째 참조는 필요할 때 프로젝트에서 직접 만든 구도 가이드만 사용한다. 편집 결과를 다음 편집의 유일한 참조로 돌려 identity drift를 누적시키지 않는다.
6. 첫 샘플은 4 steps / CFG 1 / Euler / `Flux2Scheduler`로 고정한다. 가중치·그래프 SHA·prompt·seed·입출력 파일 SHA·시간·peak 메모리를 저장한다. 동일 seed만으로 동일 캐릭터가 보장되지는 않는다.
7. 세 컷이 아래 품질 문턱을 통과하기 전에는 전곡 자동 생성이나 이미지→영상 확장을 하지 않는다. distilled의 미술 품질이 부족할 때만 같은 4B 계열의 base를 비교한다. 공식 base FP8은 `black-forest-labs/FLUX.2-klein-base-4b-fp8@103db268c10d4d3921101b46057671f9ac460da6`, 4,089,498,488 bytes로 TE/VAE를 공유할 수 있다. 추가 다운로드·실행은 자동으로 진행하지 않는다.

## 2. 조건부 후보: Qwen-Image-Edit-2511 FP8mixed

Qwen 공식 카드와 Comfy 가이드는 인물 정체성 유지와 편집 중 drift 감소를 강조한다. 네이티브 참조 편집으로 포즈·구도 변화를 시험할 가치는 있지만, 이는 세 컷의 애니메이션 캐릭터 일관성이 이미 검증됐다는 뜻은 아니다. 기본 Qwen 모델과 단순 txt2img를 매 컷 독립 실행하는 용도로 쓰지 않는다. [Qwen 공식 카드](https://huggingface.co/Qwen/Qwen-Image-Edit-2511/blob/6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9/README.md), [Comfy 네이티브 가이드](https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511)

| 용도 | 공식 저장소 / 고정 revision | 파일 | bytes |
|---|---|---|---:|
| diffusion | `Comfy-Org/Qwen-Image-Edit_ComfyUI` / `f68ace85e60b4a02a323e394253731947657b7d2` | `split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors` | 20,533,762,817 |
| encoder | `Comfy-Org/Qwen-Image_ComfyUI` / `1f12b17be14c89b026c51a91d67c32f84bb047bc` | `split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` | 9,384,670,680 |
| VAE | 동일 저장소·revision | `split_files/vae/qwen_image_vae.safetensors` | 253,806,246 |

합계 **30,172,239,743 bytes = 30.172GB**다. SHA256은 순서대로 `c9fdc158e46d3b61ef75f21ae866ca2fe808bf4a53643120d1c1e87c19280a4e` / `cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4` / `a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f`다.

위 두 Comfy 저장소와 Qwen 모델 카드는 Apache-2.0이다. 원본 Qwen 모델 revision은 `6f3ccc0b56e431dc6a0c2b2039706d7d26f22cb9`다. [Qwen 원본 프로젝트 LICENSE](https://github.com/QwenLM/Qwen-Image/blob/6b5e1f5cec987d404be5ac6657db3b9aacb56a89/LICENSE)도 함께 보존할 대상이다. 가이드의 encoder 링크는 Hunyuan 재패키징 저장소로 가지만, 그 저장소 전체의 `license: other`를 이 구성의 라이선스로 쓰지 않는다. 위 Qwen 재패키징 저장소에 **동일 SHA256의 encoder**가 있어 그 공식 Apache 출처를 사용한다.

[공식 2511 그래프](https://github.com/Comfy-Org/workflow_templates/blob/04a508b31bec1818e48122ca0761c1de4140a234/templates/image_qwen_image_edit_2511.json)는 실제로 FP8mixed, `TextEncodeQwenImageEditPlus`의 image1~3, `VAEEncode`, `KSampler`를 사용한다. SHA256은 `b23103935f437e1fe3184247fa5bafb891a28113d0c63de7449b7cdfb0419111`이다. 조사 시 기본 full 경로는 40 steps / Euler / simple scheduler이며, optional Lightning 경로는 별도다. 이번 추천에는 추가 LoRA를 포함하지 않는다.

**메모리 판단:** 20.53GB diffusion이 16GB VRAM에 전부 상주할 수 없다는 것은 파일 크기에 따른 명백한 제약이다. offload·부분 로딩이 필요하다. Comfy 모델 소개 페이지는 FP8 약 16GB를 안내하지만 batch·참조 수·해상도·호스트 RAM과 peak 측정 조건을 제시하지 않으므로 이 장비의 성공 근거로 충분하지 않다. 현재 공유 RAM 32GB에 약 30GB 파일을 준비하는 구성은 로딩·작업 공간의 여유가 작다. BF16 diffusion은 40,861,031,560 bytes로 더 부적합하다. 공식 가벼운 그래프가 확보되고 자원 측정이 가능할 때만 두 번째로 검토한다. [Comfy 모델 설명](https://comfy.org/workflows/model/qwen-image-edit/)

## 세 컷용 asset pack과 합격 조건

모델이 완성 PNG를 만들었다고 해서 애니메이션용 소재가 완성되지는 않는다. 아래가 첫 제작 증명의 최소 요구다.

| 산출물 | 필요한 내용 |
|---|---|
| `character.json` + 기준 그림 | 원본 캐릭터 ID, 고정 외형 여섯 가지, 색 팔레트, 원본 생성 provenance. 기존 작품 캐릭터·로고를 복제하지 않음 |
| `shot-01` wide | 캐릭터 전신·공간·방향이 읽히는 도입 구도, 합성할 가사 여백 |
| `shot-02` medium action | 같은 의상/얼굴, 손과 물건의 관계가 분명한 다른 행동. 도입 장면의 단순 확대를 컷으로 세지 않음 |
| `shot-03` close-up | 동일 인물의 감정 변화, 시선과 상징의 변화, 후반 회수에 쓸 표정 |
| 각 컷의 합성 소재 | 배경 plate, 캐릭터 RGBA/matte, foreground, 반복 motif. 손·머리·소매 등 가려진 경계의 여유 픽셀과 가려진 배경 복원 |
| `pack.json` | 캔버스·레이어 순서·anchor·parallax 깊이·크롭 여유·허용 변형·입출력 해시·모델/그래프/라이선스 출처 |

참조 편집 모델이 자동 RGBA·올바른 관절·가려진 배경을 제공한다고 주장하지 않는다. 레이어 분리와 배경 복원은 추가 구현/검증 항목이며, 이번 두 후보 조사만으로 해결된 부분이 아니다. 첫 미술 승인에서는 세 합성 장면 자체의 품질을 평가하고, 이후 필요한 레이어 생성이 자동으로 가능한지 검증해야 한다.

합격 문턱은 다음과 같다.

- 세 컷을 나란히 놓았을 때 얼굴·헤어·핵심 장식·의상·팔레트가 같은 인물로 읽힘.
- 구도와 포즈가 실제로 달라지고, 손·소품·시선이 가사에 맞는 사건을 표현함.
- 실루엣·명암·색 면이 960×540 재생에서도 분명하며 글자·원형 도형만으로 화면을 채우지 않음.
- 캐릭터의 얼굴·손·소품 결함을 그대로 영상으로 넘기지 않음. 자동 재시도 정책은 이후 검증하고 첫 샘플에서는 결함을 숨기지 않음.
- 가사는 전사/정렬 결과를 타이포그래피 엔진에서 합성. 그림에 생성된 비정확한 텍스트를 정답 가사로 사용하지 않음.
- 종이 인형처럼 이미지 한 장 전체를 흔드는 것과 캐릭터 동작을 구분함. 좋은 세 컷은 MV의 미술 기반이며, 자연스러운 동작·연결 편집까지 증명한 것은 아님.

## CPU와 GPU의 역할

ASR·타이밍 정렬·음악 특징 분석·연출 JSON·레이어 합성·영상 인코딩은 현재 CPU 파이프라인을 유지할 수 있다. 새 그림 생성과 참조 포즈 편집은 GPU 단계로 분리한다. **모델 CPU offload는 GPU 메모리를 절약하는 방식**이며 CPU 전용 모델 추론을 빠르게 만드는 기능으로 설명하지 않는다.

처음에는 ASR 종료 → GPU 소재 생성 → GPU 작업 종료 → CPU MV 합성을 순차 실행하여 메모리 피크를 겹치지 않게 한다. 별도 생성 환경을 준비하더라도 기존 Studio의 GPU 점유를 빼앗지 않는다. 기존 모델 전체 복제 대신, 신규 필요 파일만 프로젝트 안에 보관하고 사용 가능한 공유 파일이 확인되는 경우에도 라이선스 검증 후 읽기 전용 참조만 사용한다.

최종 판단: **FLUX.2 klein 4B FP8로 세 컷 품질을 먼저 증명한다.** 편집 일관성·레이어 제작·실측 자원을 통과하지 못하면 실패 원인을 기록하고 다음 단계를 보류한다. 이 조사만으로 전곡 MV 품질, 완전 자동 소재 선택, 5070 Ti에서의 속도 또는 사용자가 지목한 작품 수준을 약속하지 않는다.
