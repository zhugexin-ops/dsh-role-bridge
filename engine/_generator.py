# -*- coding: utf-8 -*-
u"""生成 site/index.json（插件市场静态索引）。推送之后在仓库根目录跑：
用法： & $py -u engine\_generator.py <你的用户名>
"""
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(*args):
    return subprocess.check_output(args, cwd=ROOT).decode('utf-8', 'replace').strip()


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else 'YOUR-NAME'
    commit = sh('git', 'rev-parse', 'HEAD')
    pkg = json.load(io.open(os.path.join(ROOT, 'plugin', 'package.json'), encoding='utf-8'))
    repo = 'https://github.com/%s/dsh-role-bridge' % user
    idx = {
        'format': 1,
        'plugins': [{
            'id': 'dsh-role-bridge',
            'name': u'叫她（角色外骨骼）',
            'description': u'给 DSH 装外骨骼：角色住在硬盘上，跨会话是同一个人。',
            'homepage': repo,
            'icon': 'icons/icon.svg',
            'versions': [{
                'version': pkg['version'],
                'engines': {'dsh': pkg.get('engines', {}).get('dsh', '>=1.0.0')},
                'source': {'type': 'git', 'url': repo, 'commit': commit, 'path': 'plugin'},
            }],
        }],
    }
    out = os.path.join(ROOT, 'site', 'index.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(idx, ensure_ascii=False, indent=2))
    print(u'写了 %s（commit %s）' % (out, commit[:8]))


if __name__ == '__main__':
    main()
