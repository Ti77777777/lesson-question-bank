"""启动入口。

用法：
    python run.py                 # 使用 data/qbank.db
    python run.py --demo          # 使用 data/demo.db（含示例题）
    python run.py --port 8760     # 换端口
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    args = sys.argv[1:]
    port = 8760 if '--demo' in args else 8761
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
    if "--demo" in args:
        os.environ.setdefault("QBANK_DB", str(ROOT / "data" / "demo.db"))
        print("演示库：data/demo.db")
    else:
        os.environ.setdefault("QBANK_DB", str(ROOT / "data" / "qbank.db"))

    if "--demo" in args and not Path(os.environ["QBANK_DB"]).exists():
        import subprocess
        env=os.environ.copy()
        env.pop("DEEPSEEK_API_KEY",None)
        subprocess.run([sys.executable,str(ROOT/"data/seed.py"),"--database",os.environ["QBANK_DB"]],cwd=ROOT,env=env,check=True)

    from http.server import ThreadingHTTPServer

    from app.server import Handler, create_app, LocalHTTPServer

    database = create_app()
    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(url+'api/meta',timeout=3) as response:running=json.load(response)
            with urllib.request.urlopen(url+'api/ai/status',timeout=3) as response:json.load(response)
            if running.get('db_path') != database.rel_to_root(database.DB_PATH):raise ValueError()
        except Exception:
            print(f'端口 {port} 已被占用或运行旧版题库，请关闭对应服务后重开，或用 --port 指定其他端口。')
            sys.exit(1)
        print('题库已在运行：'+url)
        if '--no-browser' not in args:
            import webbrowser
            webbrowser.open(url+'#/ai' if '--ai' in args else url)
        return
    print(f"题库系统已启动：{url}")
    print(f"数据库：{database.DB_PATH}")
    print("按 Ctrl+C 停止。")
    if "--no-browser" not in args:
        try:
            import webbrowser
            webbrowser.open(url+'#/ai' if '--ai' in args else url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
