# Local Prompt Studio v3.1.1 — 사용자 가이드

[README](../../README.md) | [English](en-US.md) | [日本語](ja-JP.md) | [简体中文](zh-CN.md) | [Русский](ru-RU.md) | **한국어**

## 1. 개요

Local Prompt Studio는 PC의 로컬 GGUF 언어 모델을 사용해 이미지 및 비디오 모델용 프롬프트를 구성하는 Windows 데스크톱 도구입니다. 주요 모드는 **Prompt Generation**, **AI 채팅**, **프롬프트 라이브러리** 세 가지입니다.

프롬프트 생성은 MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, Anima Profile을 지원하며 각 Profile이 자체 Renderer와 출력 형식을 선택합니다. 완성된 프롬프트를 ComfyUI로 보내지 않는다면 ComfyUI는 필요하지 않습니다. 설치 프로그램이 없는 Portable 앱이며 지속 데이터는 압축을 푼 폴더 안에 저장됩니다.

## 2. 요구 사항 및 다운로드

- Windows 10／11 x64
- 쓰기가 가능한 압축 해제 폴더
- 선택한 GGUF와 Context Size에 충분한 System RAM
- Vulkan 사용 시 Vulkan 지원 GPU와 정상적인 드라이버
- Python, Git, pip, CUDA, 관리자 권한 및 수동 PATH 설정 불필요

[공식 GitHub Releases](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)에서만 Windows Portable ZIP을 다운로드하세요. GGUF와 `mmproj`는 포함되지 않습니다. 비공식 출처가 재배포한 EXE는 사용하지 마세요.

같은 Release의 `SHA256SUMS.txt`와 ZIP을 비교합니다.

```powershell
Get-FileHash .\Local-Prompt-Studio-<version>-win-x64-portable.zip -Algorithm SHA256
```

## 3. 처음 10분

1. ZIP을 쓰기 가능한 새 폴더에 풉니다. ZIP 안에서 직접 실행하지 마세요.
2. `LocalPromptStudio.exe`를 실행합니다.
3. 초기 설정에서 기존 GGUF를 선택합니다.
4. 처음에는 **CPU** 또는 llama.cpp가 감지한 **Vulkan GPU**를 선택합니다.
5. **카테고리**, **모델**, **모델 변형**, **작업**을 선택합니다.
6. **요청 (입력 언어 자동 감지)**에 원하는 내용을 입력합니다.
7. **프롬프트 생성**을 선택합니다.
8. 편집 가능한 결과를 확인한 뒤 **복사**, **TXT로 저장** 또는 **프롬프트 라이브러리 → 새 프롬프트**로 저장합니다.

## 4. 초기 설정

GGUF 선택 버튼으로 PC의 기존 `.gguf`를 참조합니다. 앱은 GGUF를 복사하거나 자동 다운로드하지 않습니다. 원본 파일을 이동하거나 삭제했다면 다시 선택해야 합니다.

현재 안내는 높은 품질을 위해 Qwen3-8B Q4_K_M, 메모리가 적은 PC를 위해 Qwen3-4B Q4_K_M을 제안합니다. 이는 참고 사항이며 모든 GGUF 또는 양자화의 호환성을 보장하지 않습니다.

MiniMax H3만 공식 H3 Prompt Skill이 필요합니다. 온라인 상태에서 명시적으로 Skill을 가져오면 Portable `data/skills`에 저장됩니다. Wan 2.2, LTX-2.3, Krea 2, Anima에는 필요하지 않습니다.

잘 모르겠다면 CPU로 시작하세요. Vulkan llama.cpp runtime이 장치를 감지한 경우 Vulkan과 해당 장치를 선택할 수 있습니다. 요약을 확인한 후 설정을 완료합니다.

## 5. Backend, Context 및 메모리 설정

- **CPU**는 기본이자 fallback입니다. **CPU 스레드**로 thread 수를 설정합니다.
- **Vulkan GPU**에서는 `llama-server --list-devices`가 보고한 **Vulkan 장치**만 선택할 수 있습니다.
- **GPU Offload** 기본값은 Auto이며 고급 사용자는 layer 수를 지정할 수 있습니다.
- Context는 4096, **8192 — Recommended**, 16384, 32768입니다. 4096에는 전체 H3 Skill, 입력과 출력이 함께 들어가지 않을 수 있습니다. 큰 값은 더 많은 메모리를 사용합니다.
- Available RAM／Total RAM을 표시합니다. unified memory의 GPU 보고 메모리를 System RAM에 다시 더하지 않아 중복 계산하지 않습니다.
- 메모리 경고가 나오면 다른 앱을 닫거나 Context Size를 낮추거나 더 작은 GGUF를 사용하세요.
- 프롬프트와 채팅 생성의 최대 대기 시간은 300초입니다.

