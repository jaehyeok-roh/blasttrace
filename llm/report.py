"""보고서 초안 (담당: 선우). 로컬 Ollama LLM이 절마다 요약 문장을 쓰고, 지적 ID·상태·승인·분광 상태·계측 제한 같은
사실 줄은 기록에서 그대로 옮긴다 (llm/facts.py). LLM을 못 쓰면 같은 사실 줄만으로 된 템플릿으로 대체한다.
입력 record 예시: shared/record.json, 반환 예시: shared/report.json. 설치·설정·서버 연결: llm/README.md

  make_report(record)  LLM 생성 (캐시에 없으면 Ollama를 부르고 기다린다). 실패하면 템플릿
  peek_report(record)  네트워크 없이 바로: 캐시에 LLM 결과가 있으면 그것, 없으면 템플릿 (구역 화면용)
반환: {"text", "model", "prompt_version", "source": "llm" | "template", "note": 짧은 사유·안내, "cached", "llm_enabled"}

단독 실행: python -m llm.report shared/record.json  (--template 템플릿만, --json 반환 전체, --check Ollama·모델 확인)"""
import hashlib
import json
import logging
import sys
import threading
from collections import OrderedDict
from concurrent.futures import Future

from llm import facts as F, ollama
from llm.ollama import LLMError
from llm.prompt import KEYS, PROMPT_VERSION, SCHEMA, SYSTEM, clean, repair_message, user_message

log = logging.getLogger("blasttrace.llm")
TEMPLATE_MODEL, TEMPLATE_VERSION = "template-v1", "v1"
CACHE_MAX = 64

# 성공한 LLM 요약만 담는다 (실패는 담지 않아서 Ollama가 살아나면 다음 요청에 다시 시도한다)
_cache: "OrderedDict[str, dict]" = OrderedDict()
_inflight: dict[str, Future] = {}
_lock = threading.Lock()


def _key(fx, cfg):
    """기록 요약(LLM이 보는 내용 전부) + 모델·주소·생성 옵션 + 프롬프트 버전·본문. 구역 ID만으로 캐시하지 않는다"""
    k = {"digest": fx["digest"], "model": cfg["model"], "url": cfg["url"], "options": cfg["options"], "prompt": PROMPT_VERSION,
         "system": hashlib.sha256(SYSTEM.encode()).hexdigest(), "repair": cfg["repair"]}
    return hashlib.sha256(json.dumps(k, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _compose(fx, summary=None):
    lines = [fx["title"]]
    for n, (k, body) in enumerate(((k, fx["sections"][k]) for k in KEYS if k in fx["sections"]), 1):
        lines += ["", f"{n}. {F.SECTIONS[k]}"]
        if summary and summary.get(k):
            lines.append(f"요약 · {summary[k]}")
        lines += body
    return "\n".join(lines + [""] + F.NOTICE)


def _template(fx, note, enabled=True):
    return {"text": _compose(fx), "model": TEMPLATE_MODEL, "prompt_version": TEMPLATE_VERSION, "source": "template",
            "note": note, "cached": False, "llm_enabled": enabled}


def _llm(fx, res, cfg, cached):
    n = len(res["summary"])
    note = f"LLM 요약 {n}문장 · '-' 줄은 기록 그대로"
    if res.get("repaired"):
        note += f" · 재요청으로 {len(res['repaired'])}문장 고침"
    if res["dropped"]:
        note += f" · 검증 규칙에 걸린 문장 {len(res['dropped'])}개 제외"
    return {"text": _compose(fx, res["summary"]), "model": f"ollama/{cfg['model']}", "prompt_version": PROMPT_VERSION,
            "source": "llm", "note": note, "cached": cached, "llm_enabled": True, "dropped": [k for k, _ in res["dropped"]],
            "repaired": res.get("repaired", [])}


def _generate(fx, cfg):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_message(fx["digest"])}]
    out = ollama.chat(msgs, SCHEMA, cfg)
    summary, dropped = clean(out, fx)
    for k, why in dropped:
        log.warning("LLM 요약 제외 %s: %s", k, why)
    repaired = []
    if dropped and cfg["repair"]:
        # 걸린 절만 이유를 알려 주고 한 번 더. 다시 쓴 문장도 같은 검증을 거치고, 실패해도 첫 결과는 살린다
        msgs += [{"role": "assistant", "content": json.dumps(out, ensure_ascii=False)},
                 {"role": "user", "content": repair_message(dropped)}]
        try:
            fixed, still = clean(ollama.chat(msgs, SCHEMA, cfg), fx)
        except LLMError as e:
            log.warning("LLM 재요청 실패: %s", e)
        else:
            redo = {k for k, _ in dropped}
            repaired = [k for k in KEYS if k in redo and k in fixed]
            summary.update({k: fixed[k] for k in repaired})
            dropped = [(k, why) for k, why in dropped if k not in repaired]
            for k, why in still:
                if k in redo and k not in repaired:
                    log.warning("LLM 재요청 후에도 제외 %s: %s", k, why)
    if not summary:
        raise LLMError("LLM 문장이 형식·사실 검증 규칙을 통과하지 못함" if dropped else "LLM 요약이 비어 있음")
    return {"summary": summary, "dropped": dropped, "repaired": repaired}


