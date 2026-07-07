import json

path = '/Users/apple/.claude/projects/-Users-apple-Projects-audit-ai/519457d1-b9fb-4292-910d-c87adc274049.jsonl'

types_count = {}
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        t = obj.get('type')
        types_count[t] = types_count.get(t, 0) + 1

print(types_count)