앱은 외부 llama-server process 하나만 관리합니다. model, backend, device, Context, thread가 같으면 재사용하고 필요할 때만 재시작합니다. **모델 언로드**는 앱이 소유한 server를 종료하고 model memory를 해제하며 다음 생성에서 다시 로드합니다. Cancel, timeout, 앱 종료도 Local Prompt Studio가 시작한 server만 중지합니다.

## 6. Prompt Generation

**카테고리**, **모델**, **모델 변형**, **작업**, **프롬프트 변환 스타일**을 선택합니다. 기본 지시는 **요청**에 입력하고, 선택한 작업에서 제공하는 경우 전체／시작 이미지／종료 이미지 보충을 사용합니다. **품질 태그 자동 추가**는 Renderer가 정의한 품질 용어만 제어하며 사용자가 입력한 용어는 유지합니다.

**프롬프트 생성**, **취소**, **다시 생성**을 사용할 수 있습니다. 결과는 편집 가능하며 **복사**, **TXT로 저장**, **번역 및 편집**, **ComfyUI로 보내기**를 지원합니다. Anima는 Positive와 Negative를 별도로 표시합니다. 현재 Bridge는 Positive만 보내므로 Negative는 따로 복사하세요.

UI에서 선택한 작업이 유일한 Task 결정 원본입니다. 요청 내용으로 Task를 바꾸지 않습니다. I2VA／Ref2VA에는 Task Schema Lock이 적용되어 다른 Task schema가 반환되면 자동 수정, retry, Task 변경 없이 결과를 거부합니다. 메시지에는 선택한 Task와 가능한 경우 불일치 top-level field가 표시됩니다. Prompt 형식은 Profile과 Renderer가 결정합니다. Model 이름, `I2VA` 같은 Task token, schema field는 번역하지 않습니다.

## 7. 입력 가이드, Literal Content 및 Protected Terms

**입력 가이드**는 timing, 고정 camera, cut, speech, visible text 예를 요청에 추가합니다. media file을 분석하는 기능은 아닙니다. 시간 구간, camera／cut 의도, 대사와 표시 문자를 요청 또는 해당 보충에 명시하세요.

정확히 유지할 본문에는 paired Literal 형식을 사용합니다.

```text
[speech:ja]おかえりなさい。[/speech]
[text:en]OPEN[/text]
[speech:zh]你好[/speech]
[speech:ru]Привет[/speech]
[speech:ko]안녕하세요[/speech]
```

paired block은 여러 줄도 지원합니다. `[speech:ko] 안녕하세요` 같은 기존 행 시작 형식도 행 끝까지 하위 호환되며 paired marker가 우선합니다. 본문은 생성 Prompt에 완전히 일치해야 하고 marker 자체는 제거됩니다. Protected Terms도 유지됩니다. validation 실패 결과는 채택하지 않습니다. 진단에는 원본 입력란, 감지 방식, 문자 수, 본문을 포함하지 않는 짧은 ID가 표시됩니다. 요청과 접힌 보충 입력란을 확인하세요.

## 8. 프롬프트 번역 편집기

Profile의 Prompt 출력 언어가 번역 원본이고 **UI 언어**가 번역 대상입니다. 현재 Builtin Profile은 영어 Prompt를 출력합니다.

- 日本語 UI: English ↔ Japanese
- 简体中文 UI: English ↔ Simplified Chinese
- Русский UI: English ↔ Russian
- 한국어 UI: English ↔ Korean
- 영어 UI와 영어 출력 Profile에서는 **번역 및 편집**을 표시하지 않습니다.

**구조 보호**는 Skill 정의 구조, Protected Terms, Literal Content를 번역에서 분리합니다. Editor를 열면 최초 Original→UI 언어 번역만 자동 실행되고 **자동 번역**은 기본 OFF를 유지합니다. ON이면 입력 중지 1초 후 동기화하고, OFF이면 **번역 업데이트**를 사용합니다. revision 확인으로 오래된 응답을 버립니다. **적용**하면 Profile 출력 언어 쪽 Original이 Prompt로 돌아가며 현재 Builtin에서는 영어입니다. UI 언어 변경은 앱 재시작 후 적용됩니다.

## 9. AI 채팅 및 Vision

AI 채팅은 프롬프트 생성 GGUF를 공유하거나 별도 Chat GGUF를 사용할 수 있습니다. 동시에 model 하나만 상주하며 용도를 바꿀 때 현재 앱 소유 server를 안전하게 종료한 뒤 필요한 model을 로드합니다. 대화 Context는 현재 chat session 동안 유지됩니다.

