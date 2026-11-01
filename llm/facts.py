"""보고서의 사실 줄 (담당: 선우). 지적 ID·상태·승인·분광 상태·계측 제한 문구는 여기서 기록 그대로 결정적으로 만든다.
LLM은 이 줄들을 바꾸지 못하고, 각 절 위에 붙는 요약 문장만 쓴다 (llm/report.py).
입력 record 예시: shared/record.json (서버 record(zid)). 선택 필드가 빠져 있어도 멈추지 않는다. record는 바꾸지 않는다."""
import json
import re

# 분광 모델이 제안하는 노출 표면 3종 (sensor/dataset.py CLASS_KO와 같은 이름. llm은 sensor를 import하지 않는다)
LABEL_KO = {"bare_metal": "무도장 정상 강재 표면", "rust": "노출된 녹 표면", "orange_paint": "주황색 도장 표면"}
SPEC_STATUS = {"model_not_trained": "모델 미학습 · 측정값만 저장 (표면 분류 없음)",
               "model_incompatible": "모델과 측정 형식 불일치 · 측정값만 저장 (표면 분류 없음)"}
# 작업자·검사원 메모에 이 말이 있어도 계측값이 기록된 것은 아니다
MEASURE_WORDS = ["염분", "도막두께", "도막 두께", "DFT", "조도", "습도", "이슬점", "온도"]
SECTIONS = {"overview": "구역 정보와 현재 상태", "history": "주요 지적 및 조치·재검사 이력",
            "same_spot": "도장 전후 같은 자리 · 반려 위치 확인 사항", "spectral": "분광 측정 요약 (보조 참고 · 검사 합격 판정 아님)",
            "remaining": "남은 확인 사항 및 Hold Point"}
NOTICE = ["※ 이 초안은 검사원이 검토한 뒤 확정한다.",
          "※ AI 후보 점수·사진 정합·분광 모델 제안·LLM 요약 문장은 검사 판정이 아니다."]
NEXT = {"Punch Open": "작업자 조치 대기", "조치 완료": "재검사 촬영 필요", "재검사 대기": "검사원 종결 판정 필요"}


def _s(v, empty=""):
    """한 줄 문자열. 메모의 줄바꿈이 '- '·'※ ' 같은 사실 줄을 흉내 내지 못하게 공백으로 편다"""
    v = "" if v is None else re.sub(r"\s+", " ", str(v)).strip()
    return v or empty


def _fid(f, i):
    return _s(f.get("finding_id"), f"지적{i + 1}")


def spec(s: dict) -> dict:
    """분광 측정 한 번 -> {kind, text, sim, model_simulated, at, label, reasons, warnings}.
    kind = ok / invalid_measurement / model_not_trained / model_incompatible / legacy(폐기된 rust_index) / unknown.
    서버 spectra() 모양과 sensor.spectral.analyze() 반환 모양을 둘 다 읽는다. 원시 프레임·보정값은 쓰지 않는다"""
    s = s if isinstance(s, dict) else {}
    st, sim, msim = s.get("status"), bool(s.get("sim")), bool(s.get("model_simulated"))
    q = s.get("quality") if isinstance(s.get("quality"), dict) else {}
    reasons = [_s(x) for x in s.get("reasons") or []] or [
        _s(r.get("message", r.get("code", ""))) if isinstance(r, dict) else _s(r) for r in q.get("reasons") or []]
    warnings = [_s(w) for w in q.get("warnings") or []]
    label = s.get("label")
    if st == "ok" and label:
        kind = "ok"
        text = f"모델 제안 {s.get('label_ko') or LABEL_KO.get(label, label)} ({label})" + (" · 시뮬레이션 모델" if msim else "") \
            + " · 검사원 확인 필요"
    elif st == "invalid_measurement":
        kind = st
        text = "측정 불량 → 재측정" + (f" ({reasons[0].split(' → ')[0]})" if reasons else "") + " · 표면 분류 없음"
    elif st in SPEC_STATUS:
        kind, text = st, SPEC_STATUS[st]
    elif st is None and "rust_index" in s:
        kind, text = "legacy", "폐기된 임시 규칙(ratio-v0) 형식 — 판정 근거로 쓰지 않음"
    else:
        kind, text = "unknown", "형식을 알 수 없는 기록 — 판정 근거로 쓰지 않음"
    if sim:
        text = "센서 시뮬레이터 값 · " + text
    return {"kind": kind, "text": text, "sim": sim, "model_simulated": msim and kind == "ok", "label": label if kind == "ok" else None,
            "at": _s(s.get("at")), "reasons": reasons, "warnings": warnings}


