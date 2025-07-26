import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import re
import pandas as pd
import requests
import urllib.parse
import json
import os
from streamlit_autorefresh import st_autorefresh

st.title("Spotify Logging в Google Таблицу")

st.markdown("""
**Инструкция по авторизации и настройке:**
- Для логирования треков требуется доступ к вашему аккаунту Spotify через официальный API.
- Для работы с Google Таблицей:
    - Таблица должна содержать следующие заголовки в первой строке: `Date`, `Track`, `Artist`, `Spotify ID`, `URL`, `Context Type`.
    - В настройках доступа Google Таблицы добавьте в редакторы email сервисного аккаунта (`spotify-data-reader@spotify-listening-vix-prohect.iam.gserviceaccount.com`).
- После этого вы сможете авторизоваться и разрешить приложению доступ к информации о прослушиваемых треках, а также логировать их в Google Таблицу.
""")

st.markdown(
    """
    <style>
    .stButton button { margin-bottom: 0 !important; margin-top: 0 !important; }
    .element-container { margin-bottom: 0 !important; margin-top: 0 !important; }
    .stContainer { padding-top: 0 !important; padding-bottom: 0 !important; }
    </style>
    """,
    unsafe_allow_html=True
)

# --- Session State Defaults ---
def init_state():
    for key, val in {
        'sheet_url': '',
        'auth_code': '',
        'access_token': '',
        'refresh_token': '',
        'spotify_auth_success': False,
        'logging_active': False,
        'last_logged_track_id': None,
        'last_logged_played_at': None
    }.items():
        if key not in st.session_state:
            st.session_state[key] = val
init_state()

sheet_url = st.text_input("Ссылка на Google Таблицу:", value=st.session_state.sheet_url, key="sheet_url")

# --- Spotify OAuth ---
st.header("Авторизация Spotify")

# Универсальная загрузка client_id и client_secret
SPOTIFY_CREDS_PATH = "spotify-credentials.json"
if os.path.exists(SPOTIFY_CREDS_PATH):
    with open(SPOTIFY_CREDS_PATH, "r", encoding="utf-8") as f:
        spotify_creds = json.load(f)
else:
    creds_json = None
    try:
        import streamlit as st
        creds_json = st.secrets["SPOTIFY_CREDENTIALS_JSON"]
    except Exception:
        creds_json = os.environ.get("SPOTIFY_CREDENTIALS_JSON", None)
    if creds_json:
        spotify_creds = json.loads(creds_json)
    else:
        st.error("Не найден spotify-credentials.json и не задан SPOTIFY_CREDENTIALS_JSON в секрете или переменной окружения!")
        st.stop()
client_id = spotify_creds.get("client_id")
client_secret = spotify_creds.get("client_secret")
if not client_id or not client_secret or "ВАШ_CLIENT_ID" in client_id:
    st.error("Пожалуйста, заполните spotify-credentials.json или секрета своими данными.")
    st.stop()

# Получаем code из query params, если он есть
query_params = st.query_params

code_from_url = None
if "code" in query_params:
    code_val = query_params["code"]
    if isinstance(code_val, list):
        code_from_url = code_val[0]
    else:
        code_from_url = code_val

# (чтобы не было жёлтой плашки)
if code_from_url and not st.session_state.spotify_auth_success:
    if st.session_state.auth_code != code_from_url:
        st.session_state.auth_code = code_from_url

def extract_sheet_id(url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    return match.group(1) if match else None

# Если уже авторизован, показываем только статус
def show_spotify_status():
    st.success("✅ Авторизация Spotify прошла успешно! Теперь можно включать логирование треков.")
    st.markdown("\n**Вы авторизованы в Spotify.**")

if st.session_state.spotify_auth_success:
    show_spotify_status()
else:
    # Универсальный redirect_uri
    redirect_uri = st.secrets.get("SPOTIFY_REDIRECT_URI", "http://localhost:8501")
    scope = "user-read-currently-playing user-read-recently-played"
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "show_dialog": "true"
    }
    auth_url = f"https://accounts.spotify.com/authorize?{urllib.parse.urlencode(params)}"
    st.markdown(f'<a href="{auth_url}" target="_self">Перейти для авторизации Spotify</a>', unsafe_allow_html=True)
    # токен получается только автоматически
    auto_get_token = code_from_url and not st.session_state.spotify_auth_success
    if auto_get_token:
        auth_code = st.session_state.auth_code
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

# --- Логирование треков ---
def log_track_to_sheet(track, worksheet):
    played_at = track['played_at']
    # played_at приходит в формате ISO, можно преобразовать к локальному времени, если нужно
    date_str = played_at  # теперь Date = played_at
    track_name = track['track']['name']
    artist = ', '.join([a['name'] for a in track['track']['artists']])
    spotify_id = track['track']['id']
    url = track['track']['external_urls']['spotify']
    context_type = track.get('context', {}).get('type') if track.get('context') else None
    worksheet.append_row([date_str, track_name, artist, spotify_id, url, context_type])
    st.session_state.last_logged_played_at = played_at
    st.session_state.last_logged_track_id = spotify_id

