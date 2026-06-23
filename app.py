import os
import re
import json
import urllib.request
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)


def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


def fetch_via_supadata(video_id):
    api_key = os.environ.get('SUPADATA_API_KEY', '')
    url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}&text=true"
    req = urllib.request.Request(url, headers={'x-api-key': api_key, 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    content = data.get('content')
    if not content:
        raise Exception("자막 없음")
    if isinstance(content, str):
        return content
    return ' '.join([s.get('text', '') for s in content])


def fetch_via_yt_api(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    for langs in [['ko'], ['en'], None]:
        try:
            fetched = api.fetch(video_id, languages=langs) if langs else api.fetch(video_id)
            return ' '.join([s.text for s in fetched])
        except Exception:
            continue
    raise Exception("자막 없음")


def fetch_transcript(video_id):
    try:
        return fetch_via_supadata(video_id), 'supadata'
    except Exception:
        pass
    try:
        return fetch_via_yt_api(video_id), 'yt-api'
    except Exception as e:
        raise Exception(str(e))


def build_prompt(transcript):
    # 8000자로 제한 → API 응답 속도 향상
    text = transcript[:8000]
    return f"""유튜브 자막을 분석해서 한국어로 아래 JSON만 출력하세요. 코드블록 없이 순수 JSON만:

{{"title":"영상 제목 한 줄","three_lines":["핵심1","핵심2","핵심3"],"chapters":[{{"title":"챕터명","timestamp_guide":"앞/중/후반부","summary":"2문장 요약","key_point":"핵심 한 문장"}}],"shorts_cuts":[{{"label":"쇼츠 1","timestamp_guide":"구간","title":"제목","reason":"이유"}}],"keywords":["k1","k2","k3","k4","k5","k6","k7"]}}

chapters 3~5개, shorts_cuts 3개, keywords 7개.

자막:
{text}"""


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
        return jsonify({'error': '자막을 가져오지 못했습니다. 자막이 없거나 비공개 영상일 수 있습니다.', 'detail': str(e)}), 422

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY가 설정되지 않았습니다.'}), 500

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": build_prompt(transcript)}]
        )
        raw = "".join([b.text for b in msg.content if b.type == "text"])
        clean = raw.replace("```json", "").replace("```", "").strip()
        # JSON 부분만 추출
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if not match:
            raise ValueError("JSON 없음")
        result = json.loads(match.group())
        result['video_id'] = video_id
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': f'AI 분석 오류: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
