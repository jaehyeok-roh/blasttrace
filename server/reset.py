"""시연 시작 상태로 되돌린다. 서버를 끈 뒤 저장소 맨 위 폴더에서: python -m server.reset"""
import os
import shutil
from pathlib import Path

DATA = Path(os.environ.get("BT_DATA", Path(__file__).resolve().parent.parent / "data"))
try:
    shutil.rmtree(DATA)
    print(f"{DATA} 를 지웠습니다. 서버를 다시 켜면 예시 데이터로 시작합니다.")
except FileNotFoundError:
    print("이미 비어 있습니다.")
except PermissionError:
    raise SystemExit("DB 파일이 사용 중입니다. 서버를 끄고 다시 실행하세요.")
