
import os
import re
import json
import urllib.request
import urllib.error
from flask import Flask, request, jsonify, render_template
import anthropic
 
app = Flask(__name__)
 
 
def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None
 
 
def fetch_via_supadata(video_id):
    """Supadata 무료 API로 자막 추출"""
    api_url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}&text=true"
    req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    content = data.get('content')
    if not content:
        raise Exception("자막 없음")
    if isinstance(content, str):
        return content
    return ' '.join([s.get('text', '') for s in content])
 
 
def fetch_via_youtube_transcript_api(video_id):
    """youtube-transcript-api 로 자막 추출"""
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    for langs in [['ko'], ['en'], None]:
        try:
            fetched = api.fetch(video_id, languages=langs) if langs else api.fetch(video_id)
            return ' '.join([s.text for s in fetched])
        except Exception:
            continue
    raise Exception("자막을 찾을 수 없습니다.")
 
 
def fetch_transcript(video_id):
    """Supadata 먼저 시도 → 실패 시 youtube-transcript-api 폴백"""
    try:
        text = fetch_via_supadata(video_id)
        return text, 'supadata'
    except Exception:
        pass
    text = fetch_via_youtube_transcript_api(video_id)
    return text, 'yt-api'
 
 
def build_prompt(transcript):
    return f"""아래 유튜브 영상 자막을 분석해서 한국어로 정확히 아래 JSON 형식으로만 응답하세요.
마크다운 코드블록(```)을 쓰지 말고 순수 JSON만 출력하세요.
 
{{
  "title": "영상 주제를 한 줄로 요약한 제목",
  "three_lines": ["핵심 인사이트 1", "핵심 인사이트 2", "핵심 인사이트 3"],
  "chapters": [
    {{"title": "챕터 제목", "timestamp_guide": "앞부분/중반부/후반부 등", "summary": "2-3문장 요약", "key_point": "핵심 한 문장"}}
  ],
  "shorts_cuts": [
    {{"label": "쇼츠 1", "timestamp_guide": "영상 초반/중반/후반 구체적 구간", "title": "이 구간 제목", "reason": "선택 이유"}}
  ],
  "keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5", "키워드6", "키워드7"]
}}
 
규칙:
- three_lines: 영상 전체에서 가장 중요한 인사이트 3개
- chapters: 내용 흐름에 따라 3~6개로 자연스럽게 구성
- shorts_cuts: 쇼츠로 만들기 좋은 임팩트 있는 독립 구간 3개
- keywords: 핵심 개념/주제어 7개
 
자막:
{transcript[:15000]}"""
 
 
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
        return jsonify({
            'error': '자막을 가져오지 못했습니다. 자막이 없거나 비공개 영상일 수 있습니다.',
            'detail': str(e)
        }), 422
 
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': '서버에 ANTHROPIC_API_KEY가 설정되어 있지 않습니다.'}), 500
 
    client = anthropic.Anthropic(api_key=api_key)
 
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": build_prompt(transcript)}]
        )
        raw = "".join([b.text for b in msg.content if b.type == "text"])
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        result['video_id'] = video_id
        result['transcript_source'] = source
        return jsonify(result)
    except json.JSONDecodeError:
        return jsonify({'error': 'AI 응답을 해석하지 못했습니다. 다시 시도해주세요.'}), 500
    except Exception as e:
        return jsonify({'error': f'AI 분석 중 오류: {str(e)}'}), 500
 
 
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
