# p4-harness

**Claude Code와 Codex가 같은 Perforce 작업 규칙·CLI·상태를 사용하는 하네스.**

파일 수정 전 checkout, 작업별 numbered changelist, 명시적 작업 범위, 검증 결과와 파일 상태의 연결, 두 에이전트 사이의 인계를 구현합니다. 특정 엔진·언어·빌드 도구에 종속되지 않습니다. Python 3.11+ 표준 라이브러리로 실행하며 별도 MCP 서버나 모델 API 키가 필요하지 않습니다.

이 Git 저장소는 **하네스 코드의 배포·개발 장소**입니다. 실제 소스 작업은 기존 **P4 client workspace**에서 수행합니다. P4 워크스페이스를 Git으로 변환하지 않습니다.

작업 계획·JSONL 변경 이력·Claude 단계별 스킬·Codex 리뷰 핑퐁은 별도 [jsonl-prr](https://github.com/jaywapp/jsonl-prr)에서 관리합니다. `jsonl-prr`는 형상관리와 독립적이고, 이 저장소는 P4 파일 준비·CL·검증·인계에 집중합니다. 함께 사용할 때의 순서와 제한은 [역할 경계](docs/workflow-boundary.md)에 정리했습니다.

## 제공하는 기능

| 기능 | Claude Code | Codex |
|---|---|---|
| 공통 규칙 | `CLAUDE.md` import | `AGENTS.md` 안내 |
| 공통 작업 skill | `.claude/skills/p4-work` | `.agents/skills/p4-work` |
| 편집 전 준비 훅 | `Edit`, `Write`, `MultiEdit`, `NotebookEdit` | `apply_patch`의 Add/Update 파일 |
| 작업 상태·CL·검증 | 동일한 Python CLI | 동일한 Python CLI |
| 인계 | `handoff --to codex` | `handoff --to claude` |
| 세션 시작·종료·압축 | 작업 상태 안내 | 작업 상태 안내 |

- **기존 작업 보존:** 범위 안에 기존 opened/offline 변경이 있으면 시작을 거절합니다. 범위 밖의 사용자 변경은 그대로 둡니다.
- **작업 소유권:** 한 물리 워크스페이스에 active task 하나와 작성 세션 하나를 둡니다. CL 번호만 나눠서는 동시 편집이 격리되지 않습니다.
- **명시적 P4 작업:** `prepare`, `collect`, `delete`, `move`, `shelve`를 제공합니다. 전체 workspace reconcile을 적용하지 않습니다.
- **실제 파일에 연결된 검증:** 파일 해시·have revision·CL action·check 설정이 달라지면 이전 검증으로 완료할 수 없습니다.
- **필요한 결과부터 조회:** 기본 변경 요약, 이전 snapshot 이후 파일 비교, 실패 로그 선별, 짧은 인계 context, 필수 검증 일괄 실행을 제공합니다. 원본 manifest·로그는 보존합니다.
- **검토 가능한 완료:** `finish`는 JSON 보고서를 만들고 pending CL을 남깁니다. submit은 기존 팀 절차에서 수행합니다.

## 빠른 시작

준비물은 Python 3.11+, `p4`, 사용할 Claude Code/Codex CLI입니다. 기존 P4 연결·로그인·SSL trust가 정상인 **실제 client root**에 설치합니다. Codex는 현재 공식 문서의 `hooks.json` / `PreToolUse`를 지원하는 버전이 필요합니다.

Windows PowerShell 예시입니다. 경로·client·server·user는 본인 환경으로 바꾸세요.

```powershell
git clone https://github.com/jaywapp/p4-harness.git C:\Tools\p4-harness
python C:\Tools\p4-harness\p4h.py --workspace D:\P4\project install --client my-client --port ssl:p4.example.com:1666 --user my-user --dry-run
python C:\Tools\p4-harness\p4h.py --workspace D:\P4\project install --client my-client --port ssl:p4.example.com:1666 --user my-user
python C:\Tools\p4-harness\p4h.py --workspace D:\P4\project doctor
python C:\Tools\p4-harness\p4h.py --workspace D:\P4\project launch claude
```

Codex 실행:

```powershell
python C:\Tools\p4-harness\p4h.py --workspace D:\P4\project launch codex
```

macOS/Linux도 같은 진입점을 사용합니다.

```bash
git clone https://github.com/jaywapp/p4-harness.git "$HOME/tools/p4-harness"
python3 "$HOME/tools/p4-harness/p4h.py" --workspace "$HOME/p4/project" install --client my-client
python3 "$HOME/tools/p4-harness/p4h.py" --workspace "$HOME/p4/project" doctor
python3 "$HOME/tools/p4-harness/p4h.py" --workspace "$HOME/p4/project" launch codex
```

`--port`, `--user`를 생략하면 기존 P4 설정을 사용합니다. 설치된 훅은 설치 시 사용한 Python과 하네스의 절대 경로를 기록하므로 해당 경로를 유지하세요. 기존 설정은 보존하고 변경 전 파일은 `.p4-harness/backups/`에 백업합니다. 기존 instruction/settings 파일이 read-only이면 구성 변경용 CL에 먼저 checkout해야 합니다.

**첫 실행에서 훅 로딩을 확인하세요.** Codex에서는 프로젝트 신뢰 설정과 `/hooks`에서 새 훅 정의의 검토·신뢰가 필요합니다. Claude Code에서도 `/hooks`에서 `p4-harness` 명령을 확인합니다. 런처는 신뢰 설정이나 기존 도구 권한을 자동 승인하지 않습니다. 설정을 바꾼 뒤에는 세션을 다시 시작합니다. [Codex 훅 문서](https://developers.openai.com/codex/hooks), [Claude Code 훅 문서](https://code.claude.com/docs/en/hooks).

## 일상 작업

아래의 `p4h`는 위의 `python .../p4h.py --workspace ...` 명령 접두어를 짧게 쓴 것입니다. 가상환경에서 `python -m pip install -e /path/to/p4-harness`로 설치하면 실제 `p4h` 명령도 사용할 수 있습니다.

```text
p4h doctor
p4h begin --task fix-parser --agent claude --scope src/parser --scope tests/parser --goal "파서 오류 수정"
p4h prepare src/parser/reader.py
```

이후 Claude/Codex가 파일을 수정합니다. 지원되는 native 편집은 훅이 `prepare`를 수행합니다. shell·지원하지 않는 편집 도구를 사용할 때는 먼저 파일별 `prepare`를 실행합니다. 새 파일도 **쓰기 전에** 경로를 예약합니다.

```text
p4h collect
p4h verify --required
p4h handoff --to codex --note "파서 수정 완료. 경계값 테스트를 검토하고 마무리할 것."
```

collect의 파일/action 목록과 관련 코드를 검토한 뒤 verify합니다. 출력이 잘렸다면 `changes --offset <next_offset>` 또는 `changes --full`로 필요한 전체 목록을 확인합니다. 나중에 같은 작업의 파일 변화를 다시 볼 때는 `changes --since <snapshot_id>`를 사용합니다.

Codex를 실행해 `context`의 목표·범위·인계 메모·현재 검증 상태를 읽고 같은 CL에서 이어갑니다. 수정했다면 검증을 다시 실행합니다.

```text
p4h context
p4h verify --required
p4h shelve
p4h finish
```

`shelve`는 선택적인 명시적 체크포인트입니다. 작업을 중단할 때는 `pause --note "다음 작업"`, 재개할 때는 `resume --agent claude|codex`를 사용합니다.

검증 명령은 설치된 `.p4-harness/config.json`에 프로젝트에 맞게 등록합니다. 예를 들어 Python 프로젝트라면:

```json
{
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

이는 **기존 config에 병합할 항목**입니다. root/client 등의 기존 값을 지우지 마세요. 초기 상태에는 프로젝트 테스트를 추측해 넣지 않으며 `finish` 결과는 `verification: not_configured`입니다. [설정과 명령](docs/configuration.md), [작업·복구 절차](docs/workflow.md)를 참고하세요.

0.2부터 `collect`, `changes`, `verify`는 기본 요약 출력입니다. 기존 상세 JSON을 사용하는 자동화에는 `--full`을 지정합니다. Python 도구의 역할, 토큰 절감 아이디어·구현 범위·측정 방법은 [토큰 효율 설계](docs/token-efficiency.md)에 정리했습니다. 실제 토큰·비용 절감률은 아직 측정하지 않았습니다.

## 적용 범위와 검증

훅은 지원되는 편집을 준비하고 흔한 잘못된 P4 명령을 거절하는 협업 장치입니다. 임의 shell 스크립트·MCP·외부 편집기까지 통제하는 OS 보안 경계는 아닙니다. 일반 CLI 권한·조직 P4 권한을 유지하고, 동시에 작성해야 하는 작업은 별도 client와 물리 root를 사용합니다.

이 버전은 Linux의 실제 임시 P4/P4D 2026.1 서버를 이용한 통합 테스트와 단위 테스트를 제공합니다. Claude/Codex 훅 JSON 입력·출력도 테스트합니다. 두 제품의 로그인된 실제 CLI 세션과 회사 서버 환경은 별도 설치 후 확인 대상입니다. CI는 Linux/Windows Python 테스트와 Linux 실제 P4 통합 테스트를 실행합니다.

- [구조와 설계 결정](docs/architecture.md)
- [설정·설치 파일·플랫폼 차이](docs/configuration.md)
- [작업·검증·인계·복구](docs/workflow.md)
- [토큰 절감 아이디어·반영 기능·기대효과](docs/token-efficiency.md)
- [테스트 실행과 실제 CLI 확인](docs/testing.md)

## 하네스 개발

```bash
python3 -m unittest discover -s tests -v
```

P4 바이너리를 지정하지 않으면 서버 통합 테스트는 skip됩니다. 실제 서버 테스트 방법은 [testing.md](docs/testing.md)에 있습니다. 이 저장소의 개발 규칙은 [AGENTS.md](AGENTS.md)에 있고, 대상 P4 워크스페이스용 규칙은 `src/p4_harness/templates/`에서 관리합니다.
