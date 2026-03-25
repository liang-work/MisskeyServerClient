import os
import json
import uuid
import asyncio
import aiosqlite
import requests
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from datetime import datetime

app = Flask(__name__)
app.secret_key = str(uuid.uuid4())

DB_PATH = os.path.join(os.path.dirname(__file__), 'misskey.db')
SCHEMA_VERSION = 2

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                server_url TEXT,
                access_token TEXT,
                app_secret TEXT,
                session_token TEXT,
                created_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id TEXT PRIMARY KEY,
                key TEXT,
                value TEXT,
                updated_at TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS cache (
                id TEXT PRIMARY KEY,
                key TEXT,
                value TEXT,
                expires_at TEXT
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        ''')
        
        current_version = 0
        try:
            cursor = await db.execute('SELECT version FROM schema_version ORDER BY version DESC LIMIT 1')
            row = await cursor.fetchone()
            current_version = row[0] if row else 0
        except:
            pass
        
        if current_version < 1:
            try:
                await db.execute('ALTER TABLE sessions ADD COLUMN user_data TEXT')
            except:
                pass
            try:
                await db.execute('ALTER TABLE config ADD COLUMN updated_at TEXT')
            except:
                pass
        
        if current_version < 2:
            await db.execute('CREATE TABLE IF NOT EXISTS local_settings (key TEXT PRIMARY KEY, value TEXT)')
        
        try:
            await db.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (?)', (SCHEMA_VERSION,))
        except:
            pass
        await db.commit()

async def get_config(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT value FROM config WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            return row['value'] if row else default

async def set_config(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)', 
                        (key, value, datetime.now().isoformat()))
        await db.commit()

async def save_session(session_id, server_url, access_token, app_secret, session_token, user_data=None):
    async with aiosqlite.connect(DB_PATH) as db:
        user_data_json = json.dumps(user_data) if user_data else None
        await db.execute('''
            INSERT OR REPLACE INTO sessions (id, server_url, access_token, app_secret, session_token, created_at, user_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (session_id, server_url, access_token, app_secret, session_token, datetime.now().isoformat(), user_data_json))
        await db.commit()

async def get_session(session_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)) as cursor:
            return await cursor.fetchone()

async def get_session_by_token(session_token):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM sessions WHERE session_token = ?', (session_token,)) as cursor:
            return await cursor.fetchone()

