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
    srt_content = srt_content.replace("\r\n", "\n") # 윈도우 줄바꿈 호환
    
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
        "model_id": "eleven_multilingual_v2",
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

# [수정됨] 무음 제거 함수 추가 (오디오 앞뒤의 조용한 부분을 잘라냄)
def remove_silence(audio_segment, silence_thresh=-50.0):
    start_trim = 0
    end_trim = len(audio_segment)
    
    # 앞부분 무음 찾기
    for i in range(0, len(audio_segment), 10):
        if audio_segment[i:i+10].dBFS > silence_thresh:
            start_trim = i
            break
            
    # 뒷부분 무음 찾기
    for i in range(len(audio_segment)-10, 0, -10):
        if audio_segment[i:i+10].dBFS > silence_thresh:
            end_trim = i + 10
            break
            
    return audio_segment[start_trim:end_trim]

# [수정됨] 타임코드 매칭 함수 (안전장치 강화)
def match_target_duration(audio_segment, target_duration_ms):
    
    # 1. 먼저 앞뒤 무음을 제거해서 순수 오디오만 남김 (압축 부담 줄이기)
    if len(audio_segment) > 0:
        audio_segment = remove_silence(audio_segment)

    current_duration_ms = len(audio_segment)
    
    if current_duration_ms == 0:
        return AudioSegment.silent(duration=target_duration_ms)

    # 오디오가 타임코드보다 길 때 -> 속도를 높임 (Speed Up)
    if current_duration_ms > target_duration_ms:
        speed_factor = current_duration_ms / target_duration_ms
        
        # [안전장치] 속도 변환 시도 (실패 시 원본 사용)
        try:
            # 1.5배 이상 빨라져야 하면 음질이 깨질 수 있으므로 로그를 남기거나 주의 필요
            # pydub speedup은 1.4배 넘어가면 불안정할 수 있음
            refined_audio = audio_segment.speedup(playback_speed=speed_factor)
        except Exception as e:
            # 변환 실패시 그냥 원본을 씀 (묵음 방지)
            refined_audio = audio_segment

        # 변환 후에도 길거나, 변환이 제대로 안 됐을 경우 강제로 자름
        if len(refined_audio) > target_duration_ms:
            refined_audio = refined_audio[:int(target_duration_ms)]
            
    # 오디오가 타임코드보다 짧을 때 -> 뒤에 무음 추가 (Add Silence)
    else:
        silence_duration = target_duration_ms - current_duration_ms
        silence = AudioSegment.silent(duration=silence_duration)
        refined_audio = audio_segment + silence
        
    return refined_audio

# --- 2. Streamlit 웹 앱 UI 구성 ---

st.set_page_config(page_title="다국어 더빙용 일레븐랩스", page_icon="🎙️")
st.title("🎙️ 다국어 더빙용 일레븐랩스")

# 상단 안내 문구들
st.warning("여러 개의 SRT 파일을 업로드하면 순차적으로 더빙 오디오를 생성합니다. (한번에 2개 권장)")
st.warning("⚠ 더빙 생성을 신중하게 결정하세요. (버튼을 누르면 즉시 비용이 차감됩니다.)")

with st.sidebar:
    st.header("설정 (Settings)")
    
    voice_id = st.text_input("더빙 캐릭터의 Voice ID 입력", value="21m00Tcm4TlvDq8ikWAM")
    st.error("⚠ 목소리 캐릭터를 신중하게 입력하세요. (잘못된 ID를 입력해도 비용이 발생할 수 있습니다.)")
    
    st.info("💡 Tip: 영어 원문을 20% 정도 짧게 압축해야 자연스럽습니다.")

    st.divider() 
    if "ELEVENLABS_API_KEY" in st.secrets:
        api_key = st.secrets["ELEVENLABS_API_KEY"]
        st.success("✅ API Key가 안전하게 로드되었습니다.")
    else:
        api_key = st.text_input("ElevenLabs API Key", type="password")
        st.warning("Secrets에 키를 등록하면 매번 입력하지 않아도 됩니다.")

# 경고 박스로 안내
st.warning("SRT 파일을 업로드하세요. 반드시 '완료' 문구가 뜰 때까지 기다리세요.")

uploaded_files = st.file_uploader("아래 영역에 파일을 드래그하거나 클릭하세요", type=["srt"], accept_multiple_files=True)

# 세션 스테이트 초기화 (결과 저장소)
if 'generated_results' not in st.session_state:
    st.session_state.generated_results = []

if uploaded_files and api_key:
    if st.button(f"총 {len(uploaded_files)}개 파일 변환 시작 (Start Batch Process)"):
        
        st.session_state.generated_results = []
        
        main_progress = st.progress(0)
        status_text = st.empty()

        for file_idx, uploaded_file in enumerate(uploaded_files):
            file_name = uploaded_file.name
            status_text.markdown(f"### 🔄 처리 중: **{file_name}** ({file_idx + 1}/{len(uploaded_files)})")
            
            srt_content = uploaded_file.getvalue().decode("utf-8")
            parsed_segments = parse_srt(srt_content)
            
            if not parsed_segments:
                st.error(f"⚠️ {file_name}: 내용을 읽을 수 없습니다.")
                continue

            total_duration = parsed_segments[-1]['end_ms']
            final_audio = AudioSegment.silent(duration=total_duration + 1000)
            
            sub_progress = st.progress(0)
            
            for i, seg in enumerate(parsed_segments):
                audio_data = generate_audio(seg['text'], voice_id, api_key)
                
                if audio_data:
                    segment_audio = AudioSegment.from_file(io.BytesIO(audio_data), format="mp3")
                    # 싱크 맞추기 (수정된 함수 사용)
                    synced_audio = match_target_duration(segment_audio, seg['duration_ms'])
                    final_audio = final_audio.overlay(synced_audio, position=int(seg['start_ms']))
                
                sub_progress.progress((i + 1) / len(parsed_segments))
            
            # 결과 저장
            output_filename = file_name.replace(".srt", "_dubbed.mp3")
            buffer = io.BytesIO()
            final_audio.export(buffer, format="mp3")
            
            st.session_state.generated_results.append({
                "filename": output_filename,
                "data": buffer.getvalue()
            })
            
            st.divider()
            main_progress.progress((file_idx + 1) / len(uploaded_files))

        status_text.success("🎉 모든 파일 처리가 완료되었습니다! 아래에서 다운로드하세요.")

# 저장된 결과 표시
if st.session_state.generated_results:
    st.markdown("### 📥 완료된 파일 다운로드")
    for result in st.session_state.generated_results:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.audio(result["data"], format="audio/mp3")
        with col2:
            st.download_button(
                label=f"📥 {result['filename']} 다운로드",
                data=result["data"],
                file_name=result["filename"],
                mime="audio/mp3"
            )
        st.divider()

elif not api_key:
    st.warning("왼쪽 사이드바에 API Key를 입력하거나 Secrets에 등록해주세요.")
