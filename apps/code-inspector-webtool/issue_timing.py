"""按已记录的生命周期计算自然用时，包含等待与返工。"""
from datetime import datetime, timezone


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def timing(start, end, now, running=False):
    a, b = parse_time(start), parse_time(end) if end else now if running else None
    elapsed = '未开始' if not start else '时间未记录'
    if a and b and b >= a:
        minutes = int((b-a).total_seconds() // 60)
        days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
        elapsed = (f'{days}天 ' if days else '') + (f'{hours}小时 ' if hours else '') + f'{mins}分钟'
    return dict(start=start, end=end, elapsed=elapsed, running=running)


def attach_timing(issue, stages, activities, now=None):
    now = now or datetime.now(timezone.utc)
    terminal = issue['status'] in {'CONFIRMED', 'CANCELLED'}
    ends = [a.get('created_at') for a in activities
            if a.get('result_status') == issue['status'] or a['activity_type'] == issue['status']]
    issue['timing'] = timing(issue.get('created_at'), ends[-1] if terminal and ends else None, now, not terminal)
    if not issue.get('created_at'):
        issue['timing']['elapsed'] = '时间未记录'
    for stage in stages:
        matches = [a for a in activities if (a.get('metadata') or {}).get('plan_no') == stage['plan_no']]
        earlier = sorted([s for s in stages if s['plan_no'] == stage['plan_no'] and s['stage_no'] < stage['stage_no']], key=lambda s:s['stage_no'])
        start = None
        if not earlier:
            start = next((a.get('created_at') for a in matches if a['activity_type'] == 'STAGE_PLAN_CREATED'), None)
        else:
            previous = earlier[-1]
            start = next((a.get('created_at') for a in matches if a['activity_type'] == 'STAGE_APPROVED' and (a.get('metadata') or {}).get('stage_no') == previous['stage_no']), previous.get('approved_at'))
        own = [a for a in matches if (a.get('metadata') or {}).get('stage_no') == stage['stage_no']]
        if stage['status'] == 'PLANNED':
            start = None
        else:
            start = start or next((a.get('created_at') for a in own if a['activity_type'] == 'STAGE_SCOPE_DECLARED'), None) or stage.get('prepared_at')
        end = stage.get('approved_at') if stage['status'] == 'APPROVED' else None
        if stage.get('plan_status') == 'SUPERSEDED' or stage['status'] == 'SUPERSEDED':
            end = next((a.get('created_at') for a in matches if a['activity_type'] == 'STAGE_PLAN_SUPERSEDED'), None)
        running = stage['status'] in {'IN_PROGRESS', 'PENDING_REVIEW'} and stage.get('plan_status', 'ACTIVE') == 'ACTIVE' and not terminal
        stage['timing'] = timing(start, end, now, running)
        if not start and stage['status'] != 'PLANNED':
            stage['timing']['elapsed'] = '开始时间未记录'
