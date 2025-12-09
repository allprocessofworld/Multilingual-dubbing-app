import streamlit as st
import re
from datetime import datetime, timedelta
import requests
import io
from pydub import AudioSegment, effects

# --- 1. 기본 설정 및 함수 정의 ---

def parse_srt_time(time_str):
    """SRT 시간 문자열(00:00:00,000)을 밀리초(ms)로 변환"""
    time_str = time_str.replace(',', '.')
    t = datetime.strptime(time_str, "%H:%M:%S.%f")
    delta = timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond)
    return delta.total_seconds() * 1000

def parse_srt(srt_content):
    """SRT 내용을 파싱하여 (시작시간, 종료시간, 텍스트) 리스트로 반환"""
    pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\d+\n).)*)', re.DOTALL)
    matches = pattern.findall(srt_content)
    
    parsed_data = []
    for idx, start, end, text in matches:
        start_ms = parse_srt_time(start)
        end_ms = parse_srt_time(end)
        duration_ms = end_ms - start_ms
        clean_text = text.strip().replace('\n', ' ')
        parsed_data.append({
            'start_ms': start_ms,
            'end_ms': end_ms,
            'duration_ms': duration_ms,
            'text': clean_text
        })
    return parsed_data

def generate_audio(text, voice_id, api_key):
    """ElevenLabs API 호출"""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2", # 모델 변경 가능
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    response = requests.post(url, json=data, headers=headers)
    if response.status_code == 200:
        return response.content
    else:
        st.error(f"API Error: {response.text}")
        return None

def match_target_duration(audio_segment, target_duration_ms):
    """오디오 길이를 타임코드 길이에 맞춤 (속도 조절 or 무음 추가)"""
    current_duration_ms = len(audio_segment)
    
    if current_duration_ms == 0:
        return AudioSegment.silent(duration=target_duration_ms)

    # 1. 오디오가 타임코드보다 길 때 -> 속도를 높임 (Speed Up)
    if current_duration_ms > target_duration_ms:
        speed_factor = current_duration_ms / target_duration_ms
        # 속도가 너무 빨라지면(예: 1.5배 이상) 음질이 깨지므로 경고 필요
        # pydub의 speedup은 간단한 구현이므로 퀄리티가 중요하면 전문 DSP 라이브러리 필요
        # 여기서는 단순히 프레임 속도를 조절하여 길이를 맞춥니다 (피치 변화 최소화 로직 적용 필요)
        
        # 간단한 방식: speedup 사용 (약간의 아티팩트 발생 가능)
        refined_audio = audio_segment.speedup(playback_speed=speed_factor)
        
        # speedup 후 미세한 오차가 있을 수 있으므로 잘라내거나 늘려서 정확히 맞춤
        if len(refined_audio) > target_duration_ms:
            refined_audio = refined_audio[:int(target_duration_ms)]
            
    # 2. 오디오가 타임코드보다 짧을 때 -> 뒤에 무음 추가 (Add Silence)
    else:
        silence_duration = target_duration_ms - current_duration_ms
        silence = AudioSegment.silent(duration=silence_duration)
        refined_audio = audio_segment + silence
        
    return refined_audio

# --- 2. Streamlit 웹 앱 UI 구성 ---

st.title("🎙️ AI Dubbing Sync Tool")
st.markdown("SRT 파일을 업로드하면 타임코드에 딱 맞는 더빙 오디오를 생성합니다.")

# 사이드바: 설정
with st.sidebar:
    st.header("설정 (Settings)")
    api_key = st.text_input("ElevenLabs API Key", type="password")
    voice_id = st.text_input("Voice ID", value="21m00Tcm4TlvDq8ikWAM") # 기본값: Rachel
    st.info("💡 Tip: 영어 원문을 20% 정도 짧게 압축해야 자연스럽습니다.")

# 메인: 파일 업로드
uploaded_file = st.file_uploader("SRT 파일을 업로드하세요", type=["srt"])

if uploaded_file and api_key:
    if st.button("오디오 생성 시작 (Generate Audio)"):
        srt_content = uploaded_file.getvalue().decode("utf-8")
        # ▼▼▼ [여기 아래에 이 코드를 추가해주세요] ▼▼▼
        srt_content = srt_content.replace("\r\n", "\n") 
        # ▲▲▲ 윈도우용 줄바꿈 문자를 맥/리눅스용으로 바꿔줍니다 ▲▲▲
        
        parsed_segments = parse_srt(srt_content)

        # ▼▼▼ [안전을 위해 이 코드도 추가하면 좋습니다] ▼▼▼
        if not parsed_segments:
            st.error("SRT 내용을 읽을 수 없습니다. 파일이 'UTF-8' 인코딩인지 확인해주세요.")
            st.stop()
        st.write(f"총 {len(parsed_segments)}개의 문장을 처리합니다...")
        
        # 진행률 바
        progress_bar = st.progress(0)
        
        # 전체 오디오 트랙 초기화 (마지막 타임코드까지 채우기 위함)
        total_duration = parsed_segments[-1]['end_ms']
        final_audio = AudioSegment.silent(duration=total_duration + 1000) # 여유 있게 생성
        
        # 개별 세그먼트 처리
        for i, seg in enumerate(parsed_segments):
            # 1. 오디오 생성
            audio_data = generate_audio(seg['text'], voice_id, api_key)
            
            if audio_data:
                # 2. 오디오 처리 (pydub)
                segment_audio = AudioSegment.from_file(io.BytesIO(audio_data), format="mp3")
                
                # 3. 싱크 맞추기 (Time Stretch)
                synced_audio = match_target_duration(segment_audio, seg['duration_ms'])
                
                # 4. 전체 트랙의 정확한 위치(Start Time)에 덮어쓰기(Overlay)
                # 주의: 단순히 이어붙이는 게 아니라, 타임코드의 '시작 위치'에 배치해야 함
                final_audio = final_audio.overlay(synced_audio, position=int(seg['start_ms']))
            
            # 진행률 업데이트
            progress_bar.progress((i + 1) / len(parsed_segments))
            
        st.success("완료되었습니다! 아래 버튼을 눌러 다운로드하세요.")
        
        # 다운로드 버튼 생성
        buffer = io.BytesIO()
        final_audio.export(buffer, format="mp3")
        st.audio(buffer, format="audio/mp3")
        st.download_button(
            label="더빙 오디오 다운로드 (.mp3)",
            data=buffer,
            file_name="dubbed_output.mp3",
            mime="audio/mp3"
        )

elif not api_key:
    st.warning("왼쪽 사이드바에 API Key를 입력해주세요.")

