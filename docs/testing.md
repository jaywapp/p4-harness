# 테스트와 설치 확인

## 자동 테스트

외부 Python 패키지 없이 실행합니다.

```bash
python3 -m unittest discover -s tests -v
```

`P4_BIN`, `P4D_BIN`이 없으면 실제 서버 테스트는 skip됩니다. Linux에서 공식 P4/P4D 바이너리를 별도 폴더에 준비한 경우:

```bash
P4_BIN=/absolute/path/to/p4 P4D_BIN=/absolute/path/to/p4d python3 -m unittest discover -s tests -v
```

서버 fixture는 매 테스트마다 새 임시 database·client root·ticket 경로와 임의의 `127.0.0.1` 포트를 사용합니다. 현재 사용자의 P4 server를 사용하지 않습니다. 2026.1 신규 서버의 보안 기본값 때문에 **테스트 전용 새 loopback 서버**에만 bootstrap 설정을 적용합니다. 이 설정을 회사 서버 설치 절차로 사용하지 않습니다. [P4 서버 security levels](https://help.perforce.com/helix-core/server-apps/p4sag/current/Content/P4SAG/security-levels.html).

검증 범위:

- 오류 record, argv/form protocol, 물리 경로·scope·설정 보호
- 기존 설정 보존, 재설치, read-only instruction, Windows hook command quoting
- 실제 checkout·CL·add·delete·move·shelf와 기존 변경 보존
- Claude→Codex 인계, 같은 provider의 중복 세션, event cwd 처리
- 실제 subprocess hook deny JSON과 P4IGNORE/P4CONFIG 우선순위
- 검증 stale·실패·timeout, 변경된 have revision, 다른 CL·독점 checkout 충돌
- 공백·한글·Perforce 특수 문자가 포함된 파일
- 요약 페이지에서도 전체 action 총계 보존, full schema, 파일 내용/action/type/revision 비교, 손상·다른 task manifest 거절
- 실패 로그의 앞쪽 원인·긴 행·반복 진단·스캔 한도·알 수 없는 형식, 원본 로그 보존과 성공 시 로그 생략
- 최신 context의 파일/profile 변경 stale, 인계 메모 생략 표시, 조회 중 소스/CL/소유권 보존
- 필수 검증 일괄 실행의 중단·미실행 목록·snapshot 불일치, 빈 검증 목록의 not_configured
- 재설치 시 사용자가 채운 project map 보존

합성 200개 파일의 요약과 전체 manifest는 JSON 직렬화 바이트 크기도 비교합니다. 실제 모델 토큰·청구 비용·생산성 측정은 아니며 [측정 방법](token-efficiency.md#효과를-확인하는-방법)을 따로 구분합니다.

GitHub Actions는 Ubuntu/Windows에서 단위 테스트, Ubuntu에서 공식 r26.1 `p4/p4d`를 내려받아 통합 테스트를 실행합니다. r26.1 다운로드 경로는 해당 release 채널의 최신 패치로 바뀔 수 있으므로 CI 로그에 실제 버전을 남깁니다.

## 실제 제품에서 설치 확인

자동 테스트는 hook protocol과 코어를 검사합니다. **로그인된 Claude Code와 Codex가 훅을 실제로 로딩하는지**는 각 로컬 설치에서 아래 순서로 확인합니다.

1. 별도 테스트 P4 client/root에서 install·doctor를 실행하고 CLI 버전과 `p4 -V`를 기록합니다.
2. `launch claude`로 실행하고 `/hooks`에서 설치 명령을 확인합니다. 아직 task가 없을 때 작은 tracked 파일에 편집을 요청하면 PreToolUse가 거절해야 합니다.
3. clean scope에 `begin --agent claude ...` 후 같은 파일을 수정합니다. `p4 opened`의 CL이 task CL과 같고 `changes`에 파일이 보여야 합니다.
4. `handoff --to codex --note ...` 후 `launch codex`로 실행합니다. 프로젝트·훅 정의를 검토하고 `/hooks`에서 신뢰를 설정합니다. 설치 후 새로 신뢰했다면 세션을 다시 시작합니다.
5. Codex `apply_patch`로 같은 파일을 수정합니다. 동일 CL에 남아야 하며 이전 Claude 세션의 native 편집은 거절되어야 합니다.
6. 반대 방향 인계도 확인합니다. 등록한 check를 verify하고 파일을 바꾼 뒤 finish가 stale로 거절되는지 확인합니다.
7. 다시 검증해 finish합니다. 결과가 required check 설정에 맞고 P4에 pending CL이 남는지 확인합니다. 테스트 변경의 제출·정리는 기존 P4 절차를 따릅니다.

관리 설정이 project hook을 비활성화하거나 제품 버전이 event를 지원하지 않으면 자동 checkout을 검증한 것으로 보지 않습니다. 훅 설정 변경으로 신뢰가 해제될 수 있으므로 하네스 업데이트 후에도 확인합니다. [Codex hooks](https://developers.openai.com/codex/hooks), [Claude hooks](https://code.claude.com/docs/en/hooks).