def _summary(key, fx, cfg):
    """캐시 -> 같은 입력을 이미 생성 중이면 그 결과를 기다림 -> 직접 생성. 반환 (결과, 캐시 적중)"""
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key], True
        fut = _inflight.get(key)
        owner = fut is None
        if owner:
            fut = _inflight[key] = Future()
    if not owner:
        return fut.result(), True  # 같은 요청이 먼저 부른 LLM 결과 (실패면 같은 LLMError)
    try:
        res = _generate(fx, cfg)
    except BaseException as e:
        with _lock:
            _inflight.pop(key, None)
        fut.set_exception(e)
        raise
    with _lock:  # 캐시에 넣은 뒤에 진행 중 표시를 지운다 (그 사이에 온 요청이 다시 생성하지 않게)
        _cache[key] = res
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
        _inflight.pop(key, None)
    fut.set_result(res)
    return res, False


def make_report(record: dict) -> dict:
    """반환: {"text": 보고서 초안, "model": 모델 이름, "prompt_version": 프롬프트 버전, "source", "note", "cached"}.
    Ollama가 없거나 실패하면 template-v1 (model에 LLM 이름을 붙이지 않는다). record·DB는 바꾸지 않는다"""
    fx = F.build(record)
    try:
        cfg = ollama.config()
        if not ollama.enabled(cfg):
            return _template(fx, "LLM 꺼짐 (BT_LLM=off)", False)
        if len(fx["digest"]) > cfg["max_input"]:
            raise LLMError(f"기록이 길어 LLM 입력 한도 초과 ({len(fx['digest'])}/{cfg['max_input']}자, BT_LLM_MAX_INPUT)")
        res, hit = _summary(_key(fx, cfg), fx, cfg)
        return _llm(fx, res, cfg, hit)
    except LLMError as e:
        log.warning("LLM 보고서 대체 (%s): %s%s", record.get("zone_id"), e, f" · {e.__cause__!r}" if e.__cause__ else "")
        return _template(fx, f"LLM 미사용 · {e}")
    except Exception:
        log.exception("LLM 보고서 처리 중 오류 (%s)", record.get("zone_id"))
        return _template(fx, "LLM 미사용 · 처리 중 오류 (서버 로그 확인)")


def peek_report(record: dict) -> dict:
    """화면을 바로 그리기 위한 보고서. 네트워크를 쓰지 않는다: 이 기록·설정의 LLM 결과가 캐시에 있으면 그것, 없으면 템플릿"""
    fx = F.build(record)
    try:
        cfg = ollama.config()
    except LLMError as e:
        return _template(fx, f"LLM 미사용 · {e}", False)
    if not ollama.enabled(cfg):
        return _template(fx, "LLM 꺼짐 (BT_LLM=off)", False)
    with _lock:
        res = _cache.get(_key(fx, cfg))
    return _llm(fx, res, cfg, True) if res else _template(fx, "LLM 초안 아직 없음")


def template_report(record: dict) -> dict:
    return _template(F.build(record), "템플릿")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if not a.startswith("--")]
    if "--check" in argv:
        cfg = ollama.config()
        print(f"BT_LLM={cfg['mode']} · {cfg['url']} · 모델 {cfg['model']}")
        try:
            names = ollama.tags(cfg)
        except LLMError as e:
            print(f"실패: {e}")
            return 1
        ok = cfg["model"] in names or f"{cfg['model']}:latest" in names
        print("설치된 모델: " + (", ".join(names) or "없음"))
        print("준비됨" if ok else f"모델 없음 → ollama pull {cfg['model']}")
        return 0 if ok else 1
    path = args[0] if args else "shared/record.json"
    with open(path, encoding="utf-8") as fp:
        record = json.load(fp)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    rep = template_report(record) if "--template" in argv else make_report(record)
    if "--json" in argv:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"# {rep['source']} · {rep['model']} · {rep['prompt_version']} · {rep['note']}\n")
        print(rep["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
