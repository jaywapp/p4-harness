# 설정과 명령

## 설치되는 파일

| 대상 workspace 경로 | 내용 |
|---|---|
| `.p4-harness/config.json` | root/client/P4 명령/검증 profile |
| `.p4-harness/rules.md` | 공통 작업 규칙과 실제 CLI 경로 |
| `.p4-harness/state/` | active task, task history, manifest, 로그, 보고서 |
| `.p4-harness/backups/` | 설치 시 변경한 기존 파일의 사본 |
| `.p4-harness.p4ignore` | 설치 산출물을 위한 ignore 규칙 |
| `AGENTS.md` | 공통 규칙을 읽으라는 관리 블록을 병합 |
| `CLAUDE.md` | 공통 규칙 import 관리 블록을 병합 |
| `.agents/skills/p4-work/SKILL.md` | Codex skill |
| `.claude/skills/p4-work/SKILL.md` | 같은 내용의 Claude Code skill |
| `.codex/hooks.json` | Codex 훅 |
| `.claude/settings.local.json` | 기존 설정에 Claude Code 훅 병합 |

계정 ticket/password는 저장하지 않습니다. `.p4-harness` 상태에는 로컬 경로·작업 내용·테스트 출력이 있으므로 소스 depot의 일반 산출물로 추가하지 않습니다. 기존 tracked instruction/settings 파일은 ignore해도 추적 상태가 유지됩니다.

설치는 반복 가능하며 기존 사용자 훅과 permissions를 보존합니다. 하네스가 소유하는 블록/훅은 갱신합니다. 수정되는 기존 파일은 백업하고, 잘못된 JSON·symlink·read-only 파일을 발견하면 쓰기 전에 거절합니다. 기존 설치의 root/client/port/user 변경을 자동 덮어쓰지 않습니다. 하네스 원본 경로나 Python 경로가 바뀌면 같은 workspace에 재설치해 훅 경로를 갱신합니다.

## 공통 config

```json
{
  "version": 1,
  "workspace_root": "D:\\P4\\project",
  "client": "my-client",
  "port": "ssl:p4.example.com:1666",
  "user": "my-user",
  "p4_command": ["C:\\Program Files\\Perforce\\p4.exe"],
  "p4_timeout_seconds": 20,
  "max_scope_files": 20000,
  "checks": {
    "unit": {
      "argv": ["python", "-m", "unittest", "discover", "-s", "tests"],
      "cwd": ".",
      "timeout_seconds": 600
    }
  },
  "required_checks": ["unit"]
}
```

`workspace_root`는 `p4 info`의 실제 clientRoot와 같아야 합니다. `port/user`가 `null`이면 기존 P4CONFIG·환경·ticket 설정을 사용하되, 작업 시작 시 확인한 실제 환경이 이후 바뀌면 작업을 거절합니다. `p4_command`에는 실행 파일과 필요한 argv만 넣고 password를 넣지 않습니다.

P4 client 통신 출력은 UTF-8 기준으로 처리합니다. Unicode server에 필요한 `P4CHARSET`은 기존 P4 설정을 유지합니다. legacy 비 UTF-8 경로·다중 root/overlay mapping·symlink를 통한 편집은 별도 검증이 필요하며, 모호한 경로는 거절합니다.

검증은 shell 없이 실행합니다. `argv`는 문자열 배열이며 shell 연산자 `&&`, `|`, `>`를 넣는 자리가 아닙니다. 복잡한 검증은 프로젝트 소유의 script를 만들고 해당 script를 호출하세요. Windows의 `.cmd`/`.bat` 도구는 해당 도구가 요구하는 interpreter 호출을 profile에 명시합니다. 코드가 아닌 파일 수정으로 실행될 수 있으므로 config 변경은 일반 소스 작업과 분리해 검토합니다.

## P4IGNORE와 P4CONFIG

런처와 CLI는 기존 P4IGNORE 목록에 설치 ignore 파일의 절대 경로를 추가한 **프로세스 환경**을 만듭니다. 전역 `p4 set`이나 기존 ignore 파일은 수정하지 않습니다.

P4CONFIG가 직접 P4IGNORE를 지정하면 그 값이 환경 변수보다 우선합니다. 하네스의 reconcile preview는 자체 설치 산출물의 untracked add를 추가로 제외하므로 이 경우도 작동합니다. 실제 P4V/사용자 reconcile에서도 제외하려면 기존 P4IGNORE 설정에 아래처럼 설치 파일을 추가하세요. 설정 파일이 depot에서 관리된다면 구성 변경 CL에서 수정합니다.

