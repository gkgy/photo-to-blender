"""Build a local, versioned delivery from an inspected photo-to-Blender project."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import shutil
import struct
import uuid
from urllib.parse import quote
from zipfile import ZipFile, ZIP_DEFLATED


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def within(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f'Expected project-relative path: {relative!r}')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f'Path leaves project: {relative}')
    return path


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def png_size(path):
    with path.open('rb') as f:
        data = f.read(24)
    if len(data) != 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'Not a PNG render: {path.name}')
    return struct.unpack('>II', data[16:24])


def build_page(title, pictures, blend, review, draft):
    esc = html.escape
    cards = '\n'.join(
        f'<button data-view="{i}">{esc(p["label"])}</button>' for i,p in enumerate(pictures)
    )
    payload = json.dumps(pictures, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    remaining = review.get('remaining_differences', [])
    notes = '\n'.join(f'<li>{esc(x if isinstance(x,str) else json.dumps(x,ensure_ascii=False))}</li>' for x in remaining)
    state = '预览 · 评估未完成' if draft else '已记录评估 · 详见剩余差异'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 模型与对比</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf1f3;color:#243a44;font:15px/1.6 "Microsoft YaHei",sans-serif}}header{{padding:23px 4vw;background:white}}h1{{font-size:25px;margin:0}}p{{margin:6px 0;color:#617681}}main{{max-width:1450px;margin:20px auto;padding:0 20px;display:grid;grid-template-columns:195px 1fr;gap:20px}}nav{{display:flex;flex-direction:column;gap:7px}}button,a{{font:inherit}}button{{background:white;color:#345563;padding:10px;border:1px solid #d5dfe3;border-radius:7px;text-align:left;cursor:pointer}}button.active{{background:#345d6b;color:white}}a{{color:#31596a}}article{{background:white;border-radius:12px;overflow:hidden;border:1px solid #d9e1e5}}.toolbar{{padding:10px 16px;display:flex;gap:18px;align-items:center}}.viewer{{display:grid;grid-template-columns:1fr;min-height:350px;height:65vh}}.viewer.compare{{grid-template-columns:1fr 1fr}}figure{{margin:0;min-width:0;display:flex;flex-direction:column;background:#f6f7f8}}figure[hidden]{{display:none}}figure img{{width:100%;min-height:0;flex:1;object-fit:contain}}figcaption{{text-align:center;padding:7px;font-size:12px}}footer{{padding:15px 20px;border-top:1px solid #e0e7ea}}small{{font-size:12px;color:#67808b}}details{{padding:14px 4vw;background:white}}@media(max-width:700px){{main{{display:block}}nav{{display:grid;grid-template-columns:1fr 1fr;margin-bottom:15px}}.viewer{{height:58vh}}.toolbar{{flex-wrap:wrap}}}}
</style><header><h1>{esc(title)}</h1><p>{state}</p><small>可编辑 Blender 工程 · 原图保留 · 几何检查与视觉评估分别记录</small></header>
<main><nav>{cards}<a href="{quote(blend, safe='/')}">打开 Blender 工程</a><a href="reviews/review.json">查看评估记录</a></nav>
<article><div class="toolbar"><label><input type="checkbox" id="compare"> 与原图并排</label><a id="full" target="_blank" rel="noopener">查看原尺寸</a></div><div class="viewer" id="viewer"><figure><img id="image" alt="模型视图"><figcaption id="caption"></figcaption></figure><figure id="referenceFigure" hidden><img id="reference" alt="原始参考图"><figcaption>原始参考图</figcaption></figure></div><footer><p id="description"></p><small>并排图用于人工比较；未自动对齐视角或校准尺寸。</small></footer></article></main>
<details><summary>剩余差异与推定部分</summary><ul>{notes or '<li>请查看评估记录；未列出差异不等于整体严格 1:1。</li>'}</ul></details>
<script>const items={payload};let current=0;const buttons=[...document.querySelectorAll('[data-view]')];const reference=items.find(x=>x.kind==='reference');function show(i){{current=i;const x=items[i];document.getElementById('image').src=x.url;document.getElementById('full').href=x.url;document.getElementById('caption').textContent=x.label;document.getElementById('description').textContent=x.description;buttons.forEach((b,j)=>b.classList.toggle('active',i===j));}}buttons.forEach((b,i)=>b.onclick=()=>show(i));document.getElementById('compare').onchange=e=>{{const on=e.target.checked&&!!reference;document.getElementById('viewer').classList.toggle('compare',on);document.getElementById('referenceFigure').hidden=!on;if(reference)document.getElementById('reference').src=reference.url;}};show(0);</script></html>'''


def deliver(project, draft=False):
    root = Path(project).resolve(strict=True)
    config = read_json(root/'project.json')
    if config.get('schema_version') != 1:
        raise ValueError('Unsupported project schema')
    review_file = root/'reviews/review.json'
    review = read_json(review_file)
    if not draft:
        if review.get('status') not in {'reviewed', 'accepted', 'needs_refinement'}:
            raise ValueError('Visual review is pending. Review first or use --draft for a clearly labelled preview.')
        if review.get('blockers'):
            raise ValueError('Review contains blockers. Resolve them or use --draft.')
    blend = within(root,config['blend_file'])
    if not blend.is_file() or blend.stat().st_size == 0:
        raise ValueError('The derived Blender file is missing or empty')
    inspection = root/'logs/blender_inspection.json'
    if not draft and not inspection.is_file():
        raise ValueError('Run a fresh-process Blender inspect before delivery')
    if not draft:
        checked=read_json(inspection)
        if Path(checked.get('loaded_blend','')).resolve()!=blend or checked.get('loaded_blend_sha256')!=digest(blend):
            raise ValueError('Inspection is stale or refers to a different blend; reopen the current derived file and inspect it')
        if not checked.get('background_process') or checked.get('in_memory_dirty'):
            raise ValueError('Final delivery requires a saved file inspected in a separate background Blender process')
        if checked.get('nonfinite_meshes') or not checked.get('unit_scale_matches_manifest'):
            raise ValueError('Inspection found invalid geometry or a unit mismatch')
        if not checked.get('subject_object_count') or not checked.get('bounds'):
            raise ValueError('Inspection has no measurable subject geometry')
        checked_refs={r['path']:r for r in checked.get('references',[])}
        for ref in config.get('reference_images',[]):
            r=checked_refs.get(ref['path'],{})
            if not r.get('source_matches_manifest') or not r.get('packed_and_persistent'):
                raise ValueError('Inspection has no valid packed, persistent reference evidence for '+ref['path'])
    files = {'project.json','brief.json','reviews/review.json',config['blend_file']}
    if inspection.is_file(): files.add('logs/blender_inspection.json')
    pictures=[]
    for job in config.get('render_jobs',[]):
        p=within(root,job['file'])
        if not p.is_file():
            if draft: continue
            raise ValueError(f'Missing configured render: {job["file"]}')
        w,h=png_size(p)
        if (w,h)!=(job['width'],job['height']):
            raise ValueError(f'Render size mismatch: {job["file"]}: {w} x {h}')
        files.add(job['file'])
        pictures.append({'url':quote(job['file'],safe='/'),'label':job.get('label',p.stem),'kind':job.get('kind','beauty'),'description':f'{w} × {h} · '+('灰模结构检查' if job.get('kind')=='gray' else '模型渲染')})
    if not pictures or (not draft and not any(p['kind']=='beauty' for p in pictures)):
        raise ValueError('No usable render, or final delivery lacks a beauty view')
    refs=config.get('reference_images',[])
    if not refs:raise ValueError('Project has no preserved source image')
    for ref in refs:
        p=within(root,ref['path'])
        if not p.is_file() or digest(p)!=ref['sha256']:raise ValueError(f'Source image changed: {ref["path"]}')
        files.add(ref['path'])
        pictures.append({'url':quote(ref['path'],safe='/'),'label':'原图 · '+ref.get('original_name',p.name),'kind':'reference','description':'原始字节与项目初始化记录一致'})
    baseline=config.get('baseline_image')
    if baseline:
        p=within(root,baseline)
        if not p.is_file():raise ValueError('Baseline image missing')
        files.add(baseline);pictures.append({'url':quote(baseline,safe='/'),'label':'上一版','kind':'baseline','description':'项目指定的上一版效果图'})
    for rel in files:
        if not within(root,rel).is_file():raise ValueError(f'Missing delivery file: {rel}')
    delivery=within(root,'delivery');delivery.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
    out=delivery/('preview-' if draft else 'release-')/stamp
    out.mkdir(parents=True,exist_ok=False)
    for rel in sorted(files):
        target=out/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(within(root,rel),target)
    (out/'index.html').write_text(build_page(config['title'],pictures,config['blend_file'],review,draft),encoding='utf-8')
    manifest={'schema_version':1,'title':config['title'],'draft':draft,'created_utc':stamp,'visual_review_status':review.get('status'),'warning':'File checks do not establish visual similarity.','files':[{'path':str(p.relative_to(out)).replace('\\','/'),'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(out.rglob('*')) if p.is_file()]}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=out.with_suffix('.zip')
    with ZipFile(archive,'x',ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(out).as_posix())
    with ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('Archive CRC validation failed')
    result={'directory':str(out),'gallery':str(out/'index.html'),'archive':str(archive),'files':len(manifest['files'])+1,'archive_bytes':archive.stat().st_size,'draft':draft}
    (delivery/'latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--project',required=True);ap.add_argument('--draft',action='store_true');args=ap.parse_args()
    try:print(json.dumps(deliver(args.project,args.draft),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as e:ap.exit(1,f'Delivery stopped: {e}\n')
