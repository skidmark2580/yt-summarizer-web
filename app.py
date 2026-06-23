import os, re, json, urllib.request, urllib.error
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None

def fetch_via_supadata(video_id):
    api_key = os.environ.get('SUPADATA_API_KEY', '')
    url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}&text=true"
    req = urllib.request.Request(url, headers={'x-api-key': api_key, 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    content = data.get('content')
    if not content:
        raise Exception("자막 없음")
    return content if isinstance(content, str) else ' '.join(s.get('text','') for s in content)

def fetch_via_yt_api(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    for langs in [['ko'], ['en'], None]:
        try:
            fetched = api.fetch(video_id, languages=langs) if langs else api.fetch(video_id)
            return ' '.join(s.text for s in fetched)
        except:
            continue
    raise Exception("자막 없음")

def fetch_transcript(video_id):
    try:
        return fetch_via_supadata(video_id), 'supadata'
    except:
        pass
    return fetch_via_yt_api(video_id), 'yt-api'

def call_claude(transcript):
    """anthropic SDK 없이 urllib로 직접 API 호출 — 메모리 절약"""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')
    text = transcript[:8000]
    prompt = f"""유튜브 자막을 분석해서 한국어로 아래 JSON만 출력하세요. 코드블록 없이 순수 JSON만:

{{"title":"영상 제목","three_lines":["핵심1","핵심2","핵심3"],"chapters":[{{"title":"챕터명","timestamp_guide":"앞/중/후반부","summary":"2문장 요약","key_point":"핵심 한 문장"}}],"shorts_cuts":[{{"label":"쇼츠 1","timestamp_guide":"구간","title":"제목","reason":"이유"}}],"keywords":["k1","k2","k3","k4","k5","k6","k7"]}}

chapters 3~5개, shorts_cuts 3개, keywords 7개.

자막: {text}"""

    body = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    raw = resp['content'][0]['text']
    clean = raw.replace("```json","").replace("```","").strip()
    m = re.search(r'\{.*\}', clean, re.DOTALL)
    if not m:
        raise ValueError("JSON 파싱 실패")
    return json.loads(m.group())

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/summarize', methods=['POST'])
def summarize():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({'error': '유효한 유튜브 URL이 아닙니다.'}), 400
    try:
        transcript, source = fetch_transcript(video_id)
    except Exception as e:
        return jsonify({'error': '자막을 가져오지 못했습니다.', 'detail': str(e)}), 422
    try:
        result = call_claude(transcript)
        result['video_id'] = video_id
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'AI 분석 오류: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
