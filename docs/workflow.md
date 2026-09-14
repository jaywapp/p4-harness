# 작업과 복구

## 시작과 범위

1. `doctor`, `status`로 client/root와 현재 작업을 확인합니다. 조사만 한다면 task나 checkout이 필요하지 않습니다.
2. 수정할 소스와 관련 테스트를 읽고 작은 scope를 선택합니다. `--scope src/component --scope tests/component`처럼 복수 지정할 수 있습니다.
3. 기존 열린 파일이나 offline 변경이 scope 안에 있으면 보존하고 더 좁은 scope를 선택합니다. 기존 CL을 자동 reopen하거나 파일을 원복해 시작 조건을 만들지 않습니다.
4. `begin`으로 작업 CL을 생성합니다. ID는 영문·숫자·하이픈·밑줄 1~80자이고 이전 ID는 재사용하지 않습니다.

큰 depot 전체를 scope로 잡으면 `have`와 reconcile preview 비용이 커집니다. 기본 tracked 파일 한도는 20,000개이며 작업 단위로 줄이는 것이 우선입니다. 이미 존재하는 디렉터리는 디렉터리 scope, 존재하지 않는 경로는 개별 파일 scope로 기록됩니다. 새 디렉터리에 여러 파일을 만들려면 기존 상위 디렉터리 또는 각 새 파일을 지정합니다.

## 편집과 검토

지원 native 편집은 훅에서 checkout합니다. shell 편집은 `prepare` 후 수행합니다. `chmod`/`attrib`로 읽기 전용 표시를 지우는 방식은 사용하지 않습니다. 읽기 전용 속성을 자동 복구할 필요 없이 P4가 checkout을 담당합니다.

`collect`는 예약된 새 파일만 add합니다. `changes`는 task CL의 모든 opened 파일이 scope·예약 목록에 속하는지, 다른 CL의 파일이 섞이지 않았는지, unresolved/offline 변경이 남지 않았는지 확인하고 manifest를 반환합니다.

manifest에는 파일 경로·depot path·P4 action·type·have revision·SHA-256이 있습니다. manifest 자체가 코드 리뷰를 대신하지는 않습니다. 기존 파일은 해당 파일의 `p4 diff -du <file>`과 내용을 읽고, add 파일은 전체 내용, delete/move 파일은 의도와 참조 영향을 확인합니다. 전체 workspace diff에 다른 사용자 작업이 섞이지 않게 파일 범위를 명시합니다.

`delete`는 깨끗한 tracked 파일만 처리합니다. 수정 중인 파일을 삭제해야 한다면 우선 그 수정의 처리 방향을 정해야 하므로 거절됩니다. `move`는 source를 작업 CL로 준비하고 기존 목적지를 덮어쓰지 않습니다. file type과 `+l` 같은 독점 checkout 정책은 서버의 결정을 따릅니다.

## 검증과 완료

`verify`는 collect 후 snapshot을 만들고 설정된 argv를 실행합니다. 출력 전체는 로그에, 마지막 일부는 JSON 결과에 남깁니다. 종료 코드가 0이고 실행 전후 상태가 같을 때만 passed입니다. timeout·실패·실행 중 변경은 기록되고 완료 조건을 충족하지 못합니다.

`finish`는 `required_checks` 전체의 status·snapshot·profile이 현재 상태와 일치해야 성공합니다. 필수 검증을 설정하지 않았다면 `not_configured`라고 명시합니다. 코드가 바뀐 뒤 지난 테스트 통과를 재사용하지 않습니다. 검증 작업은 소스를 자동 format하는 명령과 나누는 것이 좋습니다.

완료 결과에는 task ID, CL, snapshot ID, verification, report 경로가 포함됩니다. 보고서는 `.p4-harness/state/reports/<task>.json`에 저장됩니다. 파일과 pending CL은 남고 active pointer만 해제됩니다. 제출 후 같은 범위의 새 작업을 시작할 수 있습니다. 제출 전에는 이전 pending 파일 때문에 새 작업의 clean-scope 검사가 거절될 수 있습니다.

## Claude와 Codex 인계

`handoff --to codex --note "남은 검토와 실행할 테스트"`는 같은 물리 파일·CL·검증 기록을 유지하고 소유자와 session binding을 변경합니다. 반대 방향도 같습니다. 받는 쪽은 `status`와 관련 코드를 확인하고 이어갑니다. 완료 후 단순 대화 내용을 옮기는 것과 달리 작업 범위·CL·snapshot은 디스크에 남습니다.

두 CLI를 모두 켜 둘 수는 있지만, 이전 owner의 native 편집은 거절됩니다. 일반 shell/MCP까지 완전히 차단하는 장치는 아니므로 실제 동시 작성은 별도 client/root로 격리합니다. 같은 제품의 두 번째 세션도 자동으로 작성 소유권을 가져가지 않습니다.

## 문제별 복구

| 상태 | 처리 |
|---|---|
| 로그인 만료·SSL·네트워크 오류 | 기존 P4 도구에서 연결을 정상화하고 `doctor` 후 재시도. 하네스는 인증 실패를 clean으로 취급하지 않음 |
| 다른 CL의 opened 파일 | 소유 작업을 확인하고 scope를 조정. 자동 reopen/revert 없음 |
| 다른 client의 `+l` checkout | 서버의 lock 소유자와 기존 절차로 해결한 뒤 재시도. 강제 unlock 없음 |
| checkout 일부 성공 후 나머지 실패 | `status`, `changes`, P4 opened를 확인. 성공한 checkout은 보존하며 같은 prepare 재시도 가능 |
| 새 파일을 예약 전에 써 버림 | 파일을 안전한 별도 위치에 보존한 후 operator가 처리 방향 결정. 미등록 파일을 자동 task 소유로 채택하지 않음 |
| task 도중 sync·view·client·CL 상태 변화 | 변경 원인과 로컬 수정부터 확인. 기록된 기준을 임의 수정해 통과시키지 않음 |
| 테스트 timeout·실패 | 로그 확인, 코드 수정 후 동일 profile 재실행 |
| 테스트 중 source 변경 | formatter/generator 실행을 분리하고 안정된 상태에서 재검증 |
| 컨텍스트 압축·세션 중단 | 상태는 유지됨. `pause --note ...` 후 `resume --agent ...`로 명시적 재개 |
| 프로세스 강제 종료 | OS lock은 해제됨. 재실행 후 task와 opened 파일을 확인. lock 파일 자체를 지울 필요 없음 |
| shelf 생성 중 실패 | pending 파일 보존. 서버의 해당 shelf와 `changes`를 확인 후 명시적으로 재시도 |

`resume`은 로그인·client 검사를 거쳐 세션 binding을 초기화하지만 파일이나 baseline을 덮어쓰지 않습니다. 운영자가 활성 세션 교체를 위해 실행할 수 있으므로 실제 이전 작성 세션을 먼저 멈추세요.

상태 JSON과 서버 변경은 하나의 원자적 트랜잭션이 아닙니다. CL 생성 직후 프로세스가 죽으면 서버에 pending CL만 남을 수 있습니다. 재시작 시 P4의 pending CL 설명에서 `[p4-harness:<task>]`를 찾아 중복 생성을 확인하세요. 상태 파일 손실 시 임의 복구·자동 cleanup을 제공하지 않으며, 백업과 실제 P4 상태를 대조해 복구합니다.