```text
P4IGNORE=.p4ignore;p4ignore.txt;D:/P4/project/.p4-harness.p4ignore
```

기존 ignore 목록·부정 패턴은 유지해야 합니다. 하네스의 추가 제외는 자체 설치 파일에만 적용하며 tracked edit/delete는 숨기지 않습니다. [P4IGNORE 공식 문서](https://help.perforce.com/helix-core/server-apps/cmdref/current/Content/CmdRef/P4IGNORE.html).

## 두 CLI의 차이

Codex 런처는 다음 세션 인자를 추가합니다.

```text
-c project_root_markers=[".p4-harness"] -c features.hooks=true
```

P4 root에서 시작해 Git 저장소 없이 프로젝트 규칙을 탐색합니다. `.codex/config.toml`이나 전역 trust를 덮어쓰지 않습니다. `/hooks`에서 새 정의를 검토하고 신뢰한 뒤 사용합니다. 설치된 버전이 해당 훅을 지원하지 않으면 업그레이드하고 확인하세요. 관리 정책이 project hooks를 금지한 환경은 조직의 hook 배포 절차를 사용합니다. [Codex hooks](https://developers.openai.com/codex/hooks), [AGENTS.md 탐색](https://developers.openai.com/codex/guides/agents-md).

Claude는 `CLAUDE.md`에서 `.p4-harness/rules.md`를 import합니다. `.claude/settings.local.json`에 훅을 병합하며 `AGENTS.md` 자동 로딩을 전제로 하지 않습니다. [Claude memory](https://code.claude.com/docs/en/memory).

두 어댑터 모두 `SessionStart`, `PreToolUse`, `PostToolUse`, `PreCompact`, `Stop`을 연결합니다. PreToolUse 성공 시 강제 allow를 반환하지 않아 기존 권한 판단이 유지됩니다. 실패하면 deny JSON을 반환합니다. Windows 명령은 PowerShell의 literal argv로 인코딩해 공백·따옴표가 있는 설치 경로를 처리합니다. Codex용 `commandWindows`도 생성합니다.

## 명령 목록

전역 `--workspace`는 subcommand **앞**에 둡니다. 생략하면 현재 경로에서 상위 `.p4-harness/config.json`을 탐색합니다. 경로 인자는 명령 호출 디렉터리와 무관하게 설치된 root 기준입니다. native 훅의 상대 경로는 이벤트의 `cwd` 기준으로 해석합니다.

| 명령 | 효과 |
|---|---|
| `install --client ... [--dry-run]` | 두 어댑터 설치/변경 예정 목록 |
| `doctor` | 실제 root/client/view와 CLI 위치 확인 |
| `status` | 작업·CL·범위·소유자·인계 메모 |
| `begin --task ID --agent claude\|codex --scope PATH --goal TEXT` | clean scope 검사 후 새 pending CL 생성; scope 반복 가능 |
| `prepare FILE ...` | 기존 파일 checkout / 새 경로 예약 |
| `collect` | 예약된 새 파일 add 후 snapshot 작성 |
| `changes` | 현재 등록된 변경 manifest; 새 파일 add는 수행하지 않음 |
| `delete FILE` | 깨끗하고 unopened인 tracked 파일 삭제 예약 |
| `move SOURCE DESTINATION` | P4 move action 유지; 새 목적지만 허용 |
| `verify PROFILE` | 명시된 검증 실행, 로그·상태 기록 |
| `handoff --to AGENT --note TEXT` | 같은 workspace/CL의 작성 소유권 인계 |
| `pause --note TEXT` | 파일을 보존하며 작업 일시 정지 |
| `resume --agent AGENT` | 명시적으로 현재 task를 재개하고 세션 소유권 초기화 |
| `shelve` | 현재 task CL을 shelf에 저장; 재실행 시 해당 shelf 갱신 |
| `finish` | required check 확인 후 보고서 생성, active 해제 |
| `launch claude\|codex -- ...` | root에서 CLI 실행; `--` 뒤 인자는 해당 CLI에 전달 |

일반 출력은 JSON이며 오류와 검증 실패/timeout/stale은 exit 1입니다. hook 명령은 provider protocol에 맞춰 deny도 JSON과 exit 0으로 반환합니다.
