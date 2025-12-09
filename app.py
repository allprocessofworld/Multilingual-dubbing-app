import streamlit as st
import re
from datetime import datetime, timedelta
import requests
import io
from pydub import AudioSegment

# --- 1. 기본 설정 및 함수 정의 ---

def parse_srt_time(time_str):
    """SRT 시간 문자열(00:00:00,000)을 밀리초(ms)로 변환"""
    time_str = time_str.replace(',', '.')
    t = datetime.strptime(time_str, "%H:%M:%S.%f")
    delta = timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond)
    return delta.total_seconds() * 1000

def parse_srt(srt_content):
    """SRT 내용을 파싱하여 (시작시간, 종료시간, 텍스트) 리스트로 반환"""
    # 윈도우 줄바꿈(\r\n)을 리눅스용(\n)으로 통일 (에러 방지 핵심)
    srt_content = srt_content.replace("\r\n", "\n")
    
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
        "model_id": "eleven_multilingual_v2", # 자동 언어 감지 모델
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
        refined_audio = audio_segment.speedup(playback_speed=speed_factor)
        if len(refined_audio) > target_duration_ms:
            refined_audio = refined_audio[:int(target_duration_ms)]
            
    # 2. 오디오가 타임코드보다 짧을 때 -> 뒤에 무음 추가 (Add Silence)
    else:
        silence_duration = target_duration_ms - current_duration_ms
        silence = AudioSegment.silent(duration=silence_duration)
        refined_audio = audio_segment + silence
        
    return refined_audio

# --- 2. Streamlit 웹 앱 UI 구성 ---

# [요청 2] 제목 변경
st.set_page_config(page_title="다국어 더빙용 일레븐랩스", page_icon="🎙️")
st.title("🎙️ 다국어 더빙용 일레븐랩스")
st.markdown("여러 개의 SRT 파일을 업로드하면 순차적으로 더빙 오디오를 생성합니다.")

# 사이드바: 설정
with st.sidebar:
    st.header("설정 (Settings)")
    
    # [요청 1] API Key 자동 로드 로직
    if "ELEVENLABS_API_KEY" in st.secrets:
        api_key = st.secrets["ELEVENLABS_API_KEY"]
        st.success("✅ API Key가 안전하게 로드되었습니다.")
    else:
        api_key = st.text_input("ElevenLabs API Key", type="password")
        st.warning("Secrets에 키를 등록하면 매번 입력하지 않아도 됩니다.")

    voice_id = st.text_input("Voice ID", value="21m00Tcm4TlvDq8ikWAM") # 기본값: Rachel
    st.info("💡 Tip: 영어 원문을 20% 정도 짧게 압축해야 자연스럽습니다.")

# [요청 3] 다중 파일 업로드 (accept_multiple_files=True)
uploaded_files = st.file_uploader("SRT 파일을 업로드하세요 (여러 개 가능)", type=["srt"], accept_multiple_files=True)

if uploaded_files and api_key:
    if st.button(f"총 {len(uploaded_files)}개 파일 변환 시작 (Start Batch Process)"):
        
        # 전체 진행바 (파일 단위)
        main_progress = st.progress(0)
        status_text = st.empty()

        for file_idx, uploaded_file in enumerate(uploaded_files):
            file_name = uploaded_file.name
            status_text.markdown(f"### 🔄 처리 중: **{file_name}** ({file_idx + 1}/{len(uploaded_files)})")
            
            # SRT 파싱
            srt_content = uploaded_file.getvalue().decode("utf-8")
            parsed_segments = parse_srt(srt_content)
            
            if not parsed_segments:
                st.error(f"⚠️ {file_name}: 내용을 읽을 수 없습니다. 건너뜁니다.")
                continue

            # 파일별 오디오 트랙 생성
            total_duration = parsed_segments[-1]['end_ms']
            final_audio = AudioSegment.silent(duration=total_duration + 1000)
            
            # 문장별 처리 진행바
            sub_progress = st.progress(0)
            
            for i, seg in enumerate(parsed_segments):
                audio_data = generate_audio(seg['text'], voice_id, api_key)
                
                if audio_data:
                    segment_audio = AudioSegment.from_file(io.BytesIO(audio_data), format="mp3")
                    synced_audio = match_target_duration(segment_audio, seg['duration_ms'])
                    final_audio = final_audio.overlay(synced_audio, position=int(seg['start_ms']))
                
                sub_progress.progress((i + 1) / len(parsed_segments))
            
            # 파일별 결과 출력
            st.success(f"✅ 완료: {file_name}")
            
            # 다운로드 버튼 생성 (파일명_dubbed.mp3)
            output_filename = file_name.replace(".srt", "_dubbed.mp3")
            buffer = io.BytesIO()
            final_audio.export(buffer, format="mp3")
            
            col1, col2 = st.columns([1, 2])
            with col1:
                st.audio(buffer, format="audio/mp3")
            with col2:
                st.download_button(
                    label=f"📥 {output_filename} 다운로드",
                    data=buffer,
                    file_name=output_filename,
                    mime="audio/mp3",
                    key=f"btn_{file_idx}" # 버튼 ID 중복 방지
                )
            
            st.divider() # 구분선
            
            # 전체 진행률 업데이트
            main_progress.progress((file_idx + 1) / len(uploaded_files))

        status_text.success("🎉 모든 파일 처리가 완료되었습니다!")

elif not api_key:
    st.warning("왼쪽 사이드바에 API Key를 입력하거나 Secrets에 등록해주세요.")
