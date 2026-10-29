"""검사 공수·효과 모델. 상우 공수 모델 v2/v2.1(bt_model.py, blasttrace_process_formulas_v2.md)과 같은 식·기본값이다.
구역 1건(블록단계 2차 표면처리 홀드포인트 → 도장 → DFT → 종결)을 단위공정 U0~U8 × 역할(S 협력사, I 검사자, O 선주,
V 페인트사, M QC관리)로 나눠 인·분을 더한다. 방법: M0 현행, M1 기록연결(BlastTrace, AI 제외), M2 M1 + AI 사전선별.
앱 기록에서 잰 값(app.measured)과 팀 실험값(shared/effect_params.json)이 있으면 기본값 대신 쓴다."""
import copy
import json
import math
import random
import statistics
from functools import lru_cache
from pathlib import Path

# (이름, 기본값, 출처 태그, 뜻). 태그: 규정 = IMO MSC.215(82), NSRP = NSRP 2007/2016 보고서, KTA = 사양 관행,
# 조선소 = 현업 입력이 필요한 값, 팀측정 = 시편 실험으로 잴 값, 가정 = 민감도 범위로 보고
SPEC = [
    ("A_z", 186.0, "NSRP", "구역 면적 m² (2,000 ft² 요소)"), ("A_flat", 120.0, "조선소", "평탄부 m²"),
    ("L_bound", 60.0, "조선소", "탱크 경계 길이 m"), ("L_stiff", 150.0, "조선소", "보강재 길이 m"),
    ("L_psm", 40.0, "조선소", "주요지지부재 길이 m"), ("n_open", 6, "조선소", "개구부 수"),
    ("A_cx", 4.0, "조선소", "복잡부 m²"), ("c_z", 10, "NSRP", "구역당 체크포인트 수"),
    ("tau_req", 10, "팀측정", "검사 요청 작성 분"), ("tau_sched_I", 15, "가정", "입회 일정 조율 분 (검사자)"),
    ("tau_sched_O", 10, "가정", "입회 일정 조율 분 (선주)"), ("tau_sched_V", 10, "가정", "입회 일정 조율 분 (페인트사)"),
    ("w_sched", 90, "조선소", "요청 → 입회 대기 분 (경과)"),
    ("tau_permit", 10, "조선소", "밀폐구역 가스측정·허가 분/회"), ("tau_travel", 10, "조선소", "이동 분/회"),
    ("tau_locate", 8, "팀측정", "구역 안에서 위치 찾기 분/회"), ("n_entry_env", 6, "조선소", "환경측정 출입 횟수"),
    ("tau_env", 5, "규정", "환경측정 분/회"), ("tau_env_rec", 3, "팀측정", "환경값 수기 기록 분/회"),
    ("tau_vis", 0.3, "팀측정", "육안 청정도 판정 분/m²"), ("att_O", 0.5, "가정", "선주 입회 시간 계수"),
    ("att_V", 0.5, "가정", "페인트사 입회 시간 계수"), ("n_salt", 1, "규정", "염분 측정 횟수"),
    ("tau_salt", 15, "팀측정", "염분 측정 분/회"), ("n_dust", 2, "가정", "먼지 테이프 횟수"),
    ("tau_dust", 5, "팀측정", "먼지 테이프 분/회"), ("n_prof", 2, "가정", "프로파일 측정 횟수"),
    ("tau_prof", 5, "팀측정", "프로파일 측정 분/회"),
    ("pi1", 0.30, "가정", "1차 검사에서 지적이 나올 확률"), ("pi2", 0.10, "가정", "재검사에서 다시 지적될 확률"),
    ("n_punch", 4, "가정", "지적이 나올 때 지적 건수"), ("a_punch", 0.5, "가정", "지적 1건 보수 면적 m²"),
    ("tau_punch", 5, "팀측정", "지적 작성 분/건"),
    ("tau_field", 20, "팀측정", "현장 일지 수기 작성 분"), ("tau_reentry", 25, "팀측정", "사무실 전산 재입력 분"),
    ("tau_photo", 15, "팀측정", "사진 정리·첨부 분"), ("tau_review", 10, "가정", "관리자 검토·서명 분"),
    ("tau_notify", 5, "가정", "조치 지시 전달 분"), ("tau_rw", 30, "조선소", "보수 작업 분/m²"),
    ("tau_rereq", 10, "가정", "재검 요청 분"), ("tau_resched", 15, "가정", "재검 재조율 분"),
    ("tau_reid", 6, "팀측정", "지적 위치 다시 찾기 분/건"), ("tau_rejudge", 2, "팀측정", "재판정 분/건"),
    ("tau_rerec", 10, "팀측정", "재검 기록 분"), ("tau_rereentry", 10, "팀측정", "재검 전산 재입력 분"),
    ("w_rework", 240, "조선소", "보수 후 재검 입회까지 대기 분 (경과)"),
    ("pi_disp", 0.05, "NSRP", "체크포인트당 분쟁 확률 (20개 중 1개)"), ("tau_disp", 30, "가정", "분쟁 논의 분/인"),
    ("p_disp_entry", 0.5, "가정", "분쟁 때 추가 입회 확률"), ("w_disp", 120, "가정", "분쟁 해소 대기 분 (경과)"),
    ("tau_dft", 0.5, "NSRP", "DFT 리딩 분/회 (30초)"), ("tau_dft_rec", 0.25, "팀측정", "DFT 리딩값 전사 분/회"),
    ("att_O_dft", 0.3, "가정", "DFT 선주 입회 계수"),
    ("tau_asm", 40, "팀측정", "종결 근거 패키지 조립 분"), ("tau_appr_M", 15, "가정", "승인 분 (QC관리)"),
    ("tau_appr_O", 15, "가정", "승인 분 (선주)"), ("n_items", 20, "규정", "필수 근거 항목 수 (Annex 2 기준)"),
    ("pi_miss", 0.10, "팀측정", "항목당 종결 때 누락 확률"), ("tau_fix", 20, "팀측정", "누락 보완 분/항목"),
    ("W_window", 480, "KTA", "블라스팅 → 프라이머 허용 시간 분 (8 h)"),
    ("tau_app", 2, "팀측정", "앱 입력 분/회 (촬영·업로드·판정 버튼). BlastTrace가 새로 만드는 일"),
]
PARAMS = {k: v for k, v, _, _ in SPEC}
TAG = {k: t for k, _, t, _ in SPEC}
LABEL = {k: n for k, _, _, n in SPEC}

