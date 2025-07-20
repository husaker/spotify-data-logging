import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import re
import pandas as pd
import requests
import urllib.parse
import time
from datetime import datetime

st.title("Spotify Logging в Google Таблицу")

st.markdown("""
**Инструкция по авторизации Spotify:**
- Для логирования треков требуется доступ к вашему аккаунту Spotify через официальный API.
- Для этого нужно создать приложение в [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/applications), получить Client ID и Client Secret, а также добавить Redirect URI (например, `http://localhost:8501`).
- После этого вы сможете авторизоваться и разрешить приложению доступ к информации о прослушиваемых треках.
""")

# --- Session State Defaults ---
def init_state():
    for key, val in {
        'sheet_url': '',
        'client_id': '',
        'client_secret': '',
        'auth_code': '',
        'access_token': '',
        'refresh_token': '',
        'spotify_auth_success': False,
        'logging_active': False,
        'last_logged_track_id': None
    }.items():
        if key not in st.session_state:
            st.session_state[key] = val
init_state()

sheet_url = st.text_input("Ссылка на Google Таблицу:", value=st.session_state.sheet_url, key="sheet_url")

# --- Spotify OAuth ---
st.header("Авторизация Spotify")

# Получаем code из query params, если он есть
query_params = st.query_params
code_from_url = query_params.get("code", [None])[0] if "code" in query_params else None
if code_from_url and not st.session_state.spotify_auth_success:
    st.session_state.auth_code = code_from_url

def extract_sheet_id(url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    return match.group(1) if match else None

# Если уже авторизованы, показываем только статус
def show_spotify_status():
    st.success("✅ Авторизация Spotify прошла успешно! Теперь можно включать логирование треков.")
    st.markdown("\n**Вы авторизованы в Spotify.**")

if st.session_state.spotify_auth_success:
    show_spotify_status()
else:
    client_id = st.text_input("Spotify Client ID:", value=st.session_state.client_id, key="client_id")
    client_secret = st.text_input("Spotify Client Secret:", type="password", value=st.session_state.client_secret, key="client_secret")
    redirect_uri = "http://localhost:8501"
    scope = "user-read-currently-playing user-read-recently-played"

    if client_id and client_secret:
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": scope,
            "show_dialog": "true"
        }
        auth_url = f"https://accounts.spotify.com/authorize?{urllib.parse.urlencode(params)}"
        st.markdown(f"[Перейти для авторизации Spotify]({auth_url})")
        # Если code уже есть в URL, сразу предлагаем получить токен
        auth_code = st.text_input("Вставьте код из URL после авторизации (параметр ?code=...):", value=st.session_state.auth_code, key="auth_code")
        auto_get_token = code_from_url and not st.session_state.spotify_auth_success
        if st.button("Получить access token") or auto_get_token:
            if not auth_code:
                st.error("Пожалуйста, вставьте код авторизации из URL.")
            else:
                token_url = "https://accounts.spotify.com/api/token"
                data = {
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret
                }
                headers = {"Content-Type": "application/x-www-form-urlencoded"}
                response = requests.post(token_url, data=data, headers=headers)
                if response.status_code == 200:
                    token_info = response.json()
                    st.session_state.access_token = token_info.get("access_token")
                    st.session_state.refresh_token = token_info.get("refresh_token", st.session_state.refresh_token)
                    st.session_state.spotify_auth_success = True
                    show_spotify_status()
                else:
                    st.error(f"Ошибка получения токена: {response.text}")
                    st.session_state.spotify_auth_success = False

st.header("")

# --- Логирование треков ---
def log_track_to_sheet(track, worksheet):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    track_name = track['item']['name']
    artist = ', '.join([a['name'] for a in track['item']['artists']])
    spotify_id = track['item']['id']
    url = track['item']['external_urls']['spotify']
    worksheet.append_row([date_str, track_name, artist, spotify_id, url])
    st.session_state.last_logged_track_id = spotify_id

def refresh_access_token():
    token_url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": st.session_state.refresh_token,
        "client_id": st.session_state.client_id,
        "client_secret": st.session_state.client_secret
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(token_url, data=data, headers=headers)
    if response.status_code == 200:
        token_info = response.json()
        st.session_state.access_token = token_info.get("access_token")
        # refresh_token может не вернуться, используем старый
        st.session_state.refresh_token = token_info.get("refresh_token", st.session_state.refresh_token)
        return True
    else:
        st.error("Не удалось обновить access token. Пожалуйста, авторизуйтесь заново.")
        st.session_state.spotify_auth_success = False
        st.session_state.access_token = ''
        st.session_state.refresh_token = ''
        return False

def get_current_track(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers)
    if resp.status_code == 200 and resp.json().get('item'):
        return resp.json()
    elif resp.status_code == 401:
        # Токен истёк, пробуем обновить
        if refresh_access_token():
            headers = {"Authorization": f"Bearer {st.session_state.access_token}"}
            resp = requests.get("https://api.spotify.com/v1/me/player/currently-playing", headers=headers)
            if resp.status_code == 200 and resp.json().get('item'):
                return resp.json()
        return None
    return None

# Кнопка для запуска/остановки логирования
def logging_controls():
    if not st.session_state.logging_active:
        if st.button("Старт логирования"):
            st.session_state.logging_active = True
            st.rerun()
    else:
        if st.button("Остановить логирование"):
            st.session_state.logging_active = False
            st.success("Логирование остановлено.")
            st.rerun()

# Основная логика логирования
if st.session_state.spotify_auth_success and sheet_url:
    logging_controls()
    if st.session_state.logging_active:
        try:
            sheet_id = extract_sheet_id(sheet_url)
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(
                'google-credentials.json', scopes=scopes
            )
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.sheet1
            # Проверяем, есть ли заголовки
            all_rows = worksheet.get_all_values()
            if not all_rows or all_rows[0] != ["Date", "Track", "Artist", "Spotify ID", "URL"]:
                worksheet.insert_row(["Date", "Track", "Artist", "Spotify ID", "URL"], 1)
            # Получаем текущий трек
            track = get_current_track(st.session_state.access_token)
            if track:
                spotify_id = track['item']['id']
                if st.session_state.last_logged_track_id != spotify_id:
                    log_track_to_sheet(track, worksheet)
                    st.success(f"Добавлен трек: {track['item']['name']} — {', '.join([a['name'] for a in track['item']['artists']])}")
                else:
                    st.info("Текущий трек уже записан.")
            else:
                st.warning("Нет данных о текущем треке. Возможно, ничего не играет или истёк токен.")
            # Показываем последние 10 строк
            all_rows = worksheet.get_all_values()
            if len(all_rows) > 1:
                last_rows = all_rows[-10:]
                df = pd.DataFrame(last_rows[1:], columns=all_rows[0]) if len(all_rows) > 1 else pd.DataFrame()
                st.subheader("10 последних строк в таблице:")
                st.table(df)
            else:
                st.info("В таблице пока только заголовки.")
            # Автообновление каждые 10 секунд
            st.info("Следующая проверка через 10 секунд...")
            time.sleep(10)
            st.rerun()
        except Exception as e:
            st.error(f"Ошибка при логировании: {e}")
else:
    if st.button("Включить логирование"):
        if not sheet_url:
            st.error("Пожалуйста, введите ссылку на Google Таблицу.") 