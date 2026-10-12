"""분광 측정값 해석 (담당: 상우 · 선우). 지금은 RF 모델 대신 파장 비율 하나로 녹 신호만 본다.
RF로 바꿀 때 함수 이름과 돌려주는 모양만 지키면 서버는 고칠 필요가 없다. CSV 형식: sensor/README.md"""

MODEL_VERSION = "ratio-v0"
WAVELENGTHS = [405, 425, 450, 475, 515, 550, 555, 600, 640, 690, 745, 855]  # ch1~ch12 (nm). ch13 밝기, ch14 깜빡임은 안 쓴다


def analyze(reflectance: list[float]) -> dict:
    """reflectance: ch1~ch12 반사율 (sample / white).
    반환: {"rust_index": 640/515 nm 반사율 비, "label": 판정 문구, "model_version": str}"""
    # ponytail: 비율 하나와 임계값 1.5는 시편을 재기 전 가정값이다. 녹(산화철)은 적색 반사가 커서 값이 오르고,
    # 맨 강판은 1 근처다. 515·640 nm는 선행연구(PNAS Nexus) 518·655 nm에 가장 가까운 채널. 실측 후 RF로 바꾼다
    r515, r640 = reflectance[4], reflectance[8]
    idx = round(r640 / r515, 2) if r515 else 0.0
    return {"rust_index": idx, "label": "녹 신호" if idx >= 1.5 else "녹 신호 없음", "model_version": MODEL_VERSION}
