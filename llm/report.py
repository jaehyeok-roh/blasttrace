"""보고서 초안 (담당: 선우). 지금은 LLM 대신 문장 틀로 채운다.
Ollama 등으로 바꿀 때 함수 이름과 돌려주는 모양만 지키면 된다.
입력 record 예시: shared/record.json, 반환 예시: shared/report.json"""


def make_report(record: dict) -> dict:
    """반환: {"text": 보고서 초안, "model": 모델 이름, "prompt_version": 프롬프트 버전}"""
    lines = [f"[검사 보고서 초안] {record['zone_id']} ({record['location']}) · BLK {record['block']}", f"현재 상태: {record['status']}"]
    for hp in record.get("hold_points", []):
        lines.append(f"Hold Point {hp['hp']} {hp['name']}: " + (f"해제 · 승인 {hp['released']}" if hp["released"]
                                                                  else "대기 (검사원 승인 전 다음 공정 착수 금지)"))
    lines.append("")
    if not record["findings"]:
        lines.append("- 확정된 지적 없음")
    for f in record["findings"]:
        by = "검사원 직접 추가" if f["score"] is None else f"AI 점수 {f['score']:.2f}"
        lines.append(f"- {f['finding_id']} [{f['stage']} · {f['status']}] {f['note'] or '내용 미입력'} ({by}, {f['confirmed_at']})")
        for a in f["actions"]:
            lines.append(f"  · 조치: {a['note'] or '내용 미입력'} ({a['at']})")
        for r in f["rechecks"]:
            res = f"성공, 대응점 {r['matches']}개" if r["success"] else "실패, 검사원 수동 확인"
            lines.append(f"  · 재검사: 자동 정합 {res} ({r['at']})")
        if f.get("reworks"):
            lines.append(f"  · 재조치 {f['reworks']}회")
        if f["spectra"]:
            idx = " → ".join(str(s["rust_index"]) for s in f["spectra"])
            lines.append(f"  · 분광: 녹 지표 {idx}, 최근 판정 '{f['spectra'][-1]['label']}'")
    for r in record.get("recurrences", []):
        lines.append(f"- 도장 후 재발: 도장 전 {r['pre']} 자리에서 {r['post']} 발생 → 표면처리·조치 원인 확인 필요")
    for r in record.get("rejected_spots", []):
        lines.append(f"- 도장 후 {r['post']}: 도장 전에 반려한 AI 후보 자리 (반려 이유: {r['reason'] or '미입력'}) → 반려 판단 재확인 필요")
    lines += ["", "※ 조도·염분·도막두께 등 계측값은 계측기 기록을 따른다.",
              "※ 이 초안은 검사원이 검토한 뒤 확정한다."]
    return {"text": "\n".join(lines), "model": "template-v0", "prompt_version": "v0"}