이미지 인식은 유효한 Chat model과 일치하는 `mmproj`를 설정해야 합니다. **+ 이미지** 또는 Drag & Drop으로 PNG, JPG／JPEG, 정적 WebP를 첨부할 수 있습니다. animated WebP는 지원하지 않습니다. 정적 WebP는 memory에서 PNG로 정규화한 뒤 localhost llama-server로 보냅니다.

**분석**은 일반 Chat 답변을 만들고 **프롬프트 참조 분석**은 Prompt Generation에서 재사용하기 쉬운 model-independent 정보를 만듭니다. transfer preview를 확인하고 편집한 다음 **프롬프트 보충으로 보내기**로 요청, 전체 보충, 시작 이미지 보충 또는 종료 이미지 보충에 추가합니다.

이미지, Chat 내용, Prompt는 외부 cloud로 전송하지 않고 앱이 관리하는 localhost llama-server에서만 처리합니다.

## 10. 프롬프트 라이브러리

프롬프트 라이브러리는 기록과 독립적입니다. record에는 Title, Model, Task, Tags, 정확한 Prompt 본문이 들어갑니다. 즐겨찾기와 기존 Tag 추천으로 Tags를 재사용합니다. Model, Task, Title, Tags로 검색하며 여러 Tag는 AND 조건입니다. Tag 선택 시 자동 검색합니다. 마지막 Tag를 해제할 때는 전체 record를 자동 표시하지 않으며 필요할 때 **검색**을 누릅니다.

결과 row를 선택하면 프롬프트 세부 정보를 표시합니다. metadata 편집은 Title／Tags만 변경하고 Prompt 본문, Model, Task, 식별자는 변경하지 않습니다. 단일 복사, 체크한 여러 record의 **선택한 프롬프트 복사**, 삭제 확인을 지원합니다. **설정**에서 태그 행 수, 결과 행 수, 프롬프트 세부 정보 최소 행 수를 조정합니다.

## 11. Prompt Library Datasets

기존 `data/prompt_library.sqlite3`는 **Default Dataset**으로 남고 자동 이동하지 않습니다. **새 데이터세트**로 독립 Library를 만들고 selector로 전환합니다. **데이터세트 불러오기**는 유효한 SQLite Library를 관리되는 복사본으로 가져오고 **데이터세트 내보내기**는 portable database 복사본을 만듭니다. active Dataset은 기억됩니다.

Prompt, Tags, 즐겨찾기 Tag 상태는 Dataset마다 분리됩니다. Dataset merge는 구현되지 않았습니다.

v3.0.0에서 이전할 때는 두 앱을 종료하고 새 버전의 **프롬프트 라이브러리 → 데이터세트 불러오기**에서 이전 `data/prompt_library.sqlite3`를 선택하세요. source DB는 검증되고 이동되지 않습니다. 이전 release 폴더를 덮어쓰지 말고 backup으로 남기세요. Library 이전은 Dataset export／load를 사용합니다. Settings, History, 추가 Datasets 전체에 대한 범용 자동 migration 또는 merge가 없으므로 개별 내부 file 복사가 지원된다고 가정하지 마세요.

## 12. ComfyUI 연동

ComfyUI 연동은 선택 사항입니다.

1. ComfyUI를 종료하고 동봉된 `ComfyUI-Bridge/MMH3PromptBridge`를 `ComfyUI/custom_nodes/MMH3PromptBridge`로 복사합니다.
2. ComfyUI를 재시작하고 browser를 열거나 새로 고칩니다.
3. Local Prompt Studio의 **설정 → ComfyUI 연동**을 엽니다.
4. 로컬에서는 `http://127.0.0.1:8188`을 유지하고 **연결 테스트**를 누릅니다.
5. **ComfyUI와 Pairing**을 누르고 양쪽 6자리 code가 일치할 때만 ComfyUI에서 **Allow**를 누릅니다.
6. ComfyUI의 대상 text／Prompt node를 오른쪽 클릭하고 **MMH3 Prompt Bridge → Set as MMH3 Target**에서 STRING／multiline STRING widget을 선택합니다.
7. Prompt를 생성하거나 편집한 후 **ComfyUI로 보내기**를 누릅니다.

전송은 현재 browser workflow의 선택 widget 문자만 바꿉니다. workflow를 queue하거나 생성을 시작하거나 broadcast하지 않으며 `/prompt` API를 사용하지 않습니다. browser reload 또는 workflow／tab 변경 후 target을 다시 선택해야 할 수 있습니다. 원격 ComfyUI에는 HTTPS가 필수이며 인증 없이 internet에 공개하지 마세요. credential은 Windows DPAPI CurrentUser로 보호되고 수동 token 절차는 없습니다. Bridge browser JavaScript UI는 영어로 유지됩니다.

## 13. 설정 참고