async def delete_session(session_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        await db.commit()

async def save_local_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('INSERT OR REPLACE INTO local_settings (key, value) VALUES (?, ?)', (key, value))
        await db.commit()

async def get_local_setting(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT value FROM local_settings WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            return row['value'] if row else default

async def save_cache(key, value, ttl=3600):
    async with aiosqlite.connect(DB_PATH) as db:
        cache_id = str(uuid.uuid4())
        expires_at = datetime.now().timestamp() + ttl
        await db.execute('INSERT OR REPLACE INTO cache (id, key, value, expires_at) VALUES (?, ?, ?, ?)',
                        (cache_id, key, json.dumps(value), expires_at))
        await db.commit()

async def get_cache(key):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT value, expires_at FROM cache WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            if row and row['expires_at'] > datetime.now().timestamp():
                return json.loads(row['value'])
    return None

def misskey_request(method, endpoint, server_url, access_token=None, data=None):
    base_url = server_url.rstrip('/') + '/api'
    url = f"{base_url}{endpoint}"
    print(f"[DEBUG] Request URL: {url}")
    print(f"[DEBUG] Request data: {data}")
    headers = {'Content-Type': 'application/json'}
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    
    body = data if data else {}
    if access_token:
        body['i'] = access_token
    
    try:
        response = requests.post(url, json=body, headers=headers, timeout=30)
        print(f"[DEBUG] Response status: {response.status_code}")
        print(f"[DEBUG] Response text: {response.text[:500]}")
        try:
            result = response.json()
            if isinstance(result, str):
                return {'error': {'message': result, 'code': 'STRING_RESPONSE'}}
            return result
        except:
            return {'error': {'message': f'Invalid response from server: {response.status_code}', 'code': response.status_code}}
    except requests.exceptions.RequestException as e:
        return {'error': {'message': str(e), 'code': 'REQUEST_FAILED'}}

def get_error_message(result, default='Unknown error'):
    if not isinstance(result, dict):
        return str(result)
    error = result.get('error')
    if error is None:
        return default
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return error.get('message', default)
    return default

@app.route('/')
def index():
    session_id = request.cookies.get('session_id')
    return render_template('index.html', session_id=session_id)

@app.route('/compose')
def compose():
    session_id = request.cookies.get('session_id')
    return render_template('compose.html', session_id=session_id)

@app.route('/settings')
def settings():
    session_id = request.cookies.get('session_id')
    return render_template('settings.html', session_id=session_id)

@app.route('/drive')
def drive():
    session_id = request.cookies.get('session_id')
    return render_template('drive.html', session_id=session_id)

@app.route('/api/config', methods=['GET', 'POST'])
async def api_config():
    if request.method == 'POST':
        data = request.json
        await set_config('server_url', data.get('server_url', ''))
        return jsonify({'success': True})
    else:
        server_url = await get_config('server_url', '')
        return jsonify({'server_url': server_url})

@app.route('/api/local-settings', methods=['GET', 'POST'])
async def api_local_settings():
    if request.method == 'POST':
        data = request.json
        key = data.get('key')
        value = data.get('value')
        if key:
            await save_local_setting(key, json.dumps(value))
        return jsonify({'success': True})
    else:
        key = request.args.get('key')
        if key:
            value = await get_local_setting(key)
            if value:
                return jsonify({'value': json.loads(value)})
            return jsonify({'value': None})
        rows = await get_all_local_settings()
        return jsonify({'settings': rows})

async def get_all_local_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM local_settings') as cursor:
            rows = await cursor.fetchall()
            return {row['key']: json.loads(row['value']) for row in rows}

@app.route('/api/login/start', methods=['POST'])
async def login_start():
    data = request.json
    server_url = data.get('server_url', '').strip()
    
    if not server_url:
        return jsonify({'error': 'Server URL is required'}), 400
    
    app_name = "Misskey Client"
    callback_url = url_for('login_callback', _external=True)
    
    create_app_data = {
        'name': app_name,
        'description': 'Misskey Web Client',
        'permission': [
            'read:account', 'read:notes', 'write:notes', 'write:reactions',
            'read:following', 'write:following', 'read:mutes', 'write:mutes',
            'read:notifications', 'write:notifications', 'read:favorites', 'write:favorites',
            'read:channels', 'write:channels', 'read:drive', 'write:drive',
            'read:clips', 'write:clips', 'read:users', 'write:users'
        ],
        'callbackUrl': callback_url
    }
    
    try:
        result = misskey_request('POST', '/app/create', server_url, data=create_app_data)
        
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to create app')}), 400
        
        app_secret = result.get('secret')
        session_token_data = misskey_request('POST', '/auth/session/generate', server_url, data={'appSecret': app_secret})
        
        if 'error' in session_token_data:
            return jsonify({'error': get_error_message(session_token_data, 'Failed to generate session')}), 400
        
        session_token = session_token_data.get('token')
        auth_url = session_token_data.get('url')
        
        session_id = str(uuid.uuid4())
        await save_session(session_id, server_url, None, app_secret, session_token)
        
        return jsonify({
            'auth_url': auth_url,
            'session_id': session_id
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/login/callback')
async def login_callback():
    session_token = request.args.get('token')
    session_id = request.args.get('session_id')
    
    print(f"[DEBUG] Full query params: {dict(request.args)}")
    print(f"[DEBUG] All cookies: {dict(request.cookies)}")
    print(f"[DEBUG] Callback - token: {session_token[:20] if session_token else None}..., session_id: {session_id}")
    
    if not session_token:
        return render_template('error.html', error='Missing token parameter')
    
    if session_id:
        stored_session = await get_session(session_id)
    else:
        stored_session = await get_session_by_token(session_token)
    
    if not stored_session:
        return render_template('error.html', error='Session not found. Please login again.')
    
    session_id = stored_session['id']
    server_url = stored_session['server_url']
    app_secret = stored_session['app_secret']
    
    try:
        token_data = misskey_request('POST', '/auth/session/userkey', server_url, data={
            'appSecret': app_secret,
            'token': session_token
        })
        
        if 'error' in token_data:
            return render_template('error.html', error=get_error_message(token_data, 'Failed to get access token'))
        
        access_token = token_data.get('accessToken')
        user = token_data.get('accessToken')
        
        user_info = misskey_request('POST', '/i', server_url, access_token, {})
        
        await save_session(session_id, server_url, access_token, app_secret, None, user_info)
        
        response = redirect(url_for('index'))
        response.set_cookie('session_id', session_id, httponly=True)
        return response
        
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/api/login/status')
async def login_status():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'logged_in': False})
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'logged_in': False})
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        user_data = misskey_request('POST', '/i', server_url, access_token, {})
        if 'error' in user_data:
            return jsonify({'logged_in': False})
        
        return jsonify({
            'logged_in': True,
            'user': user_data,
            'server_url': server_url
        })
    except:
        return jsonify({'logged_in': False})

