"""행사장용 인터넷 주소. 발표 노트북이 따로 있고 폰을 같은 와이파이에 못 붙일 때 이 노트북의 서버를 인터넷으로 연다.
cloudflared 빠른 터널(계정 없음, 켤 때마다 주소가 바뀜)의 긴 로그 대신 주소만 크게 보여 주고 클립보드에 복사한다.

    python -m server.tunnel          # 서버 포트 8000
    python -m server.tunnel 8765     # 다른 포트

cloudflared 위치: BT_CLOUDFLARED > PATH > ~/tools/cloudflared.exe
"""
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

port = sys.argv[1] if len(sys.argv) > 1 else "8000"
exe = os.environ.get("BT_CLOUDFLARED") or shutil.which("cloudflared") or str(Path.home() / "tools" / "cloudflared.exe")
if not Path(exe).exists():
    sys.exit(f"cloudflared를 찾지 못했습니다: {exe}\nBT_CLOUDFLARED에 경로를 넣거나 ~/tools/cloudflared.exe에 두세요.")
try:
    urllib.request.urlopen(f"http://localhost:{port}/api/v", timeout=3)
except OSError:
    print(f"주의: localhost:{port}에 서버가 없습니다. 다른 창에서 uvicorn을 먼저 켜세요(터널은 그대로 열어 둡니다).\n")

p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{port}"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
url = linked = None
print("터널 여는 중...")
try:
    for line in p.stdout:
        if not url and (m := re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)):
            url = m.group(0)
            copied = bool(shutil.which("clip")) and subprocess.run(["clip"], input=url, text=True).returncode == 0
            print(f"\n{'=' * 64}\n  발표 노트북 주소:  {url}\n  ({'클립보드에 복사함 · ' if copied else ''}폰은 그 화면 왼쪽 아래 QR)\n{'=' * 64}\n")
        elif not linked and "Registered tunnel connection" in line:
            linked = True
            loc = re.search(r"location=(\w+)", line)
            print(f"연결됨 ({loc[1] if loc else '?'}). 이 창을 닫으면 주소가 끊깁니다. 끄려면 Ctrl+C")
        elif " ERR " in line:  # 끊김 같은 문제만 보여 준다
            print(line.rstrip()[:200])
except KeyboardInterrupt:
    pass
finally:
    p.terminate()
print("터널을 닫았습니다. 다시 켜면 주소가 바뀝니다.")