def refresh_access_token():
    token_url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": st.session_state.refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(token_url, data=data, headers=headers)
    if response.status_code == 200:
        token_info = response.json()
        st.session_state.access_token = token_info.get("access_token")
        st.session_state.refresh_token = token_info.get("refresh_token", st.session_state.refresh_token)
        return True
    else:
        st.error("Не удалось обновить access token. Пожалуйста, авторизуйтесь заново.")
        st.session_state.spotify_auth_success = False
        st.session_state.access_token = ''
        st.session_state.refresh_token = ''
        return False

def get_recent_tracks(token, after=None):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 5}  # теперь по 5 треков
    if after:
        params["after"] = after
    try:
        resp = requests.get("https://api.spotify.com/v1/me/player/recently-played", headers=headers, params=params, timeout=10)
    except requests.exceptions.Timeout:
        st.error("Ошибка: запрос к Spotify API превысил таймаут (10 секунд). Проверьте соединение.")
        return []
    except Exception as e:
        st.error(f"Ошибка при обращении к Spotify API: {e}")
        return []
    if resp.status_code == 200:
        return resp.json().get('items', [])
    elif resp.status_code == 401:
        if refresh_access_token():
            headers = {"Authorization": f"Bearer {st.session_state.access_token}"}
            try:
                resp = requests.get("https://api.spotify.com/v1/me/player/recently-played", headers=headers, params=params, timeout=10)
            except requests.exceptions.Timeout:
                st.error("Ошибка: запрос к Spotify API превысил таймаут (10 секунд). Проверьте соединение.")
                return []
            except Exception as e:
                st.error(f"Ошибка при обращении к Spotify API: {e}")
                return []
            if resp.status_code == 200:
                return resp.json().get('items', [])
        return []
    else:
        st.error(f"Ошибка Spotify API: {resp.status_code} — {resp.text}")
        return []

# --- Блок результатов ---
if st.session_state.spotify_auth_success and sheet_url:
    with st.container():
        col1, col2 = st.columns([2, 5])
        with col1:
            if st.session_state.logging_active:
                if st.button("Остановить логирование", use_container_width=True):
                    st.session_state.logging_active = False
                    st.session_state.status_message = "Логирование остановлено."
            else:
                if st.button("Старт логирования", use_container_width=True):
                    st.session_state.logging_active = True
                    st.session_state.status_message = "Логирование запущено."
        with col2:
            if "status_message" in st.session_state:
                st.info(st.session_state.status_message)

# --- Блок результатов ---
if st.session_state.logging_active and sheet_url:
    with st.spinner("Обновление данных..."):
        try:
            st_autorefresh(interval=120000, key="autorefresh")
            sheet_id = extract_sheet_id(sheet_url)
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            google_creds_path = 'google-credentials.json'
            if not os.path.exists(google_creds_path):
                # Пробуем взять из секрета Streamlit (или переменной окружения)
                import tempfile
                import json
                creds_json = None
                try:
                    import streamlit as st
                    creds_json = st.secrets["GOOGLE_CREDENTIALS_JSON"]
                except Exception:
                    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", None)
                if creds_json:
                    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".json") as tmp_gfile:
                        tmp_gfile.write(creds_json)
                        google_creds_path = tmp_gfile.name
                else:
                    st.error("Не найден google-credentials.json и не задан GOOGLE_CREDENTIALS_JSON в секрете или переменной окружения!")
                    st.stop()
            creds = Credentials.from_service_account_file(
                google_creds_path, scopes=scopes
            )
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(sheet_id)
            worksheet = sh.sheet1
            all_rows = worksheet.get_all_values()
            expected_header = ["Date", "Track", "Artist", "Spotify ID", "URL", "Context Type"]
            if not all_rows or all_rows[0] != expected_header:
                worksheet.insert_row(expected_header, 1)
                all_rows = worksheet.get_all_values()
            logged_pairs = set()
            for row in all_rows[1:]:
                # Сравниваем только id и дату, независимо от длины строки
                if len(row) >= 4:
                    logged_pairs.add((row[3], row[0]))
            try:
                recent_tracks = get_recent_tracks(st.session_state.access_token)
            except Exception as e:
                st.session_state.status_message = f"Ошибка при получении треков: {e}"
                recent_tracks = []
            new_logged = 0
            for track in reversed(recent_tracks):
                spotify_id = track['track']['id']
                played_at = track['played_at']
                if (spotify_id, played_at) not in logged_pairs:
                    log_track_to_sheet(track, worksheet)
                    new_logged += 1
            if new_logged:
                st.session_state.status_message = f"Добавлено новых треков: {new_logged}"
            else:
                st.session_state.status_message = "Нет новых прослушанных треков для логирования."
            all_rows = worksheet.get_all_values()
            if len(all_rows) > 1:
                last_rows = all_rows[-6:]
                # Дополняем строки до нужного количества столбцов
                n_cols = len(all_rows[0])
                last_rows_fixed = [row + [None]*(n_cols-len(row)) if len(row)<n_cols else row for row in last_rows]
                # Инвертируем только данные, заголовок оставляем сверху
                if len(last_rows_fixed) > 1:
                    data_rows = last_rows_fixed[1:][::-1]
                    df = pd.DataFrame(data_rows, columns=all_rows[0])
                else:
                    df = pd.DataFrame()
                st.subheader("5 последних строк в таблице:")
                st.table(df)
            else:
                st.info("В таблице пока только заголовки.")
        except Exception as e:
            st.session_state.status_message = f"Ошибка при логировании: {e}" 