# M1 레버 ρ(줄어드는 비율). 보수·기본·낙관 세 벌로 범위를 보고한다 [가정 → 팀측정으로 바꿀 값]
SCENARIOS = {
    "보수": dict(rho_locate=0.3, rho_reid=0.5, rho_reentry=0.8, rho_field=0.1, rho_photo=0.5, rho_env_rec=0.8,
                 rho_dft_rec=0.8, rho_asm=0.5, pi_miss=0.03, rho_disp_count=0.1, rho_disp_time=0.1, rho_rerec=0.3),
    "기본": dict(rho_locate=0.5, rho_reid=0.7, rho_reentry=0.9, rho_field=0.3, rho_photo=0.7, rho_env_rec=0.9,
                 rho_dft_rec=0.9, rho_asm=0.8, pi_miss=0.01, rho_disp_count=0.3, rho_disp_time=0.3, rho_rerec=0.5),
    "낙관": dict(rho_locate=0.7, rho_reid=0.9, rho_reentry=1.0, rho_field=0.5, rho_photo=0.9, rho_env_rec=1.0,
                 rho_dft_rec=1.0, rho_asm=0.9, pi_miss=0.0, rho_disp_count=0.5, rho_disp_time=0.5, rho_rerec=0.7),
}
AI = dict(pi1=0.20, rho_vis=0.2)  # M2: 협력사 사전확인으로 1차 지적률↓, 육안 시간↓ [가설]
# v2.1: 전사·보고 자동화는 출처값(NSRP 2016), 환경 로거 연동 시 검사자 환경측정 출입 6 → 2회
SRC_LEVERS = dict(SCENARIOS["기본"], rho_reentry=1.0, rho_dft_rec=1.0, rho_env_rec=1.0, rho_asm=0.9)
ENV_LOGGER = dict(n_entry_env=2, tau_env=2)

UNITS = ["U0 요청·조율", "U1 접근·위치 찾기", "U2 환경측정·기록", "U3 표면검사·판정", "U4 기록",
         "U5 지적·재검", "U6 분쟁·합의", "U7 DFT 측정·기록", "U8 종결", "앱 입력 (새로 생긴 일)"]
SHORT = ["요청·조율", "접근·위치", "환경측정", "판정", "기록", "지적·재검", "분쟁", "DFT", "종결", "앱 입력"]