@app.route('/api/logout', methods=['POST'])
async def logout():
    session_id = request.cookies.get('session_id')
    if session_id:
        await delete_session(session_id)
    
    response = jsonify({'success': True})
    response.delete_cookie('session_id')
    return response

@app.route('/api/notes/timeline', methods=['GET'])
async def get_timeline():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    timeline_type = request.args.get('type', 'home')
    limit = int(request.args.get('limit', 20))
    until_id = request.args.get('until_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    endpoint_map = {
        'home': '/notes/timeline',
        'local': '/notes/local-timeline',
        'global': '/notes/global-timeline',
        'hybrid': '/notes/hybrid-timeline'
    }
    
    endpoint = endpoint_map.get(timeline_type, '/notes/timeline')
    
    data = {'limit': limit}
    if until_id:
        data['untilId'] = until_id
    
    try:
        result = misskey_request('POST', endpoint, server_url, access_token, data)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch timeline')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/create', methods=['POST'])
async def create_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    text = data.get('text', '')
    cw = data.get('cw')
    visibility = data.get('visibility', 'public')
    reply_id = data.get('reply_id')
    renote_id = data.get('renote_id')
    file_ids = data.get('file_ids')
    poll = data.get('poll')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    payload = {
        'text': text,
        'visibility': visibility
    }
    if cw:
        payload['cw'] = cw
    if renote_id:
        payload['renoteId'] = renote_id
    if reply_id:
        payload['replyId'] = reply_id
    if file_ids:
        payload['fileIds'] = file_ids
    if poll:
        payload['poll'] = poll
    
    try:
        result = misskey_request('POST', '/notes/create', server_url, access_token, payload)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to create note')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/schedule', methods=['POST'])
async def schedule_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    text = data.get('text', '')
    cw = data.get('cw')
    visibility = data.get('visibility', 'public')
    scheduled_at = data.get('scheduled_at')
    file_ids = data.get('file_ids')
    
    if not scheduled_at:
        return jsonify({'error': 'Scheduled time is required'}), 400
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    payload = {
        'text': text,
        'cw': cw,
        'visibility': visibility,
        'scheduledAt': scheduled_at
    }
    if file_ids:
        payload['fileIds'] = file_ids
    
    try:
        result = misskey_request('POST', '/notes/schedule/create', server_url, access_token, payload)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to schedule note')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/react', methods=['POST'])
async def react_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    reaction = data.get('reaction', '👍')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/reactions/create', server_url, access_token, {
            'noteId': note_id,
            'reaction': reaction
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to react')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/unreact', methods=['POST'])
async def unreact_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/reactions/delete', server_url, access_token, {
            'noteId': note_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to remove reaction')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/reactions', methods=['GET'])
async def get_note_reactions():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    note_id = request.args.get('note_id')
    reaction_type = request.args.get('type')
    limit = int(request.args.get('limit', 20))
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    data = {'noteId': note_id, 'limit': limit}
    if reaction_type:
        data['type'] = reaction_type
    
    try:
        result = misskey_request('POST', '/notes/reactions', server_url, access_token, data)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch reactions')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/renote', methods=['POST'])