def build(record: dict) -> dict:
    """record -> {"title", "sections": {키: [사실 줄]}, "digest": LLM에 보낼 요약(JSON 문자열), "ids", "statuses", "hps"}.
    사실 줄이 없는 절(같은 자리 기록 없음, 분광 없음)은 빠진다"""
    zid, loc, blk, zst = (_s(record.get(k), "미기록") for k in ("zone_id", "location", "block", "status"))
    fs = [f for f in record.get("findings") or [] if isinstance(f, dict)]
    hps = [h for h in record.get("hold_points") or [] if isinstance(h, dict)]
    recs = [r for r in record.get("recurrences") or [] if isinstance(r, dict)]
    rejs = [r for r in record.get("rejected_spots") or [] if isinstance(r, dict)]
    closed = [f for f in fs if f.get("status") == "Close-out"]
    opened = [f for f in fs if f.get("status") != "Close-out"]
    n_pre = sum(f.get("stage") == "도장 전" for f in fs)
    n_post = sum(f.get("stage") == "도장 후" for f in fs)
    sec = {}

    sec["overview"] = [f"구역 상태: {zst} (구역 진행 단계 · 지적별 상태와 Hold Point 승인은 아래에 따로 적음)",
                       f"지적 {len(fs)}건 (도장 전 {n_pre} · 도장 후 {n_post}) · 종결(Close-out) {len(closed)}건 · 미종결 {len(opened)}건"
                       if fs else "확정된 지적 없음"]

    hist, digest_fs, specs, any_rc = [], [], {}, False
    for i, f in enumerate(fs):
        fid, note = _fid(f, i), _s(f.get("note"), "내용 미입력")
        if "score" not in f:
            by, src = "후보 출처 미기록", "미기록"
        elif f["score"] is None:
            by, src = "검사원 직접 추가", "검사원 직접 추가"
        else:
            try:
                by, src = f"AI 후보 점수 {float(f['score']):.2f}", "AI 후보 채택"
            except (TypeError, ValueError):
                by, src = "AI 후보 점수 형식 오류", "AI 후보 채택"
        conf = f", 확정 {f['confirmed_at']}" if f.get("confirmed_at") else ""
        hist.append(f"- {fid} [{_s(f.get('stage'), '단계 미기록')} · {_s(f.get('status'), '상태 미기록')}] {note} ({by}{conf})")
        acts = [a for a in f.get("actions") or [] if isinstance(a, dict)]
        rcs = [r for r in f.get("rechecks") or [] if isinstance(r, dict)]
        for a in acts:
            hist.append(f"  · 조치: {_s(a.get('note'), '내용 미입력')} ({_s(a.get('at'), '시각 미기록')})")
        if not acts:
            hist.append("  · 조치 기록 없음")
        for r in rcs:
            any_rc = True
            res = (f"사진 정합 성공 · 대응점 {r['matches']}개" if r.get("matches") is not None else "사진 정합 성공") \
                if r.get("success") else "사진 정합 실패 → 검사원 수동 확인"
            hist.append(f"  · 재검사: {res} ({_s(r.get('at'), '시각 미기록')})")
        if f.get("reworks"):
            hist.append(f"  · 재조치 {f['reworks']}회")
        if f.get("closed_at"):
            hist.append(f"  · 종결 {f['closed_at']}")
        sp = [spec(s) for s in f.get("spectra") or []]
        if sp:
            specs[fid] = sp
        digest_fs.append({"id": fid, "stage": _s(f.get("stage"), "미기록"), "status": _s(f.get("status"), "미기록"), "note": note,
                          "source": src, "confirmed_at": _s(f.get("confirmed_at")),
                          "actions": [{"note": _s(a.get("note"), "내용 미입력"), "at": _s(a.get("at"))} for a in acts],
                          "rechecks": [{"photo_alignment": "성공" if r.get("success") else "실패", "at": _s(r.get("at"))} for r in rcs],
                          "reworks": f.get("reworks") or 0, "closed_at": _s(f.get("closed_at")),
                          "spectral": [{"at": x["at"], "result": x["text"]} for x in sp]})
    if fs:
        hist.append("※ AI 후보 점수 = 다시 볼 곳을 고른 상대 점수 (결함 확정·합격 확률 아님) · 점수 없음 = 검사원 직접 추가")
    if any_rc:
        hist.append("※ 사진 정합 = 이전 사진과 위치를 맞춘 결과 (조치 성공·결함 제거·검사 합격 판정 아님)")
    if fs:
        sec["history"] = hist

    same = [f"- 도장 후 재발: 도장 전 {_s(r.get('pre'), '?')} 자리에서 {_s(r.get('post'), '?')} 발생 → 표면처리·조치 원인 확인 필요"
            for r in recs]
    same += [f"- 도장 후 {_s(r.get('post'), '?')}: 도장 전에 반려한 AI 후보 자리 (반려 이유: {_s(r.get('reason'), '미입력')})"
             " → 반려 판단 재확인 필요" for r in rejs]
    if same:
        sec["same_spot"] = same + ["※ 같은 자리 = 도장 후 결함 중심이 사진 정합으로 옮긴 도장 전 위치 안 (기록상 위치 대응이며 "
                                   "도장 아래 부식이나 원인을 확정하지 않음)"]

    if specs:
        lines, flat = [], [x for sp in specs.values() for x in sp]
        for fid, sp in specs.items():
            last = sp[-1]
            lines.append(f"- {fid} 분광 {len(sp)}회: 최근 {last['text']}" + (f" ({last['at']})" if last["at"] else ""))
            if len(sp) > 1:
                lines.append("  · 이전: " + " / ".join(f"{x['text']}" + (f" ({x['at']})" if x["at"] else "") for x in sp[:-1]))
            if last["warnings"]:
                lines.append("  · 품질 경고: " + " / ".join(last["warnings"]))
        if any(x["sim"] for x in flat):
            lines.append("※ 센서 시뮬레이터 값(지어낸 값)이 포함됨 · 실측 아님")
        if any(x["model_simulated"] for x in flat):
            lines.append("※ 시뮬레이션 모델(모의 데이터로 학습) 제안이 포함됨 · 실측 학습 모델 아님")
        if any(x["kind"] == "ok" for x in flat):
            lines.append("※ 모델 제안은 노출 표면 3종(bare_metal·rust·orange_paint) 분류일 뿐 · orange_paint라도 녹 제거나 "
                         "도막 아래 상태를 뜻하지 않음 · 모델 점수는 정답 확률 아님")
        lines.append("※ 보정값은 상대 반사 지표 (절대 반사율 아님) · 분광은 보조 참고이며 검사 합격 판정 아님")
        sec["spectral"] = lines

    rem = []
    for i, f in enumerate(fs):
        if f.get("status") == "Close-out":
            continue
        fid, st = _fid(f, i), _s(f.get("status"), "상태 미기록")
        rcs = [r for r in f.get("rechecks") or [] if isinstance(r, dict)]
        extra = " · 최근 재검사 사진 정합 실패 (검사원 수동 확인)" if rcs and not rcs[-1].get("success") else ""
        rem.append(f"- 미종결 {fid} [{st}] → {NEXT.get(st, '검사원 확인 필요')}{extra}")
    if fs and not opened:
        rem.append("- 미종결 지적 없음")
    if same:
        rem.append(f"- 같은 자리 기록 확인: 재발 {len(recs)}건 · 반려했던 자리 {len(rejs)}건")
    for fid, sp in specs.items():
        if sp[-1]["kind"] != "ok":
            rem.append(f"- {fid} 분광 최근 측정에 표면 분류 없음: {sp[-1]['text']}")
    for h in hps:
        name = f"Hold Point {_s(h.get('hp'), '?')} {_s(h.get('name'))}".rstrip()
        rem.append(f"- {name}: " + (f"해제 · 승인 {h['released']}" if h.get("released") else "대기 (검사원 승인 전 다음 공정 착수 금지)"))
    if not hps:
        rem.append("- Hold Point 기록 없음")
    rem.append("- 조도·염분·도막두께·환경 계측값: 이 기록에 없음 → 별도 계측 기록 확인 필요")
    for i, f in enumerate(fs):
        memo = " ".join(_s(t) for t in [f.get("note")] + [a.get("note") for a in f.get("actions") or [] if isinstance(a, dict)])
        words = [w for w in MEASURE_WORDS if w in memo]
        if words:
            rem.append(f"- {_fid(f, i)} 메모의 '{', '.join(words)}' 언급은 작업 내용 기록일 뿐 수치·기준 충족이 확인된 기록이 아님")
    sec["remaining"] = rem

    digest = {"zone": {"id": zid, "location": loc, "block": blk, "status": zst},
              "counts": {"findings": len(fs), "pre": n_pre, "post": n_post, "closed": len(closed), "open": len(opened)},
              "findings": digest_fs,
              "hold_points": [{"hp": h.get("hp"), "name": _s(h.get("name")), "state": "해제" if h.get("released") else "대기",
                               "approval": _s(h.get("released"))} for h in hps],
              "same_spot_recurrences": [{"pre": _s(r.get("pre")), "post": _s(r.get("post"))} for r in recs],
              "rejected_spots": [{"post": _s(r.get("post")), "reject_reason": _s(r.get("reason"), "미입력")} for r in rejs],
              "not_recorded": "조도·염분·도막두께·환경 계측값 (이 기록에 없음)"}
    return {"title": f"[검사 보고서 초안] {zid} ({loc}) · BLK {blk}", "sections": sec,
            "digest": json.dumps(digest, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            "ids": {d["id"] for d in digest_fs} | {x for r in recs for x in (_s(r.get("pre")), _s(r.get("post")))}
            | {_s(r.get("post")) for r in rejs},
            "statuses": {zst} | {_s(f.get("status")) for f in fs}, "zone_id": zid, "zone_status": zst,
            "fstatus": {d["id"]: d["status"] for d in digest_fs}, "fstage": {d["id"]: d["stage"] for d in digest_fs},
            "hps": [{"hp": _s(h.get("hp")), "name": _s(h.get("name")), "released": bool(h.get("released"))} for h in hps],
            "open": len(opened),
            # LLM 요약의 분광 문장 검사용: 기록 유무, 표면 분류가 있는 측정의 라벨, 시뮬레이터 값·시뮬레이션 모델 여부
            "spec": {"any": bool(specs), "ok_labels": {x["label"] for sp in specs.values() for x in sp if x["kind"] == "ok"},
                     "sim": any(x["sim"] or x["model_simulated"] for sp in specs.values() for x in sp),
                     "by_id": {fid: {x["label"] for x in sp if x["kind"] == "ok"} for fid, sp in specs.items()}},
            "manual_ids": {d["id"] for d in digest_fs if d["source"] == "검사원 직접 추가"}}


def numbers(text: str) -> set[str]:
    """글 속 숫자들. 정수는 앞의 0을 뗀다 ('10월 9일'과 기록의 '10-09'를 같은 숫자로)"""
    return {x if "." in x else str(int(x)) for x in re.findall(r"\d+(?:\.\d+)?", text)}
