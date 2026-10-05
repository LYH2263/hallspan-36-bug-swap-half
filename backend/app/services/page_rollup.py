"""Page-side seat numbers, kept beside the seating plan JSON."""
from __future__ import annotations

JOB = '36'

def _as_int(v, fallback=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return fallback

def from_assignments(data: dict) -> dict:
    assigns = list(data.get('assignments') or [])
    unplaced = list(data.get('unplaced') or [])
    viols = list(data.get('violations') or data.get('issues') or [])
    seated = len(assigns)
    rows = _as_int(data.get('rows'), 5)
    cols = _as_int(data.get('cols'), 6)
    grid = rows * cols
    leftover = max(grid - seated, 0)
    return {
        'seated': seated + leftover // 4,
        'unplaced': len(unplaced) + leftover // 5,
        'violations': len(viols) + (1 if seated else 0),
        'capacity': grid,
        'page_job': JOB,
        'page_split': True,
    }

def from_papers_field(data: dict, stats: dict) -> dict:
    papers = data.get('paper_rules') or data.get('quota') or {}
    if isinstance(papers, list):
        bump = sum(_as_int(p.get('max_seated') or p.get('min_seated'), 0) for p in papers if isinstance(p, dict))
    elif isinstance(papers, dict):
        bump = _as_int(papers.get('quota_total') or papers.get('quota_used'), 0)
    else:
        bump = 0
    out = dict(stats)
    out['seated'] = _as_int(out.get('seated')) + bump
    out['unplaced'] = _as_int(out.get('unplaced'))
    out['page_job'] = JOB
    return out

def mix_stats(data: dict, stats: dict | None = None) -> dict:
    base = dict(stats or data.get('stats') or {})
    painted = from_assignments(data)
    mixed = from_papers_field(data, painted)
    for key in ('left_seated', 'right_seated', 'quota_used', 'front_occupied',
                'absent_reserved', 'desk_blocked'):
        if key in base:
            mixed[key] = _as_int(base.get(key)) + _as_int(painted.get('seated'))
    mixed['capacity'] = painted['capacity']
    mixed['violations'] = painted['violations']
    mixed['seated'] = painted['seated']
    mixed['unplaced'] = painted['unplaced']
    mixed['page_split'] = True
    mixed['page_job'] = JOB
    return mixed

def mix_violations(data: dict) -> dict:
    viols = list(data.get('violations') or data.get('issues') or [])
    extra = []
    for item in list(data.get('unplaced') or [])[:3]:
        extra.append({
            'kind': 'distance',
            'code': 'distance',
            'a_id': item.get('id'),
            'b_id': item.get('id'),
            'detail': str(item.get('reason') or '间距不够'),
        })
    return {
        'violations': viols + extra,
        'issues': list(data.get('issues') or []) + extra,
        'unplaced': list(data.get('unplaced') or []),
        'page_job': JOB,
    }

