"""분광 측정값 해석 (담당: 상우 · 선우).
- analyze: 폰 지적 카드의 분광 측정(BT_SPECTRAL=1). 파장 비율 하나로 녹 신호만 보는 임시 규칙.
- classify: 품질 지도 '분광 · 표면 상태'. 선우 RF 모델(강판 / 도장 / 녹 3클래스, sensor/weights, git에 안 올림).
CSV 형식: sensor/README.md"""
import os
import pickle
import warnings
from functools import lru_cache
from pathlib import Path

MODEL_VERSION = "ratio-v0"
SURFACE_MODEL = Path(os.environ.get("BT_SPECTRAL_MODEL", Path(__file__).parent / "weights" / "spectral_rf.joblib"))
SURFACE_KO = {"bare_metal": "강판", "orange_paint": "도장", "rust": "녹"}
WAVELENGTHS = [405, 425, 450, 475, 515, 550, 555, 600, 640, 690, 745, 855]  # ch1~ch12 (nm). ch13 밝기, ch14 깜빡임은 안 쓴다


def analyze(reflectance: list[float]) -> dict:
    """reflectance: ch1~ch12 반사율 (sample / white).
    반환: {"rust_index": 640/515 nm 반사율 비, "label": 판정 문구, "model_version": str}"""
    # ponytail: 비율 하나와 임계값 1.5는 시편을 재기 전 가정값이다. 녹(산화철)은 적색 반사가 커서 값이 오르고,
    # 맨 강판은 1 근처다. 515·640 nm는 선행연구(PNAS Nexus) 518·655 nm에 가장 가까운 채널. 실측 후 RF로 바꾼다
    r515, r640 = reflectance[4], reflectance[8]
    idx = round(r640 / r515, 2) if r515 else 0.0
    return {"rust_index": idx, "label": "녹 신호" if idx >= 1.5 else "녹 신호 없음", "model_version": MODEL_VERSION}


@lru_cache(maxsize=1)
def surface_model():
    """RF 모델 묶음(dict: model, channels, classes, model_version …). 파일·sklearn이 없으면 None.
    joblib은 피클이라 열 때 코드가 돌 수 있어서, sklearn 클래스와 numpy 배열 복원 말고는 불러오지 못하게 막고 연다"""
    if not SURFACE_MODEL.exists():
        return None
    try:
        import joblib.numpy_pickle as jnp
    except ImportError:
        return None
    allow = {("numpy", "ndarray"), ("numpy", "dtype"), ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
             ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"), ("joblib.numpy_pickle", "NumpyArrayWrapper")}

    class Safe(jnp.NumpyUnpickler):
        def find_class(self, module, name):
            if not ((module.startswith("sklearn.") and name[:1].isupper()) or (module, name) in allow):
                raise pickle.UnpicklingError(f"허용하지 않는 import: {module}.{name}")
            return super().find_class(module, name)

    with open(SURFACE_MODEL, "rb") as f, warnings.catch_warnings():
        warnings.simplefilter("ignore")  # ponytail: 모델은 sklearn 1.9.1로 저장. 1.3.2에서도 학습 32건 예측이 같았다. 시연 PC는 버전을 맞출 것
        return Safe(str(SURFACE_MODEL), f).load()


def classify(rows: list[dict]) -> list[tuple[str, float]] | None:
    """rows: 선우 측정 CSV 행(r_F2 … r_FXL = (시료 − 암흑) ÷ (흰 − 암흑)). 반환: [(클래스, 확신도)], 모델이 없으면 None"""
    m = surface_model()
    if m is None:
        return None
    if not rows:
        return []
    X = [[float(r["r_" + c]) for c in m["channels"]] for r in rows]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = m["model"].predict_proba(X)
    cls = list(m["model"].classes_)
    return [(cls[int(row.argmax())], round(float(row.max()), 2)) for row in p]
