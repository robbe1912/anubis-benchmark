#!/usr/bin/env python3
"""Extract content from possibly-truncated OpenAI/Anthropic response JSON.
Uses regex instead of json.loads to handle z.ai response truncation."""
import re, sys, json

def extract(path):
    raw = open(path, encoding='utf-8', errors='replace').read()
    
    # Try content field first
    for field in ['"content"', '"reasoning_content"']:
        marker = field + ':"'
        idx = raw.find(marker)
        if idx < 0:
            marker = field + ' : "'
            idx = raw.find(marker)
        if idx < 0:
            continue
        start = idx + len(marker)
        # Walk to find unescaped closing quote
        i = start
        while i < len(raw):
            if raw[i] == '\\' and i + 1 < len(raw):
                i += 2
                continue
            if raw[i] == '"':
                break
            i += 1
        if i > start:
            esc = raw[start:i]
            content = esc.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
            return content
    
    return None

if __name__ == '__main__':
    resp_path = sys.argv[1]
    out_transcript = sys.argv[2]
    out_code = sys.argv[3]
    
    content = extract(resp_path)
    if not content:
        print('ERROR: no content found')
        sys.exit(1)
    
    with open(out_transcript, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'id': '1', 'choices': [{'message': {'content': content}}]}, ensure_ascii=False) + '\n')
    with open(out_code, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'OK: {len(content)} chars extracted')
