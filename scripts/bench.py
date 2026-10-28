#!/usr/bin/env python3
"""Local, auditable animation benchmark recorder. No model calls or installs."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'dev-0.3'
PROTOCOL = 'animation-model-test/' + VERSION
TYPES = ('live-elements', 'live-brief', 'frozen-elements', 'frozen-design')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def boot_id():
    if sys.platform == 'darwin':
        try:
            return subprocess.check_output(['sysctl', '-n', 'kern.boottime'], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None  # Restricted hosts cannot establish a trustworthy cross-call clock domain.
    p = Path('/proc/sys/kernel/random/boot_id')
    return p.read_text().strip() if p.exists() else None


def stamp():
    return {'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'mono_ns': time.monotonic_ns(), 'boot': boot_id(), 'host': platform.node()}


def elapsed(a, b):
    if not a['boot'] or a['boot'] != b['boot'] or a['host'] != b['host']:
        return None
    delta = b['mono_ns'] - a['mono_ns']
    # Sleep or discontinuity is not silently deducted from the continuous budget.
    wall = (dt.datetime.fromisoformat(b['utc']) - dt.datetime.fromisoformat(a['utc'])).total_seconds()*1000
    ms = delta / 1e6
    return round(ms, 3) if ms >= 0 and abs(wall-ms) < 2000 else None


def save_blob(run, rel, data):
    p = run / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('xb') as f:
        f.write(data)
    p.chmod(0o444)
    return {'path': rel, 'sha256': digest(data)}


def verified(run, ref):
    p = run / ref['path']
    data = p.read_bytes()
    if digest(data) != ref['sha256']:
        raise ValueError('Frozen artifact changed: ' + ref['path'])
    return data


def event(run, m, name, **fields):
    now = stamp()
    m['seq'] = m.get('seq', 0) + 1
    row = {'seq': m['seq'], 'run_id': m['run_id'], 'event': name,
           'clock': now, 'elapsed_ms': elapsed(m['issued'], now), **fields}
    with (run/'events.jsonl').open('a') as f:
        f.write(json.dumps(row, ensure_ascii=False)+'\n')
    write_json(run/'manifest.json', m)
    return now


def validate_materials(items):
    if not isinstance(items,list) or len(items)!=4:
        raise ValueError('请先向用户收集 4 项素材；未齐备前不开始计时。')
    if any(not isinstance(x,dict) for x in items) or {x.get('id') for x in items}!={'1','2','3','4'}:
        raise ValueError('Materials require unique ids 1 through 4')
    for x in items:
        if x.get('type') not in ('text','image','url','profile') or any(not isinstance(x.get(k),str) or not x[k].strip() for k in ('name','content','requirement')):
            raise ValueError('Each user material requires name, type, content and requirement')
        if any(marker in json.dumps(x) for marker in ('BEGIN_','END_')):
            raise ValueError('Reserved packet delimiters cannot occur in materials')
    return items


def material_requirements(text):
    try:
        return validate_materials(json.loads(text.split('BEGIN_MATERIAL_REQUIREMENTS\n',1)[1].split('\nEND_MATERIAL_REQUIREMENTS',1)[0]))
    except (IndexError,json.JSONDecodeError) as e:
        raise ValueError('Missing or malformed user materials') from e


def task_packet(materials, prompt=None, mode='live-elements'):
    text=(ROOT/'assets/task.md').read_text()
    text=text.replace('BEGIN_MATERIAL_REQUIREMENTS\n[]\nEND_MATERIAL_REQUIREMENTS',
        'BEGIN_MATERIAL_REQUIREMENTS\n'+json.dumps(validate_materials(materials),ensure_ascii=False,indent=2)+'\nEND_MATERIAL_REQUIREMENTS')
    if prompt is not None: text=text.replace(creative_prompt(text),validate_prompt(prompt),1)
    return text.replace('input_type: live-elements','input_type: '+mode,1)


def validate_sources(data, materials):
    validate_materials(materials)
    if not isinstance(data, dict) or not isinstance(data.get('characters'), list):
        raise ValueError('source pack requires characters array (one record per material)')
    chars = data['characters']
    if len(chars)!=4 or {x.get('character_id') for x in chars}!={x['id'] for x in materials}:
        raise ValueError('Source records must match the four supplied materials')
    profiles={x['id'] for x in materials if x['type']=='profile'}
    required = ('source_urls', 'retrieved_at',
                'evidence_type', 'observed_features', 'uncertainties',
                'adopted_description', 'fictional_additions', 'status', 'evidence_ids')
    for c in chars:
        if any(k not in c for k in required):
            raise ValueError('Missing source fields: '+', '.join(k for k in required if k not in c))
        if not isinstance(c['adopted_description'], str) or not c['adopted_description'].strip():
            raise ValueError('Full adopted description required, including explicit fallback when unavailable')
        if c['status'] not in ('ACQUIRED', 'UNAVAILABLE', 'PARTIAL'):
            raise ValueError('Invalid source status')
        for key in ('source_urls', 'observed_features', 'uncertainties', 'fictional_additions', 'evidence_ids'):
            if not isinstance(c[key], list):
                raise ValueError(key+' must be an array')
        if c['status'] != 'ACQUIRED' and not c['uncertainties']:
            raise ValueError('Missing uncertainty for incomplete sources')
        if c['character_id'] not in profiles:
            continue
        if any(k not in c for k in ('avatar','recent_posts','inference_notes')):
            raise ValueError('Profile materials require avatar, recent_posts and inference_notes')
        avatar = c['avatar']
        if not isinstance(avatar, dict) or not isinstance(avatar.get('viewed'), bool) or not isinstance(avatar.get('observations'), list) or not isinstance(avatar.get('evidence_ids'), list):
            raise ValueError('Avatar requires viewed, observations and evidence_ids')
        if not isinstance(c['recent_posts'], list) or len(c['recent_posts']) > 5 or not isinstance(c['inference_notes'], list):
            raise ValueError('recent_posts must contain at most 5 items; inference_notes must be a list')
        nested_ids = list(avatar['evidence_ids'])
        for post in c['recent_posts']:
            if not isinstance(post, dict) or any(k not in post for k in ('url','published_at','observations','evidence_ids')):
                raise ValueError('Each recent post requires URL, date, observations and evidence_ids')
            if not isinstance(post['evidence_ids'], list) or not isinstance(post['observations'], list):
                raise ValueError('Post evidence_ids and observations must be arrays')
            nested_ids.extend(post['evidence_ids'])
            if post['published_at'] is not None:
                try:
                    received = dt.datetime.fromisoformat(c['retrieved_at'])
                    published = dt.datetime.fromisoformat(post['published_at'])
                    if received.tzinfo is None or published.tzinfo is None: raise ValueError('Timezone required')
                    age = received - published
                except (TypeError, ValueError) as e:
                    raise ValueError('Use timezone-aware ISO timestamps for dated posts and retrieval') from e
                if not dt.timedelta(0) <= age <= dt.timedelta(days=30):
                    raise ValueError('Post is outside the 30-day window')
        if not set(nested_ids).issubset(set(c['evidence_ids'])):
            raise ValueError('Avatar/post evidence must be included in character evidence_ids')
        if c['status'] == 'ACQUIRED' and not (avatar['viewed'] and avatar.get('url') and avatar['observations'] and avatar['evidence_ids'] and c['recent_posts'] and c['inference_notes'] and all(p['published_at'] and p['url'] and p['observations'] and p['evidence_ids'] for p in c['recent_posts'])):
            raise ValueError('ACQUIRED requires viewed avatar, recent posts and inference evidence; a bio alone is insufficient')
        if c['status'] != 'ACQUIRED' and not c['uncertainties']:
            raise ValueError('Missing uncertainty for incomplete sources')
    return data


def creative_prompt(text):
    try:
        value = text.split('BEGIN_CREATIVE_PROMPT\n',1)[1].split('\nEND_CREATIVE_PROMPT',1)[0]
    except IndexError as e:
        raise ValueError('Missing creative prompt block') from e
    return validate_prompt(value)


def validate_prompt(value):
    if not value.strip() or any(tag in value for tag in ('BEGIN_', 'END_')):
        raise ValueError('Nonempty creative brief required; packet delimiters are not allowed inside it')
    return value


def reuse_packet(prompt, materials):
    return task_packet(materials, prompt, 'live-brief')


def packet(base, mode, sources, design=None):
    base = base.replace(creative_prompt(base), '按下方冻结输入完成动画；资料和故事是否允许修改遵从本模式，不自行重新查询人物。', 1)
    # Replace whole mode-dependent sections, avoiding contradictory live-research instructions.
    start = base.index('二、素材准备与必要查询')
    end = base.index('四、独立作答')
    replacement = '二、冻结资料比较\n采用下列已冻结的四项素材描述，不重新查询或推导；用户原始素材仍保留在第一节。\n'
    replacement += 'BEGIN_FROZEN_SOURCES\n'+json.dumps(sources,ensure_ascii=False,indent=2)+'\nEND_FROZEN_SOURCES\n\n'
    if mode == 'frozen-design':
        replacement += '三、同稿制作\n不另编故事，不减少设计承诺；按下列完整稿件制作。\nBEGIN_FROZEN_DESIGN\n'+design+'\nEND_FROZEN_DESIGN\n\n'
    else:
        replacement += base[base.index('三、自主创作'):end]
    result = base[:start]+replacement+base[end:]
    result = result.replace('input_type: live-elements', 'input_type: '+mode)
    result = result.replace('包含查询、设计与编码', '仅包含本轮设计与编码' if mode=='frozen-elements' else '仅包含本轮实现')
    if mode == 'frozen-design':
        a, b = result.index('五、先公开设计稿'), result.index('六、动画制作要求')
        result = result[:a]+'五、设计稿\n输入设计已冻结。公开完整原稿，可以说明实现选择，不得改动必要故事事件。\n\n'+result[b:]
    return result


def parse_packet(text):
    if not text.startswith('BEGIN_ANIMATION_TEST_PACKET\n') or not text.rstrip().endswith('END_ANIMATION_TEST_PACKET'):
        raise ValueError('Incomplete packet envelope')
    if 'protocol: '+PROTOCOL+'\n' not in text:
        raise ValueError('Unsupported protocol; do not silently change versions')
    mode = next((t for t in TYPES if 'input_type: '+t+'\n' in text), None)
    if mode is None:
        raise ValueError('Missing input_type')
    materials = material_requirements(text)
    if mode == 'live-brief':
        prompt = creative_prompt(text)
        if text != reuse_packet(prompt, materials):
            raise ValueError('Reuse packet changes or omits material requirements or common rules')
        return mode, None, None
    if mode == 'live-elements':
        if text != task_packet(materials):
            raise ValueError('Custom live packet differs from fixed protocol')
        return mode, None, None
    try:
        sources = validate_sources(json.loads(text.split('BEGIN_FROZEN_SOURCES\n',1)[1].split('\nEND_FROZEN_SOURCES',1)[0]),materials)
        design = text.split('BEGIN_FROZEN_DESIGN\n',1)[1].split('\nEND_FROZEN_DESIGN',1)[0] if mode=='frozen-design' else None
    except (IndexError, json.JSONDecodeError) as e:
        raise ValueError('Missing or malformed frozen input') from e
    expected = packet(task_packet(materials), mode, sources, design)
    if text != expected:
        raise ValueError('Packet is missing or changes fixed requirements; use intact exported packet')
    return mode, sources, design


def doctor():
    result = {'skill_version': VERSION, 'python': sys.version.split()[0], 'os': platform.platform(),
              'automatic_aesthetics': 'UNAVAILABLE', 'automatic_semantics': 'UNAVAILABLE',
              'release': 'DEVELOPMENT_ONLY', 'installs_performed': False}
    try:
        from playwright.sync_api import sync_playwright
        from PIL import Image
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            ctx = browser.new_context(service_workers='block')
            page = ctx.new_page()
            page.set_content('<title>capability probe</title>')
            result.update(browser=browser.version, browser_probe=page.title()=='capability probe',
                          websocket_blocking=hasattr(ctx, 'route_web_socket'))
            ctx.close()
            browser.close()
    except Exception as e:
        result.update(browser_probe=False, reason=str(e))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def issue(args):
    attachments=[]
    if args.packet:
        if args.materials or args.prompt: raise ValueError('Use either packet or materials/prompt')
        text=Path(args.packet).read_text()
        materials=material_requirements(text)
        for item in materials:
            if item['type']=='image':
                relative=Path(item['content'])
                if relative.is_absolute() or '..' in relative.parts: raise ValueError('Packet image must be a relative attachment')
                data=(Path(args.packet).resolve().parent/relative).read_bytes()
                if digest(data)!=item.get('sha256'): raise ValueError('Image attachment missing or changed')
                attachments.append((item['content'],data))
    else:
        if not args.materials: raise ValueError('请先询问用户提供 4 项素材；信息齐备后才发题计时。')
        materials=validate_materials(json.loads(Path(args.materials).read_text()))
        for item in materials:
            if item['type']=='image':
                src=Path(item['content']).expanduser()
                if not src.is_absolute(): src=Path(args.materials).resolve().parent/src
                data=src.read_bytes()
                item['content']='materials/'+item['id']+src.suffix
                item['sha256']=digest(data)
                attachments.append((item['content'],data))
        text=task_packet(materials,Path(args.prompt).read_text() if args.prompt else None,
                         'live-brief' if args.prompt else 'live-elements')
    mode, sources, design = parse_packet(text)
    run = Path(args.run).expanduser().resolve()
    run.mkdir(parents=True, exist_ok=False)
    issued = stamp()
    m = {'run_id': uuid.uuid4().hex, 'skill_version': VERSION, 'protocol': PROTOCOL,
         'input_type': mode, 'issued': issued, 'phase': 'research' if mode in ('live-elements','live-brief') else 'design' if mode=='frozen-elements' else 'implementation',
         'environment': {'os': platform.platform(), 'model': args.model, 'client': args.client, 'identity_source': 'caller supplied, unverified', 'hidden_context': 'unknown'},
         'timing_scope': 'first_tool_after_user_material_collection',
         'prior_exposure': True,
         'bootstrap_ms': None, 'evidence': [], 'designs': [], 'attempts': [], 'notes': [],
         'protection': 'shared-filesystem hashes and read-only copies; not tamper-proof',
         'tool_hashes': {p.name:digest(p.read_bytes()) for p in sorted((ROOT/'scripts').glob('*.py'))}}
    m['task'] = save_blob(run, 'input/original-task.md', text.encode())
    m['attachments']=[save_blob(run,'input/'+name,data) for name,data in attachments]
    if mode != 'live-elements':
        m['creative_prompt'] = save_blob(run, 'input/creative-prompt.md', creative_prompt(text).encode())
    if sources is not None:
        m['sources'] = save_blob(run, 'input/source-pack.json', json.dumps(sources,ensure_ascii=False,indent=2).encode())
    if design is not None:
        m['designs'].append({**save_blob(run,'design/design-v1.md',design.encode()),'clock':issued,'note':'frozen input'})
    event(run,m,'task_issued')
    print(text)
    print('\nRUN_DIRECTORY: '+str(run))


@contextlib.contextmanager
def locked(run):
    if not (run/'manifest.json').is_file():
        raise ValueError('Not a run directory')
    with (run/'.lock').open('w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield json.loads((run/'manifest.json').read_text())


def report(run, m):
    if m['protocol'] != PROTOCOL:
        raise ValueError('Historical run protocol differs; do not rewrite old results with new rules')
    start = stamp()
    original = verified(run,m['task']).decode()
    sources = json.loads(verified(run,m['sources']))
    design = verified(run,m['designs'][-1]).decode()
    material_coverage = [{'character':c['character_id'], 'avatar_viewed':c.get('avatar',{}).get('viewed'), 'recent_post_count':len(c.get('recent_posts',[])), 'claim':c['status']} for c in sources['characters']]
    materials_complete = all(c['status']=='ACQUIRED' for c in sources['characters'])
    material_notice = '用户素材声明已落实（独立准确性未验证）' if materials_complete else '素材推导未完成：部分用户素材的采用或必要查询证据不足；本轮为降级结果。'
    if m['input_type'] in ('frozen-elements','frozen-design'): material_notice = '可选冻结素材比较：本轮没有重新查询素材；不能与 live-brief 混称同条件。'
    for e in m['evidence']:
        verified(run,e)
    exports = run/'exports'
    exports.mkdir(exist_ok=True)
    (exports/'reuse-original.md').write_text(original)
    prompt = verified(run,m['creative_prompt']).decode()
    materials=material_requirements(original)
    for ref in m.get('attachments',[]):
        data=verified(run,ref)
        target=exports/ref['path'].removeprefix('input/')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    (exports/'reuse-test.md').write_text(reuse_packet(prompt,materials))
    base = task_packet(materials)
    (exports/'reuse-elements.md').write_text(packet(base,'frozen-elements',sources))
    (exports/'reuse-design.md').write_text(packet(base,'frozen-design',sources,design))
    rows = []
    for a in m['attempts']:
        verified(run,a['artifact'])
        checks_path = run/a['directory']/'checks.json'
        checks = json.loads(checks_path.read_text()) if checks_path.exists() else {'status':'UNVERIFIED','checks':[]}
        earned = sum(c['weight'] for c in checks['checks'] if c['status']=='PASS')
        unknown = sum(c['weight'] for c in checks['checks'] if c['status']=='UNVERIFIED')
        score = None if unknown or not checks['checks'] else earned
        scores = {'understanding': {'score':None,'status':'UNAVAILABLE','reason':'No validated fixed semantic evaluator'},
                  'programming':{'score':score,'confirmed_points':earned,'unverified_points':unknown if checks['checks'] else 100,'checks':checks},
                  'aesthetics':{'score':None,'status':'UNAVAILABLE','reason':'No domain-validated local evaluator or human calibration set'},
                  'speed':{'first_submission_ms':m['attempts'][0]['elapsed_ms'],'attempt_elapsed_ms':a['elapsed_ms'],
                           'repair_ms':a.get('repair_ms'), 'timing_scope':m['timing_scope'],'budget_status':a['budget_status']},
                  'release_status':'DEVELOPMENT_ONLY','skill_version':VERSION,'material_coverage':material_coverage}
        out = run/a['directory']/'scores.json'
        write_json(out,scores)
        rows.append((a,scores))
    body = ['# 动画模型测试报告（开发版）','',material_notice,json.dumps(material_coverage,ensure_ascii=False), '自动语义与审美评分不可用；本报告不代表正式版验收通过。',
            f"输入类型：{m['input_type']}；计时范围：{m['timing_scope']}；提前曝光：{m['prior_exposure']}。",'']
    for a,s in rows:
        body.extend([f"## 尝试 {a['number']}（{'首次' if a['number']==1 else '用户要求修复'}）",
                     f"作品：[animation.html](../{a['directory']}/animation.html)",
                     f"理解：未评分；编程：{s['programming']['score']} / 100；审美：未评分。",
                     f"编程已确认得分：{s['programming']['confirmed_points']}；未验证权重：{s['programming']['unverified_points']}。",
                     f"首次提交耗时：{s['speed']['first_submission_ms']} ms；本次预算状态：{a['budget_status']}。",
                     f"详情：[scores.json](../{a['directory']}/scores.json)",''])
    body += ['## 资料与计时',json.dumps({'source_status':m.get('source_status'),'research_ms':m.get('research_ms'),
        'notes':m['notes'],'environment':m['environment']},ensure_ascii=False,indent=2),'',
        '## 同款提示词 + 四个素材要求（用户原始素材；社交账号重新推导）',(exports/'reuse-test.md').read_text(),
        '## 完整原始题面',original,'## 完整人物资料',json.dumps(sources,ensure_ascii=False,indent=2),'## 完整设计稿',design]
    result = run/'results'
    result.mkdir(exist_ok=True)
    (result/'report.md').write_text('\n\n'.join(body))
    # Escape all submitted material. The preview is an opaque-origin sandbox with no network.
    first = verified(run,m['attempts'][0]['artifact']).decode()
    csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"
    preview = '<meta http-equiv="Content-Security-Policy" content="'+csp+'">'+first
    doc = '<!doctype html><meta charset="utf-8"><title>动画测试报告 · 开发版</title><h1>动画测试报告 · 开发版</h1><p>审美与语义评分不可用。首次原版预览：</p>'
    doc += '<iframe title="首次动画" sandbox="allow-scripts" width="100%" height="720" srcdoc="'+html.escape(preview,quote=True)+'"></iframe>'
    doc += '<p>'+html.escape(material_notice)+'</p><p>预览仅用于观看；以隔离验证器记录为准。</p>'
    for name,label in [('reuse-test.md','主要复用：同款提示词 + 四个素材要求，社交账号重新推导'),('reuse-original.md','原始题面存档'),('reuse-elements.md','可选：冻结素材比较，不重新推导'),('reuse-design.md','可选：冻结设计比较，不重新推导')]:
        doc += '<details><summary>'+label+'（完整可复制）</summary><textarea readonly rows="18" cols="100">'+html.escape((exports/name).read_text())+'</textarea></details>'
    doc += '<pre>'+html.escape('\n\n'.join(body))+'</pre>'
    (result/'report.html').write_text(doc)
    m['reporting_ms'] = elapsed(start,stamp())
    m['phase']='delivered'
    event(run,m,'run_finished',report='results/report.html')
    print(str(result/'report.html'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor')
    q = sub.add_parser('export-reuse')
    q.add_argument('--prompt',required=True)
    q.add_argument('--output',required=True)
    q.add_argument('--materials',required=True,help='Four user materials; image attachments must accompany the output')
    q = sub.add_parser('issue')
    q.add_argument('--run',required=True)
    q.add_argument('--packet')
    q.add_argument('--materials',help='Agent-normalized user materials JSON')
    q.add_argument('--prompt',help='Optional user-supplied creative brief')
    q.add_argument('--exposed',action='store_true')
    q.add_argument('--model',default='unknown')
    q.add_argument('--client',default='unknown')
    for name in ['status','evidence','sources','design','submit','validate','report','note','repair-start']:
        q = sub.add_parser(name)
        q.add_argument('--run',required=True)
        if name in ['evidence','sources','design','submit']: q.add_argument('--file',required=True)
        if name in ['note','repair-start','design']: q.add_argument('--note',required=name!='design',default='')
        if name=='design': q.add_argument('--prompt',help='Standalone creative brief without inferred material descriptions')
        if name=='evidence':
            q.add_argument('--url',required=True)
            q.add_argument('--kind',required=True,choices=['tool-output','page','image'])
        if name=='validate': q.add_argument('--attempt',type=int,default=1)
    args = p.parse_args()
    if args.command=='doctor': return doctor()
    if args.command=='issue': return issue(args)
    if args.command=='export-reuse':
        materials=validate_materials(json.loads(Path(args.materials).read_text()))
        if any(x['type']=='image' for x in materials):
            raise ValueError('For images, use a run report to export hash-verified companion attachments')
        text = reuse_packet(Path(args.prompt).read_text(),materials)
        with Path(args.output).open('x') as f: f.write(text)
        print(args.output)
        return
    run = Path(args.run).expanduser().resolve()
    with locked(run) as m:
        cmd=args.command
        verified(run,m['task'])
        if cmd=='status':
            now=stamp()
            print(json.dumps({'phase':m['phase'],'elapsed_ms':elapsed(m['issued'],now),
                'last_event':m['seq'],'designs':len(m['designs']),'submissions':len(m['attempts']),
                'research_remaining_ms': max(0,120000-(elapsed(m['issued'],now) or 120000)) if m['phase']=='research' else None},ensure_ascii=False))
        elif cmd=='evidence':
            if m['phase']!='research': raise ValueError('Evidence collection is restricted to research')
            now=stamp(); age=elapsed(m['issued'],now)
            ref=save_blob(run,f"evidence/sources/{len(m['evidence'])+1:03d}{Path(args.file).suffix}",Path(args.file).read_bytes())
            e={**ref,'id':len(m['evidence'])+1,'url':args.url,'kind':args.kind,'clock':now,
               'late':age is None or age>120000,'provenance':'host export supplied; independent verification pending'}
            m['evidence'].append(e); event(run,m,'source_evidence_saved',evidence=e)
            print(json.dumps(e,ensure_ascii=False))
        elif cmd=='sources':
            if m['phase']!='research': raise ValueError('Sources already frozen or not applicable')
            data=validate_sources(json.loads(Path(args.file).read_text()), material_requirements(verified(run,m['task']).decode()))
            valid={e['id'] for e in m['evidence'] if not e['late']}
            for c in data['characters']:
                if not set(c['evidence_ids']).issubset(valid): raise ValueError('Unknown or late evidence cannot be adopted')
            m['sources']=save_blob(run,'input/source-pack.json',json.dumps(data,ensure_ascii=False,indent=2).encode())
            m['research_ms']=elapsed(m['issued'],stamp())
            m['source_status']={'claims':[c['status'] for c in data['characters']], 'accuracy':'UNVERIFIED',
                                'budget_exceeded':m['research_ms'] is None or m['research_ms']>120000}
            m['phase']='design'; event(run,m,'source_pack_frozen')
        elif cmd=='design':
            if m['input_type']=='frozen-design' or m['attempts']: raise ValueError('Cannot change frozen input or submitted design')
            if m['phase'] not in ('design','implementation'): raise ValueError('Freeze sources first')
            data=Path(args.file).read_bytes()
            if not data.strip(): raise ValueError('Empty design')
            if m['designs'] and not args.note: raise ValueError('Design revision requires --note describing changes')
            if not m.get('creative_prompt'):
                if not args.prompt: raise ValueError('First design requires --prompt: standalone reusable creative brief')
                prompt=validate_prompt(Path(args.prompt).read_text())
                m['creative_prompt']=save_blob(run,'input/creative-prompt.md',prompt.encode())
            elif args.prompt and Path(args.prompt).read_bytes()!=verified(run,m['creative_prompt']):
                raise ValueError('The reusable creative prompt is frozen; start a new run for a changed brief')
            d=save_blob(run,f"design/design-v{len(m['designs'])+1}.md",data)
            m['designs'].append({**d,'clock':stamp(),'note':args.note})
            m['phase']='implementation'; event(run,m,'design_frozen',design=d)
        elif cmd=='repair-start':
            if not m['attempts'] or m.get('repair_started'): raise ValueError('Repair needs prior submission and no pending repair')
            m['repair_started']=stamp(); m['notes'].append({'kind':'repair_authorization','text':args.note})
            m['phase']='repair'; event(run,m,'repair_started',authorization=args.note)
        elif cmd=='submit':
            if not m['designs'] or 'sources' not in m: raise ValueError('Freeze sources and design before submission')
            if m['attempts'] and not m.get('repair_started'): raise ValueError('First result cannot be overwritten; authorized repair-start required')
            verified(run,m['sources']); verified(run,m['designs'][-1])
            now=stamp(); age=elapsed(m['issued'],now)
            n=len(m['attempts'])+1; directory=f'submissions/attempt-{n:02d}'
            artifact=save_blob(run,directory+'/animation.html',Path(args.file).read_bytes())
            design=save_blob(run,directory+'/design.md',verified(run,m['designs'][-1]))
            a={'number':n,'directory':directory,'artifact':artifact,'design':design,'clock':now,'elapsed_ms':age,
               'budget_status':'TIMING_INTERRUPTED' if age is None else 'TIMEOUT' if age>900000 else 'WITHIN_BUDGET',
               'repair_ms':elapsed(m['repair_started'],now) if m.get('repair_started') else None}
            m.pop('repair_started',None); m['attempts'].append(a); m['phase']='submitted'
            event(run,m,'submission_received',attempt=a)
            print(json.dumps(a,ensure_ascii=False))
        elif cmd=='validate':
            if not 1<=args.attempt<=len(m['attempts']): raise ValueError('No such frozen attempt')
            a=m['attempts'][args.attempt-1]; verified(run,a['artifact']); verified(run,a['design'])
            dest=run/a['directory']
            if (dest/'checks.json').exists(): raise ValueError('Validation already recorded; preserve original feedback')
            start=stamp()
            try:
                proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/verify_browser.py'),str(dest)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                try:
                    stdout,stderr=proc.communicate(timeout=110)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGTERM)
                    stdout,stderr=proc.communicate(timeout=5)
                    raise RuntimeError('Verification timed out; terminated only this verification process group')
                if proc.returncode:
                    raise RuntimeError(stderr[-1600:] or stdout[-1600:])
            except Exception as e:
                write_json(dest/'checks.json',{'status':'UNVERIFIED','checks':[], 'reason':str(e)})
            m['phase']='validated'; event(run,m,'validation_finished',attempt=args.attempt,validation_ms=elapsed(start,stamp()))
            print((dest/'checks.json').read_text())
        elif cmd=='note':
            m['notes'].append({'clock':stamp(),'text':args.note}); event(run,m,'condition_noted',text=args.note)
        elif cmd=='report':
            if not m['attempts']: raise ValueError('No submission to report')
            report(run,m)


if __name__=='__main__':
    try: main()
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as e:
        print('ERROR: '+str(e),file=sys.stderr); sys.exit(2)
