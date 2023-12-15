import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from pytube import YouTube, Playlist,Channel
from pytube.exceptions import RegexMatchError

import re, os, traceback

from download import YouTubeDownloader
app = Flask(__name__)
app.static_folder = 'static'

############################################## yt_tracker ##############################################
def yt_tracker_init_db():
    with sqlite3.connect('yt_tracker.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT NOT NULL PRIMARY KEY,
                name TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT NOT NULL PRIMARY KEY,
                title TEXT NOT NULL,
                track_flag INTEGER,
                channel_id TEXT,
                FOREIGN KEY (channel_id) REFERENCES channels (id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS syncfolders (
                folder_path TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                playlist_id TEXT NOT NULL,
                sync_date DATETIME,
                PRIMARY KEY (folder_path, channel_id, playlist_id),
                FOREIGN KEY (channel_id) REFERENCES channels (id),
                FOREIGN KEY (playlist_id) REFERENCES playlists (id)
            );
        ''')
        conn.commit()

def get_channel_info(url):
    try:
        # 非youtube網址
        if not url.startswith("https://www.youtube.com/"):
            return None, None, []
        # 預設為影片網址
        channel_id = DOWNLOADER.get_video_ID(url)
        if channel_id is None:    
            # 判斷是否為playlist網址
            channel_id = DOWNLOADER.get_playlist_ID(url)
            if channel_id is not None:
                channel_id = Playlist(url).owner_id
        else:
            # 是影片網址
            channel_id = YouTube(url).channel_id
        # 上方判斷完成的，直接獲取channel_name
        if channel_id is not None:
            url = "https://www.youtube.com/channel/" + channel_id
            channel_name = Channel(url).channel_name
            url += '/playlists'
        else:
            # 網址中間有@為新架構的youtube's id 將url設置到playlists位置'/playlists'來爬蟲
            if '@' in url:
                url = url.split('@')[0] + '@' + url.split('@')[1].split('/')[0] + '/playlists'
        print(url)
        # 從playlists抓取播放清單
        chrome_path = "./chromedriver.exe"
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-extensions')
        service = ChromeService(chrome_path)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        # 打開目標URL
        driver.get(url)
        # 使用顯性等待等待直到 channel_id_element 出現在頁面上
        channel_id_element_locator = (By.CSS_SELECTOR, '.style-scope.ytd-c4-tabbed-header-renderer')
        wait = WebDriverWait(driver, 10)
        channel_id_element = wait.until(EC.presence_of_element_located(channel_id_element_locator))

        # 獲取頁面源碼
        page_source = driver.page_source

        soup = BeautifulSoup(page_source, 'html.parser')

        # 找到 channel_name 的 <yt-formatted-string>
        channel_name_element = soup.find('yt-formatted-string', {'class': 'style-scope ytd-channel-name'})
        channel_name = channel_name_element.get_text(strip=True) if channel_name_element else None

        # 找到 channel_id 的 <yt-formatted-string>
        channel_id_element = soup.find('yt-formatted-string', {'class': 'style-scope ytd-c4-tabbed-header-renderer'})
        channel_id = channel_id_element.get_text(strip=True) if channel_id_element else None

        # 找到所有的超連結
        links = soup.find_all('a', href=True)

        # 獲取播放清單的 id
        playlist_id_list = [DOWNLOADER.get_playlist_ID(link['href']) for link in links if DOWNLOADER.get_playlist_ID(link['href']) is not None]

        # 關閉瀏覽器
        driver.quit()

        return channel_id, channel_name, playlist_id_list
    except RegexMatchError:
        return None, None, []

def get_data_from_db():
    with sqlite3.connect('yt_tracker.db') as conn:
        cursor = conn.cursor()

        # 獲取所有 channels
        cursor.execute('SELECT * FROM channels')
        channels = []
        for channel_row in cursor.fetchall():
            channel = {'id':channel_row[0],'name': channel_row[1], 'playlists': []}

            # 獲取該 channel 的所有 playlists
            cursor.execute('SELECT * FROM playlists WHERE channel_id = ?', (channel_row[0],))
            for playlist_row in cursor.fetchall():
                playlists = {'id': playlist_row[0],'title': playlist_row[1], 'track_flag': playlist_row[2], 'channel_id': playlist_row[3]}
                channel['playlists'].append(playlists)
            channels.append(channel)
    return channels
def yt_tracker_sync(folder_path, audio_only):
    # 檢查下載目錄是否存在
    error_message = ''
    normal_message = ''
    with sqlite3.connect('yt_tracker.db') as conn:
        synced_list = [] # 已同步error_message's list
        cursor = conn.cursor()
        
        # 獲取所有需要同步的播放清單
        cursor.execute('SELECT * FROM playlists WHERE track_flag = 1;')
        playlists_to_download = cursor.fetchall()
            
        for playlist_info in playlists_to_download:
            playlist_id, title, track_flag, channel_id = playlist_info
            # 取得channel_name
            cursor.execute('SELECT name FROM channels WHERE id = ?;', (channel_id,))
            channel_name = cursor.fetchone()[0]
            
            # 去除非法字符
            channel_name = re.sub(r'[\\/:"*?<>|]', '', channel_name)
            title = re.sub(r'[\\/:"*?<>|]', '', title)
            # 取得下載目錄的路徑
            channel_folder_path = os.path.join(folder_path, channel_name) 
            playlist_folder_path = os.path.join(channel_folder_path, title) 
            # 建立 channel、playlist 資料夾
            os.makedirs(channel_folder_path, exist_ok=True)
            os.makedirs(playlist_folder_path, exist_ok=True)
            
            cursor.execute('SELECT sync_date FROM syncfolders WHERE folder_path = ? AND channel_id = ? AND playlist_id = ?;', (folder_path, channel_id, playlist_id))
            sync_date = cursor.fetchone()
            if sync_date is None:
                # 資料庫中不存在該 folder_path，將它加入資料庫
                sync_date = None
                cursor.execute('INSERT INTO syncfolders (folder_path, channel_id, playlist_id, sync_date) VALUES (?, ?, ?, ?);', (folder_path, channel_id, playlist_id, datetime.now()))
                conn.commit()                    
            else:
                sync_date = datetime.strptime(sync_date[0], "%Y-%m-%d %H:%M:%S.%f")
            pytube_playlist = Playlist("https://www.youtube.com/playlist?list=" + playlist_id)
            for url in pytube_playlist.video_urls:
                # 下載sync_date日期前的影片
                if sync_date is None or (sync_date <= YouTube(url).publish_date):
                    DOWNLOADER.download([url], playlist_folder_path, audio_only) 
            synced_list.append(os.path.join(channel_folder_path, playlist_folder_path) )
            
        normal_message = "已同步: "+",".join(map(str, synced_list))
    return error_message, normal_message
# 追蹤全部
@app.route('/yt_tracker_track_all', methods=['POST'])
def yt_tracker_track_all():
    if request.method == 'POST' and 'channelId' in request.form:
        channel_id = request.form['channelId']

        # 在這裡加入處理邏輯，例如遍歷該頻道的所有播放清單，然後進行相應的處理
        with sqlite3.connect('yt_tracker.db') as conn:
            cursor = conn.cursor()
            
            # 取得該頻道的所有播放清單
            cursor.execute('SELECT id FROM playlists WHERE channel_id = ?', (channel_id,))
            playlist_ids = [row[0] for row in cursor.fetchall()]

            # 對每個播放清單進行處理，例如將其標記為已追蹤
            for playlist_id in playlist_ids:
                cursor.execute('UPDATE playlists SET track_flag = 1 WHERE id = ?', (playlist_id,))
                conn.commit()

    # 重定向到主頁面
    return redirect(url_for('yt_tracker_tracker_manager'))

# 取消追蹤全部
@app.route('/yt_tracker_untrack_all', methods=['POST'])
def yt_tracker_untrack_all():
    if request.method == 'POST' and 'channelId' in request.form:
        channel_id = request.form['channelId']

        # 在這裡加入處理邏輯，例如遍歷該頻道的所有播放清單，然後進行相應的處理
        with sqlite3.connect('yt_tracker.db') as conn:
            cursor = conn.cursor()
            
            # 取得該頻道的所有播放清單
            cursor.execute('SELECT id FROM playlists WHERE channel_id = ?', (channel_id,))
            playlist_ids = [row[0] for row in cursor.fetchall()]

            # 對每個播放清單進行處理，例如將其標記為未追蹤
            for playlist_id in playlist_ids:
                cursor.execute('UPDATE playlists SET track_flag = 0 WHERE id = ?', (playlist_id,))
                conn.commit()

    # 重定向到主頁面
    return redirect(url_for('yt_tracker_tracker_manager'))
# 追蹤反轉
@app.route('/yt_tracker_toggle_track_flag', methods=['POST'])
def yt_tracker_toggle_track_flag():
    if request.method == 'POST' and 'playlistId' in request.form:
        playlist_id = request.form['playlistId']

        # 确保 current_flag 是整数
        current_flag = int(request.form['currentFlag'])

        with sqlite3.connect('yt_tracker.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT track_flag FROM playlists WHERE id = ?', (playlist_id,))
            existing_track_flag = cursor.fetchone()[0]

            if existing_track_flag is not None:
                new_track_flag = 1 if current_flag == 0 else 0
                cursor.execute('UPDATE playlists SET track_flag = ? WHERE id = ?', (new_track_flag, playlist_id))
                conn.commit()

    # 重定向到主页
    return redirect(url_for('yt_tracker_tracker_manager'))
@app.route('/yt_tracker_tracker_manager', methods=['GET', 'POST'])
def yt_tracker_tracker_manager():
    error_message = ''
    normal_message = ''
    if request.method == 'POST' and 'url' in request.form:
        url = request.form['url']
        if url == None: 
            error_message = "Invalid URL"
        else:
            channel_id, channel_name, playlist_id_list = get_channel_info(url)
            if channel_id == None: 
                error_message = "Invalid YouTube Channel URL"
            else:
                normal_message = str(channel_name) + " 追蹤清單已加入:"
                with sqlite3.connect('yt_tracker.db') as conn:
                    cursor = conn.cursor()

                    # 查詢 channels 表中是否已經存在相同的 channel_id
                    cursor.execute('SELECT id FROM channels WHERE id = ?', (channel_id,))
                    existing_channel = cursor.fetchone()
                    # 如果不存在，則插入新資料
                    if not existing_channel:
                        cursor.execute('INSERT INTO channels (id, name) VALUES (?, ?)', (channel_id, channel_name))
                        conn.commit()

                    for list_id in playlist_id_list:
                        # 查詢 playlists 表中是否已經存在相同的 playlists_id
                        cursor.execute('SELECT id FROM playlists WHERE id = ?', (list_id,))
                        existing_playlist = cursor.fetchone()

                        # 如果不存在，則插入新資料
                        if not existing_playlist:
                            pytube_playlist = Playlist("https://www.youtube.com/playlist?list=" + list_id)
                            cursor.execute('INSERT INTO playlists (id, title, track_flag, channel_id) VALUES (?, ?, ?, ?)',
                                            (list_id, pytube_playlist.title, 0, channel_id))
                            conn.commit()
                            normal_message += str(pytube_playlist.title) + ','
    channels = get_data_from_db()
    return render_template('/yt_tracker/tracker_manager.html', error_message=error_message, normal_message=normal_message, channels=channels)

# 同步管理頁面
@app.route('/yt_tracker_sync_manager', methods=['GET', 'POST'])
def yt_tracker_sync_manager():
    error_message = ''
    normal_message = ''
    if request.method == 'POST' and 'folder_path' in request.form:
        # 獲取用戶選擇的下載目錄
        try:
            folder_path = request.form.get('folder_path')
            audio_only = request.form.get('audio_only') == 'on'
            
        except Exception as e:
            traceback.print_exc()  # 將異常的詳細資訊輸出到控制台
            error_message = str(e)
            
        if not os.path.exists(folder_path):
            error_message = f"Download folder '{folder_path}' does not exist."
        else:
            error_message, normal_message = yt_tracker_sync(folder_path, audio_only)    

    channels = get_data_from_db()
    return render_template('/yt_tracker/sync_manager.html', error_message=error_message, normal_message=normal_message, channels=channels)

@app.route('/yt_tracker_delete_channel', methods=['GET', 'POST'])
def yt_tracker_delete_channel():
    if request.method == 'POST' and 'channelId' in request.form:
        channel_id = request.form['channelId']

        with sqlite3.connect('yt_tracker.db') as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM playlists WHERE channel_id = ?', (channel_id,))
            cursor.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
            conn.commit()

    return redirect(url_for('yt_tracker_tracker_manager'))

# 下載管理頁面
@app.route('/yt_tracker_download_manager', methods=['GET', 'POST'])
def yt_tracker_download_manager():
    error_message = ''
    normal_message = ''
    if request.method == 'POST' and 'folder_path' in request.form and 'url' in request.form:
        # 獲取用戶選擇的下載目錄
        try:
            folder_path = request.form.get('folder_path')
            audio_only = request.form.get('audio_only') == 'on'
            download_playlist = request.form.get('download_playlist') == 'on'
            url = request.form['url']
            if not os.path.exists(folder_path):
                error_message += f"Download folder '{folder_path}' does not exist."
            else:            
                video_id, playlist_id = DOWNLOADER.get_id_from_url(url)
                if download_playlist:
                    # 下載清單
                    if playlist_id is not None:
                        DOWNLOADER.download_from_playlist_id(playlist_id, folder_path, audio_only)
                        normal_message = "Download playlist '{playlist_id}' OK!"
                    else:
                        error_message += f"Is Not a Playlist. URL: '{url}' "
                else:
                    # 下載單一視頻or音頻
                    if video_id is not None: 
                        DOWNLOADER.download([url], folder_path, audio_only)
                        normal_message = f"Download '{url}' OK!"
                    else:  
                        error_message = "Invalid URL."
        except Exception as e:
            traceback.print_exc()  # 將異常的詳細資訊輸出到控制台
            error_message = str(e)           
    channels = get_data_from_db()
    return render_template('/yt_tracker/download_manager.html', error_message=error_message, normal_message=normal_message, channels=channels)

@app.route('/yt_tracker_index', methods=['GET', 'POST'])
def yt_tracker_index():
    error_message = ''
    normal_message = ''
    return render_template('/yt_tracker/index.html', error_message=error_message, normal_message=normal_message)

############################################## home ##############################################
def init_db():
    with sqlite3.connect('yt_tracker.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id TEXT NOT NULL PRIMARY KEY,
                name TEXT NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT NOT NULL PRIMARY KEY,
                title TEXT NOT NULL,
                track_flag INTEGER,
                channel_id TEXT,
                FOREIGN KEY (channel_id) REFERENCES channels (id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS syncfolders (
                folder_path TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                playlist_id TEXT NOT NULL,
                sync_date DATETIME,
                PRIMARY KEY (folder_path, channel_id, playlist_id),
                FOREIGN KEY (channel_id) REFERENCES channels (id),
                FOREIGN KEY (playlist_id) REFERENCES playlists (id)
            );
        ''')
        conn.commit()
@app.route('/', methods=['GET', 'POST'])
def index():
    error_message = ''
    normal_message = ''
    return render_template('index.html', error_message=error_message, normal_message=normal_message)
if __name__ == "__main__":
    # 初始化下載器
    DOWNLOADER = YouTubeDownloader()
    yt_tracker_init_db()
    app.run(debug=True)