- **LLM 모델 경로**: Prompt Generation GGUF
- **추론 백엔드**, **Vulkan 장치**, **CPU 스레드**, **GPU Offload**, **컨텍스트 크기**: llama.cpp 실행 및 메모리
- **Skill 위치**: MiniMax H3 Skill과 업데이트
- **기록**: 선택적 로컬 생성 기록, 기본 OFF
- **테마**, **UI 언어**: 외관과 locale. 언어는 재시작 후 적용
- **AI 채팅 모델**, `mmproj`: Prompt model 공유 또는 별도 Chat GGUF와 model별 image projection file
- **프롬프트 라이브러리**: Tags／결과 행 수와 Prompt 세부 정보 최소 행 수
- **ComfyUI 연동**: URL, 연결 테스트, Pairing

설정은 Portable 폴더에 저장되고 GGUF 파일을 변경하지 않습니다.

## 14. Portable 데이터, 개인정보 보호 및 Offline 사용

지속 데이터는 앱 폴더의 `data`에 저장됩니다.

- `data/config.json`
- `data/local-prompt-studio.log`
- `data/llama-server/`
- `data/history.sqlite3`
- `data/prompt_library.sqlite3`
- `data/prompt_library_datasets.json`
- `data/prompt_library_datasets/<dataset-id>/prompt_library.sqlite3`
- `data/skills/h3-prompt-writing/`
- `data/comfyui_credentials.dat`

앱 설정에 Registry를 사용하지 않고 Windows service나 Start Menu shortcut을 만들지 않습니다. 종료 후 압축 해제 폴더를 삭제하면 제거됩니다. telemetry, 광고, analytics가 없습니다. 일반 Prompt 생성은 선택한 GGUF와 `127.0.0.1`만 사용합니다. H3 Skill 가져오기／업데이트 확인, 공식 model page 열기, ComfyUI Test Connection／Pair／Send 등 명시한 작업만 network를 사용합니다. Request와 생성 Prompt를 일반 log에 저장하지 않습니다. ComfyUI credential은 DPAPI CurrentUser로 보호되며 공유하면 안 됩니다. 필요한 데이터를 준비하면 일반 생성은 offline으로 작동합니다.

## 15. 업데이트 및 백업

1. Local Prompt Studio를 종료하고 llama-server가 끝날 때까지 기다립니다.
2. 이전 release 폴더를 backup으로 남기고 새 ZIP을 그 위에 풀지 않습니다.
3. 새 버전을 별도의 쓰기 가능한 폴더에 풉니다.
4. 사용자 데이터는 이전 폴더의 `data`에 있습니다. 앱 종료 상태에서 backup하세요.
5. Prompt Library는 이전 버전의 **데이터세트 내보내기**와 새 버전의 **데이터세트 불러오기**로 이전합니다. v3.0.0의 `data/prompt_library.sqlite3`는 직접 불러올 수 있습니다.
6. 새 설치를 확인한 후 이전 폴더를 삭제합니다. Release ZIP, `SHA256SUMS.txt`, backup을 보관할 수 있습니다.

cloud synchronization, Dataset 자동 merge, 모든 Portable 데이터를 위한 범용 자동 migration은 제공하지 않습니다.

## 16. 문제 해결

- **설정을 쓸 수 없음**: 사용자 계정이 쓸 수 있는 폴더에 다시 풀고 ZIP 안에서 실행하지 마세요.
- **model 미설정**: 설정에서 기존 GGUF를 선택합니다. model은 복사되지 않습니다.
- **H3 Skill 미설정**: MiniMax H3에만 공식 Skill을 online으로 가져옵니다.
- **Vulkan 장치 미감지**: GPU 드라이버를 확인하거나 CPU로 전환합니다. 감지는 llama.cpp 결과를 따릅니다.
- **Context 부족**: 8192, 메모리가 충분하면 16384를 사용하고 필요하면 입력을 줄입니다.
- **RAM 부족**: 다른 앱을 닫고 Context를 낮추거나 더 작은 GGUF를 선택합니다.
- **생성이 느림**: CPU에서는 몇 분 걸릴 수 있으며 최대 대기는 300초입니다.
- **Cancel 후 종료 중**: 소유 server가 멈출 때까지 몇 초 기다립니다. 앱을 닫아도 자체 server만 종료합니다.
- **ComfyUI 연결 실패**: ComfyUI 실행, Bridge 설치, browser reload, URL을 확인합니다.

[GitHub Issues](https://github.com/tarou61300/Local-Prompt-Studio/issues)에 보고할 때 Windows 버전, CPU／GPU, GGUF 파일명, backend, Context Size, 정확한 error, 관련 `data/llama-server/` log를 포함하세요. 개인 Request, Prompt, credential 또는 개인정보는 게시하지 마세요.