async def renote():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/renote', server_url, access_token, {
            'noteId': note_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to renote')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/delete', methods=['POST'])
async def delete_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/delete', server_url, access_token, {
            'noteId': note_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to delete note')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/update', methods=['POST'])
async def update_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    text = data.get('text')
    cw = data.get('cw')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/edit', server_url, access_token, {
            'noteId': note_id,
            'text': text,
            'cw': cw
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to update note')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/show', methods=['GET'])
async def get_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    note_id = request.args.get('id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/show', server_url, access_token, {'id': note_id})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch note')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/thread', methods=['GET'])
async def get_thread():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    note_id = request.args.get('id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        note = misskey_request('POST', '/notes/show', server_url, access_token, {'id': note_id})
        if 'error' in note:
            return jsonify({'error': get_error_message(note, 'Failed to fetch note')}), 400
        
        replies = misskey_request('POST', '/notes/replies', server_url, access_token, {
            'noteId': note_id,
            'limit': 100
        })
        if 'error' in replies:
            return jsonify({'error': get_error_message(replies, 'Failed to fetch replies')}), 400
        
        return jsonify({'note': note, 'replies': replies})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notifications', methods=['GET'])
async def get_notifications():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    limit = int(request.args.get('limit', 20))
    since_id = request.args.get('since_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    data = {'limit': limit}
    if since_id:
        data['sinceId'] = since_id
    
    try:
        result = misskey_request('POST', '/i/notifications', server_url, access_token, data)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch notifications')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/i', methods=['GET'])
async def get_my_info():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/i', server_url, access_token, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch user info')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/profile', methods=['GET', 'POST'])
async def user_profile():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    if request.method == 'POST':
        data = request.json
        name = data.get('name')
        description = data.get('description')
        location = data.get('location')
        birthday = data.get('birthday')
        
        try:
            result = misskey_request('POST', '/i/update', server_url, access_token, {
                'name': name,
                'description': description,
                'location': location,
                'birthday': birthday
            })
            if 'error' in result:
                return jsonify({'error': get_error_message(result, 'Failed to update profile')}), 400
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        try:
            result = misskey_request('POST', '/i', server_url, access_token, {})
            if 'error' in result:
                return jsonify({'error': get_error_message(result, 'Failed to get profile')}), 400
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/api/users/search', methods=['GET'])
async def search_users():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    query = request.args.get('q', '')
    limit = int(request.args.get('limit', 10))
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/users/search', server_url, access_token, {
            'query': query,
            'limit': limit
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to search users')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/show', methods=['GET'])
async def get_user():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = request.args.get('id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/users/show', server_url, access_token, {'userId': user_id})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch user')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/notes', methods=['GET'])
async def get_user_notes():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = request.args.get('user_id')
    limit = int(request.args.get('limit', 20))
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/users/notes', server_url, access_token, {
            'userId': user_id,
            'limit': limit
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch user notes')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/follow', methods=['POST'])
async def follow_user():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    user_id = data.get('user_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/following/create', server_url, access_token, {
            'userId': user_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to follow')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/unfollow', methods=['POST'])
async def unfollow_user():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    user_id = data.get('user_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/following/delete', server_url, access_token, {
            'userId': user_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to unfollow')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/files', methods=['GET'])
async def get_drive_files():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    folder_id = request.args.get('folder_id')
    limit = int(request.args.get('limit', 30))
    type_filter = request.args.get('type')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    data = {'limit': limit}
    if folder_id:
        data['folderId'] = folder_id
    if type_filter:
        data['type'] = type_filter
    
    try:
        result = misskey_request('POST', '/drive/files', server_url, access_token, data)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch drive files')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/files/create', methods=['POST'])
async def upload_file():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400
    
    folder_id = request.form.get('folder_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        files = {'file': (file.filename, file.stream, file.content_type)}
        data = {'i': access_token}
        if folder_id:
            data['folderId'] = folder_id
        
        response = requests.post(
            f"{server_url.rstrip('/')}/api/drive/files/create",
            data=data,
            files=files,
            timeout=60
        )
        result = response.json()
        
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to upload file')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/files/delete', methods=['POST'])
async def delete_drive_file():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    file_id = data.get('file_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/drive/files/delete', server_url, access_token, {
            'fileId': file_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to delete file')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/files/update', methods=['POST'])
async def update_drive_file():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    file_id = data.get('file_id')
    name = data.get('name')
    folder_id = data.get('folder_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    payload = {'fileId': file_id}
    if name:
        payload['name'] = name
    if folder_id:
        payload['folderId'] = folder_id
    
    try:
        result = misskey_request('POST', '/drive/files/update', server_url, access_token, payload)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to update file')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/folders', methods=['GET', 'POST'])
async def get_drive_folders():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    if request.method == 'POST':
        data = request.json or {}
    else:
        data = {}
        data['folderId'] = request.args.get('folder_id')
    
    data['limit'] = data.get('limit', 30)
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/drive/folders', server_url, access_token, data)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch folders')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/drive/folders/create', methods=['POST'])
async def create_drive_folder():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    name = data.get('name')
    folder_id = data.get('folder_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    payload = {'name': name}
    if folder_id:
        payload['parentId'] = folder_id
    
    try:
        result = misskey_request('POST', '/drive/folders/create', server_url, access_token, payload)
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to create folder')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/favorite', methods=['POST'])
async def favorite_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/favorites/create', server_url, access_token, {
            'noteId': note_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to favorite')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/unfavorite', methods=['POST'])