def n_dft(p):
    """PSPC Annex 3 DFT 리딩 수: 평탄 1/5 m², 경계 2.5 m당 1, 보강재 세트×2, 주요지지부재 세트×3(≥3세트), 개구부 양쪽, 복잡부 5/m²(≥3)"""
    return (p["A_flat"] / 5 + p["L_bound"] / 2.5 + p["L_stiff"] / 2.5 * 2 + max(p["L_psm"] / 2.5, 3) * 3
            + 2 * p["n_open"] + max(5 * p["A_cx"], 3))


def apply(p, lev=None, ai=False, extra=None):
    """현행 값 p에 레버를 걸어 M1(lev) / M2(lev + ai) 값으로 바꾼다. lev=None이면 M0"""
    q = copy.deepcopy(p)
    if lev:
        q["app"] = 1  # 앱을 쓰면 촬영·업로드·버튼 시간이 새로 생긴다
        for k in ("locate", "reid", "reentry", "field", "photo", "env_rec", "dft_rec", "asm", "rerec"):
            q["tau_" + k] *= 1 - lev["rho_" + k]
        q["tau_rereentry"] *= 1 - lev["rho_reentry"]
        q["pi_miss"] = lev["pi_miss"]
        q["pi_disp"] *= 1 - lev["rho_disp_count"]
        q["tau_disp"] *= 1 - lev["rho_disp_time"]
    if extra:
        q.update(extra)
    if ai:
        q["pi1"] = AI["pi1"]
        q["tau_vis"] *= 1 - AI["rho_vis"]
    return q


def minutes(p):
    """구역 1건 기대 인·분: [단위공정별 {역할: 분}]"""
    entry = p["tau_permit"] + p["tau_travel"] + p["tau_locate"]  # 1인 1회 출입
    vis = p["tau_vis"] * p["A_z"]
    rounds = p["pi1"] / (1 - p["pi2"])  # 기대 재검 라운드 = π₁(1 + π₂ + π₂² + …)
    nd = p["c_z"] * p["pi_disp"]  # 기대 분쟁 건수
    extra = p["p_disp_entry"] * entry
    nr = n_dft(p)
    app = p.get("app", 0) * p["tau_app"]
    return [
        dict(S=p["tau_req"], I=p["tau_sched_I"], O=p["tau_sched_O"], V=p["tau_sched_V"]),
        dict(S=entry, I=entry * (1 + p["n_entry_env"] + 1), O=entry * 2, V=entry),
        dict(I=p["n_entry_env"] * (p["tau_env"] + p["tau_env_rec"])),
        dict(I=vis + p["n_salt"] * p["tau_salt"] + p["n_dust"] * p["tau_dust"] + p["n_prof"] * p["tau_prof"]
             + p["pi1"] * p["n_punch"] * p["tau_punch"], O=vis * p["att_O"], V=vis * p["att_V"]),
        dict(I=p["tau_field"] + p["tau_reentry"] + p["tau_photo"], M=p["tau_review"]),
        dict(S=rounds * (p["tau_notify"] + p["tau_rw"] * p["n_punch"] * p["a_punch"] + p["tau_rereq"] + entry
                         + p["tau_reid"] * p["n_punch"]),
             I=rounds * (p["tau_notify"] + p["tau_resched"] + entry + p["tau_reid"] * p["n_punch"]
                         + p["tau_rejudge"] * p["n_punch"] + p["tau_rerec"] + p["tau_rereentry"])),
        dict(S=nd * p["tau_disp"], I=nd * (p["tau_disp"] + extra), O=nd * (p["tau_disp"] + extra), V=nd * p["tau_disp"]),
        dict(I=nr * (p["tau_dft"] + p["tau_dft_rec"]), O=nr * p["tau_dft"] * p["att_O_dft"]),
        dict(M=p["tau_asm"] + p["tau_appr_M"] + p["n_items"] * p["pi_miss"] * p["tau_fix"], O=p["tau_appr_O"]),
        dict(I=app * (1 + rounds), S=app * rounds),  # 홀드포인트 촬영·판정 1회 + 재검 라운드마다 재검 사진(I)·조치 등록(S)
    ]


