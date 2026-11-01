"""로컬 Ollama HTTP 호출 (담당: 선우). 설정은 부를 때마다 환경변수에서 읽는다 (import 때 네트워크·설정 고정 없음).

BT_LLM              ollama(기본) / off       off면 LLM을 부르지 않고 템플릿만 쓴다
BT_LLM_URL          http://127.0.0.1:11434   Ollama 주소
BT_LLM_MODEL        qwen2.5:7b               설치돼 있다고 가정하지 않는다. 없으면 '모델 없음'으로 템플릿 대체
BT_LLM_TIMEOUT      120                      응답 전체 대기 초 (연결은 3초)
BT_LLM_TEMPERATURE  0.1
BT_LLM_NUM_PREDICT  700                      출력 토큰 한도
BT_LLM_NUM_CTX      8192                     문맥 길이
BT_LLM_MAX_INPUT    12000                    LLM에 보낼 기록 요약(JSON) 최대 글자 수. 넘으면 자르지 않고 템플릿으로 대체
BT_LLM_KEEP_ALIVE   30m                      모델을 메모리에 붙잡아 둘 시간 (시연 중 다시 읽는 시간 절약)
BT_LLM_REPAIR       1                        검증에 걸린 절만 이유를 알려 주고 한 번 다시 쓰게 함. 0이면 안 함"""
import json
import os
import re

import httpx

DEFAULTS = {"BT_LLM": "ollama", "BT_LLM_URL": "http://127.0.0.1:11434", "BT_LLM_MODEL": "qwen2.5:7b", "BT_LLM_TIMEOUT": "120",
            "BT_LLM_TEMPERATURE": "0.1", "BT_LLM_NUM_PREDICT": "700", "BT_LLM_NUM_CTX": "8192", "BT_LLM_MAX_INPUT": "12000",
            "BT_LLM_KEEP_ALIVE": "30m", "BT_LLM_REPAIR": "1"}


class LLMError(Exception):
    """LLM을 못 쓴 이유. str(e)는 화면에 그대로 보여 줄 짧은 사유다 (원시 예외는 서버 로그에만)"""


def config() -> dict:
    e = {k: os.environ.get(k) or v for k, v in DEFAULTS.items()}
    try:
        return {"mode": e["BT_LLM"].strip().lower(), "url": e["BT_LLM_URL"].rstrip("/"), "model": e["BT_LLM_MODEL"].strip(),
                "timeout": float(e["BT_LLM_TIMEOUT"]), "keep_alive": e["BT_LLM_KEEP_ALIVE"],
                "max_input": int(e["BT_LLM_MAX_INPUT"]), "repair": e["BT_LLM_REPAIR"].strip() not in ("0", "off", "false"),
                # 생성 결과에 영향을 주는 값. 캐시 키에 들어간다
                "options": {"temperature": float(e["BT_LLM_TEMPERATURE"]), "num_predict": int(e["BT_LLM_NUM_PREDICT"]),
                            "num_ctx": int(e["BT_LLM_NUM_CTX"]), "top_p": 0.9, "seed": 42}}
    except ValueError as x:
        raise LLMError("BT_LLM_* 설정값이 숫자가 아님") from x


def enabled(cfg=None) -> bool:
    return (cfg or config())["mode"] == "ollama"


def chat(messages: list, schema: dict, cfg: dict) -> dict:
    """/api/chat 한 번 (stream 없음, JSON 스키마로 출력 형식 고정). messages = [{"role", "content"}, ...].
    반환: 파싱한 JSON 객체. 실패는 LLMError"""
    body = {"model": cfg["model"], "stream": False, "format": schema, "options": cfg["options"], "keep_alive": cfg["keep_alive"],
            "messages": messages}
    try:
        # trust_env=False: HTTP_PROXY 같은 환경변수가 있어도 로컬 Ollama로 바로 보낸다 (기록이 프록시로 나가지 않게)
        r = httpx.post(cfg["url"] + "/api/chat", json=body, timeout=httpx.Timeout(cfg["timeout"], connect=3.0), trust_env=False)
    except httpx.TimeoutException as x:
        raise LLMError(f"LLM 응답 시간 초과 ({cfg['timeout']:g}초)") from x
    except httpx.HTTPError as x:
        raise LLMError("Ollama 연결 실패 (ollama serve 실행·BT_LLM_URL 확인)") from x
    if r.status_code == 404:
        raise LLMError(f"모델 없음: {cfg['model']} (ollama pull {cfg['model']})")
    if r.status_code != 200:
        raise LLMError(f"Ollama 오류 (HTTP {r.status_code})")
    try:
        d = r.json()
        content = d["message"]["content"]
    except (ValueError, KeyError, TypeError) as x:
        raise LLMError("Ollama 응답 형식 오류") from x
    # 생각하는 모델(qwen3 등)이 본문에 <think>를 섞어 보내는 경우
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    if not content:
        raise LLMError("LLM 빈 응답")
    if d.get("done_reason") == "length":
        raise LLMError("LLM 출력이 길이 한도에서 잘림 (BT_LLM_NUM_PREDICT)")
    try:
        out = json.loads(content)
    except ValueError as x:
        raise LLMError("LLM 응답이 JSON이 아님") from x
    if not isinstance(out, dict):
        raise LLMError("LLM 응답이 JSON 객체가 아님")
    return out


def tags(cfg=None) -> list[str]:
    """설치된 모델 이름들 (python -m llm.report --check 에서만 쓴다)"""
    cfg = cfg or config()
    try:
        r = httpx.get(cfg["url"] + "/api/tags", timeout=httpx.Timeout(5.0, connect=3.0), trust_env=False)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as x:
        raise LLMError("Ollama 연결 실패 (ollama serve 실행·BT_LLM_URL 확인)") from x
