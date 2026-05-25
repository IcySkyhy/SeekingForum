"""
sync_db.py — 数据库同步工具
使用 GitHub Gist 作为中转，实现跨设备 SQLite 数据库同步。

用法:
    python sync_db.py upload    # 上传本地数据库到 Gist
    python sync_db.py download  # 从 Gist 下载数据库到本地
    python sync_db.py status    # 查看同步状态

环境变量 (在 .env 中配置):
    SYNC_GITHUB_TOKEN  — GitHub Personal Access Token (需要 gist 权限)
    SYNC_GIST_ID       — Gist ID (首次上传后自动生成并写入 .env)

定时同步 (Linux/Mac cron):
    0 2 * * * cd /path/to/SeekingForum && python sync_db.py upload

定时同步 (Windows 任务计划程序):
    创建基本任务 → 每天触发 → 启动程序: python → 参数: sync_db.py upload
"""

import os
import sys
import json
import base64
import gzip
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv, set_key

load_dotenv()

DB_PATH = Path(__file__).parent / 'instance' / 'forum.db'
ENV_PATH = Path(__file__).parent / '.env'
BEIJING_TZ = timezone(timedelta(hours=8))

GITHUB_TOKEN = os.environ.get('SYNC_GITHUB_TOKEN', '')
GIST_ID = os.environ.get('SYNC_GIST_ID', '')


def github_api(method, url, data=None):
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'SeekingForum-Sync',
    }
    body = json.dumps(data).encode('utf-8') if data else None
    if body:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode('utf-8'))


def upload():
    """压缩并上传数据库到 GitHub Gist"""
    global GIST_ID
    if not GITHUB_TOKEN:
        print('[错误] 请在 .env 中设置 SYNC_GITHUB_TOKEN')
        print('  获取方式: GitHub → Settings → Developer settings → Personal access tokens → 勾选 gist 权限')
        sys.exit(1)

    if not DB_PATH.exists():
        print(f'[错误] 数据库文件不存在: {DB_PATH}')
        sys.exit(1)

    raw = DB_PATH.read_bytes()
    compressed = gzip.compress(raw)
    encoded = base64.b64encode(compressed).decode('ascii')
    now = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')

    meta = json.dumps({'size': len(raw), 'compressed': len(compressed), 'time': now})
    payload = {
        'description': f'SeekingForum DB backup — {now}',
        'files': {
            'forum_db.gz.b64': {'content': encoded},
            'meta.json': {'content': meta},
        }
    }

    if GIST_ID:
        result = github_api('PATCH', f'https://api.github.com/gists/{GIST_ID}', payload)
        print(f'[成功] 数据库已更新到 Gist: {GIST_ID}')
    else:
        payload['public'] = False
        result = github_api('POST', 'https://api.github.com/gists', payload)
        GIST_ID = result['id']
        set_key(str(ENV_PATH), 'SYNC_GIST_ID', GIST_ID)
        print(f'[成功] 已创建新 Gist: {GIST_ID}')
        print(f'  Gist ID 已自动写入 .env')

    print(f'  原始大小: {len(raw)/1024:.1f} KB')
    print(f'  压缩大小: {len(compressed)/1024:.1f} KB')
    print(f'  同步时间: {now}')


def download():
    """从 GitHub Gist 下载并恢复数据库"""
    if not GITHUB_TOKEN:
        print('[错误] 请在 .env 中设置 SYNC_GITHUB_TOKEN')
        sys.exit(1)
    if not GIST_ID:
        print('[错误] 请在 .env 中设置 SYNC_GIST_ID (首次需先执行 upload)')
        sys.exit(1)

    result = github_api('GET', f'https://api.github.com/gists/{GIST_ID}')
    files = result.get('files', {})

    if 'forum_db.gz.b64' not in files:
        print('[错误] Gist 中未找到数据库备份文件')
        sys.exit(1)

    encoded = files['forum_db.gz.b64']['content']
    compressed = base64.b64decode(encoded)
    raw = gzip.decompress(compressed)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        backup = DB_PATH.with_suffix('.db.bak')
        DB_PATH.rename(backup)
        print(f'  已备份旧数据库到: {backup.name}')

    DB_PATH.write_bytes(raw)

    meta = json.loads(files.get('meta.json', {}).get('content', '{}'))
    print(f'[成功] 数据库已恢复')
    print(f'  大小: {len(raw)/1024:.1f} KB')
    print(f'  备份时间: {meta.get("time", "未知")}')


def status():
    """查看同步状态"""
    print('=== 求索论坛数据库同步状态 ===')
    print(f'  数据库路径: {DB_PATH}')
    print(f'  数据库存在: {"是" if DB_PATH.exists() else "否"}')
    if DB_PATH.exists():
        size = DB_PATH.stat().st_size
        mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime, BEIJING_TZ)
        print(f'  数据库大小: {size/1024:.1f} KB')
        print(f'  最后修改: {mtime.strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'  GitHub Token: {"已配置" if GITHUB_TOKEN else "未配置"}')
    print(f'  Gist ID: {GIST_ID or "未配置"}')

    if GITHUB_TOKEN and GIST_ID:
        try:
            result = github_api('GET', f'https://api.github.com/gists/{GIST_ID}')
            meta = json.loads(result['files'].get('meta.json', {}).get('content', '{}'))
            print(f'  云端最后同步: {meta.get("time", "未知")}')
        except Exception as e:
            print(f'  云端状态获取失败: {e}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == 'upload':
        upload()
    elif cmd == 'download':
        download()
    elif cmd == 'status':
        status()
    else:
        print(f'未知命令: {cmd}')
        print('可用命令: upload, download, status')
        sys.exit(1)
