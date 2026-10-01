"""Export inspectable evidence for offline review. Never changes live policy."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from runtime_store import atomic_json


def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def verify_sources(facts):
    """Detect missing or changed originals before a later review/replay."""
    problems=[]
    for item in facts.get('files',[]):
        path=Path(item['path'])
        if not path.is_file():problems.append(dict(path=str(path),reason='missing'))
        elif file_hash(path)!=item['sha256']:problems.append(dict(path=str(path),reason='changed'))
    return problems


def build_review(run, output):
    run,output=Path(run).resolve(),Path(output).resolve()
    if not run.is_dir():raise ValueError('Run directory does not exist')
    if output==run or run in output.parents:
        raise ValueError('Review output must be outside its source run')
    output.mkdir(parents=True,exist_ok=False)
    rows=[]
    gaps=[]
    def document(path,required=False):
        if not path.exists():
            if required:gaps.append(dict(path=str(path),reason='missing'))
            return None
        try:
            value=json.loads(path.read_text())
            if not isinstance(value,dict):raise ValueError('expected object')
            return value
        except (ValueError,OSError) as exc:
            gaps.append(dict(path=str(path),reason='unreadable_json',detail=str(exc)))
            return None
    for name in ('events.previous.jsonl','events.jsonl'):
        path=run/name
        if path.exists():
            for number,line in enumerate(path.read_text().splitlines(),1):
                if line.strip():
                    try:
                        row=json.loads(line)
                        if not isinstance(row,dict):raise ValueError('expected event object')
                    except ValueError as exc:
                        gaps.append(dict(path=str(path),line=number,reason='unreadable_event',detail=str(exc)))
                        continue
                    row.setdefault('kind','legacy_event')
                    if not isinstance(row['kind'],str):
                        gaps.append(dict(path=str(path),line=number,reason='invalid_event_kind'))
                        continue
                    row['source_line']=number
                    row['source_file']=str(path)
                    rows.append(row)
    if not rows:gaps.append(dict(reason='event_timeline_missing'))
    result=document(run/'result.json',True)
    recording=document(run/'recording.json',True)
    document(run/'config.json',True)
    document(run/'run.json',True)
    if recording:
        for name in ('dropped_records','skipped_clips','evicted_clips','event_rotations'):
            if recording.get(name):gaps.append(dict(reason=name,count=recording[name]))
        if recording.get('writer_stopped') is False or recording.get('writer_alive') is True or recording.get('error'):
            gaps.append(dict(reason='recording_not_finalized',recording=recording))
    counts=Counter(row['kind'] for row in rows)
    failures=[r for r in rows if r['kind'] in ('action_failed','action_rejected','audio_unavailable')
              or r['kind']=='skill_finished' and isinstance(r.get('result'),dict) and r['result'].get('status')!='completed']
    if result and (result.get('status') in ('failed','cancelled') or result.get('verified') is False
                   or result.get('state') in ('STOPPED','FAILED')):
        failures.append(dict(kind='terminal_result',result=result,source_file=str(run/'result.json')))
    clips=[]
    for path in sorted((run/'evidence').glob('*/manifest.json')):
        manifest=document(path,True)
        if not manifest:continue
        clips.append(dict(path=str(path),manifest=manifest))
        if not all(manifest.get(key) is True for key in ('pre_window_complete','post_window_complete')):
            gaps.append(dict(path=str(path),reason='incomplete_event_window'))
        for frame in manifest.get('frame_times',[]):
            if isinstance(frame,list) and frame and isinstance(frame[0],int):
                frame_path=path.parent/f'{frame[0]:08d}.jpg'
                if not frame_path.is_file():gaps.append(dict(path=str(frame_path),reason='referenced_frame_missing'))
        if manifest.get('audio_available') and not (path.parent/'audio.jsonl').is_file():
            gaps.append(dict(path=str(path.parent/'audio.jsonl'),reason='referenced_audio_missing'))
    files=[]
    for path in sorted(run.rglob('*')):
        if path.is_symlink():
            gaps.append(dict(path=str(path),reason='symlink_source_not_exported'))
        elif path.is_file():
            before=path.stat()
            sha256=file_hash(path)
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                gaps.append(dict(path=str(path),reason='source_changed_during_export'))
            files.append(dict(path=str(path),relative_path=str(path.relative_to(run)),
                              sha256=sha256,size=after.st_size))
    with (output/'timeline.jsonl').open('w') as stream:
        for row in rows:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    facts=dict(run=str(run),result=result,event_counts=dict(counts),failures=failures,
               evidence_clips=clips,files=files,history_may_be_rotated=(run/'events.previous.jsonl').exists(),
               recording=recording,evidence_gaps=gaps,complete=not gaps,
               timeline=str(output/'timeline.jsonl'),source_storage='referenced_originals_with_hashes')
    atomic_json(output/'facts.json',facts)
    reason=str((result or {}).get('reason',''))
    hypotheses=[]
    changes=[]
    if 'progress' in reason or 'obstruct' in reason or 'oscillat' in reason:
        hypotheses.append('位置反馈未取得进展；障碍、转向误差或坐标识别误差仍需通过连续证据区分。')
        changes.append(dict(type='route_candidate',proposal='复核失败路段并录制替代途经点；未经往返验证不加入已验证路线。',activation='review_required'))
    if 'marker' in reason or 'calibration' in reason:
        hypotheses.append('当前画面或候选不满足已有标定；这不能证明目标不存在。')
        changes.append(dict(type='calibration_candidate',proposal='保存当前正负例，复核 ROI／模板；不得直接降低全局确认门槛。',activation='review_required'))
    if 'loot_messages' in reason or 'receipt' in reason:
        hypotheses.append('动作结果缺乏新增回执证据；需区分未取得物品与聊天滚动／识别漏检。')
        changes.append(dict(type='receipt_candidate',proposal='将点击前后连续帧加入回放，检查消息顺序与 OCR 回退；不得将点击成功改写为收获成功。',activation='review_required'))
    atomic_json(output/'proposal.json',dict(status='awaiting_review',hypotheses=hypotheses,changes=changes,
        regression_results=[],live_validation=[],activate=False))
    report=['# 运行证据复盘','',f'来源：{run}', '',
            '这是事实导出；不会自动调整阈值、策略或代码。','',
            f'最终结果：{result.get("reason",result.get("state","见事实文件")) if result else "缺少最终结果"}',
            f'已记录事件：{len(rows)}；失败／拒绝事件：{len(failures)}；证据片段：{len(clips)}。','',
            f'证据缺口：{len(gaps)} 项；详情见 facts.json 的 evidence_gaps。',
            'timeline.jsonl 保存可定位的事件行；片段引用原始文件，复盘或回放前应校验 files 中的哈希。','',
            '## 待审改进流程','',
            '1. 根据 facts.json 和原始证据区分已知事实与原因假设。',
            '2. 在 proposal.json 中记录修改候选、对应失败和预计影响。',
            '3. 用独立正负例与完整事件序列回放验证；记录全部失败。',
            '4. 完成有界实机复验后审核启用；不得仅凭离线通过修改能力验收状态。','']
    (output/'review.md').write_text('\n'.join(report))
    return facts


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    result=build_review(a.run,a.output)
    print(json.dumps(dict(output=str(a.output),failures=len(result['failures'])),ensure_ascii=False))