async def unfavorite_note():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    note_id = data.get('note_id')
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/favorites/delete', server_url, access_token, {
            'noteId': note_id
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to unfavorite')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/hashtags/trend', methods=['GET'])
async def get_trending_hashtags():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/hashtags/trend', server_url, access_token, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch trending')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/meta', methods=['GET'])
async def get_meta():
    session_id = request.cookies.get('session_id')
    server_url = None
    
    if session_id:
        stored_session = await get_session(session_id)
        if stored_session:
            server_url = stored_session['server_url']
    
    if not server_url:
        server_url = await get_config('server_url', '')
    
    if not server_url:
        return jsonify({'error': 'No server configured'}), 400
    
    try:
        result = misskey_request('POST', '/meta', server_url, None, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch meta')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/instance', methods=['GET'])
async def get_instance_info():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/meta', server_url, access_token, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch instance info')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/reactions', methods=['GET'])
async def get_reactions():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    if access_token:
        try:
            result = misskey_request('POST', '/meta', server_url, access_token, {})
            reactions = result.get('defaultReaction', '👍')
            return jsonify({'reactions': [reactions, '❤️', '😆', '😮', '😢', '😠', '👍', '👎', '🎉', '🍕', '🐦', '😻', '💯', '✨', '🙌', '👏']})
        except:
            pass
    
    return jsonify({'reactions': ['👍', '❤️', '😆', '😮', '😢', '😠', '👎', '🎉', '🍕', '🐦']})

@app.route('/api/clips', methods=['GET'])
async def get_clips():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/clips/list', server_url, access_token, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch clips')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/channels', methods=['GET'])
async def get_channels():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/channels/followed', server_url, access_token, {})
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to fetch channels')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/notes/search', methods=['GET'])
async def search_notes():
    session_id = request.cookies.get('session_id')
    if not session_id:
        return jsonify({'error': 'Not logged in'}), 401
    
    stored_session = await get_session(session_id)
    if not stored_session or not stored_session['access_token']:
        return jsonify({'error': 'Not logged in'}), 401
    
    query = request.args.get('q', '')
    limit = int(request.args.get('limit', 20))
    
    server_url = stored_session['server_url']
    access_token = stored_session['access_token']
    
    try:
        result = misskey_request('POST', '/notes/search', server_url, access_token, {
            'query': query,
            'limit': limit
        })
        if 'error' in result:
            return jsonify({'error': get_error_message(result, 'Failed to search notes')}), 400
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    asyncio.run(init_db())
    app.run(debug=True, port=5000)