def rebast_risk(p, n=20000, seed=1):
    """블라스팅 완료 → 프라이머 착수 경과시간 L_hold를 몬테카를로로 뽑아 P(L_hold > W) = 재블라스팅 위험.
    항마다 로그정규 노이즈(CV 0.35). 상우 bt_model.lead_time_mc와 같은 순서로 난수를 뽑아 같은 값이 나온다"""
    return _risk(tuple(sorted(p.items())), n, seed)


@lru_cache(maxsize=64)
def _risk(items, n, seed):
    p, rnd, over = dict(items), random.Random(seed), 0

    def ln(mu, cv=0.35):
        s = math.sqrt(math.log(1 + cv * cv))
        return rnd.lognormvariate(math.log(mu) - s * s / 2, s)

    entry = p["tau_permit"] + p["tau_travel"] + p["tau_locate"]
    tests = p["n_salt"] * p["tau_salt"] + p["n_dust"] * p["tau_dust"] + p["n_prof"] * p["tau_prof"]
    for _ in range(n):
        L = ln(p["w_sched"]) + ln(entry) + ln(p["tau_vis"] * p["A_z"]) + ln(tests) + ln(p["tau_field"])
        if rnd.random() < 1 - (1 - p["pi_disp"]) ** p["c_z"]:  # 구역에 분쟁 1건 이상
            L += ln(p["w_disp"]) + ln(p["tau_disp"])
        if rnd.random() < p["pi1"]:  # 지적 → 보수 → 재검
            L += (ln(p["tau_rw"] * p["n_punch"] * p["a_punch"]) + ln(p["w_rework"]) + ln(entry)
                  + ln(p["tau_reid"] * p["n_punch"]) + ln(p["tau_rejudge"] * p["n_punch"]))
        over += L > p["W_window"]
    return over / n


def team_values(path=Path(__file__).parent.parent / "shared" / "effect_params.json"):
    """팀 실험으로 잰 값: {"tau_locate": 4.5, ...}. '_'로 시작하는 키는 설명이라 건너뛴다"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in d.items() if k in PARAMS and not k.startswith("_")}


def report(over=None):
    """화면용 결과. over = {이름: 값}으로 바꿀 값(앱 기록·팀 실험)"""
    p0 = dict(PARAMS, **(over or {}))
    p0["pi2"] = min(p0["pi2"], 0.9)  # 1이면 재검이 끝나지 않는다
    m0, m1, m2 = p0, apply(p0, SCENARIOS["기본"]), apply(p0, SCENARIOS["기본"], ai=True)
    per = {k: [sum(u.values()) for u in minutes(p)] for k, p in (("M0", m0), ("M1", m1), ("M2", m2))}
    T = {k: sum(v) for k, v in per.items()}
    roles = {k: {} for k in per}
    for k, p in (("M0", m0), ("M1", m1)):
        for u in minutes(p):
            for r, v in u.items():
                roles[k][r] = roles[k].get(r, 0) + v
    eta = lambda a, b: (1 - b / a) * 100
    rng = {s: eta(T["M0"], sum(sum(u.values()) for u in minutes(apply(p0, lev)))) for s, lev in SCENARIOS.items()}
    plus = sum(sum(u.values()) for u in minutes(apply(p0, SRC_LEVERS, extra=ENV_LOGGER)))
    plus_ai = sum(sum(u.values()) for u in minutes(apply(p0, SRC_LEVERS, ai=True, extra=ENV_LOGGER)))
    d = [a - b for a, b in zip(per["M0"], per["M1"])]
    return dict(
        T=T, per=per, units=UNITS, short=SHORT, roles=roles, n_dft=round(n_dft(p0)),
        eta01=eta(T["M0"], T["M1"]), eta02=eta(T["M0"], T["M2"]), range=rng,
        plus=dict(T=plus, eta=eta(T["M0"], plus)), plus_ai=dict(T=plus_ai, eta=eta(T["M0"], plus_ai)),
        saved=dict(기록=d[2] + d[4] + d[7] + d[8], 위치=d[1] + d[5], 분쟁=d[6], 판정=d[3]),
        risk={k: rebast_risk(p) * 100 for k, p in (("M0", m0), ("M1", m1), ("M2", m2))},
        rounds=m0["pi1"] / (1 - m0["pi2"]), used=p0)


if __name__ == "__main__":
    r = report()
    print({k: round(v) for k, v in r["T"].items()}, round(r["eta01"], 1), {k: round(v, 1) for k, v in r["risk"].items()},
          round(r["plus"]["T"]), round(r["plus_ai"]["T"]), r["n_dft"])
