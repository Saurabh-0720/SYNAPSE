from flask import Flask, jsonify, request, session, send_from_directory, send_file
from flask_cors import CORS
import sqlite3
from datetime import datetime, date, timedelta
import os
import hashlib
import secrets
from functools import wraps

app = Flask(__name__, static_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Configure CORS
CORS(app, 
        resources={r"/api/*": {"origins": "*"}},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

DATABASE = 'leaderboard.db'

# ========== STATIC FILE SERVING ==========

@app.route('/')
def index():
    """Serve the main index.html"""
    return send_file('index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve static files (CSS, JS, etc.)"""
    # Don't serve API routes as static files
    if filename.startswith('api/'):
        return "Not found", 404
    
    if os.path.exists(filename):
        return send_from_directory('.', filename)
    return "File not found", 404

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Serve assets folder"""
    return send_from_directory('assets', filename)

# ========== AUTHENTICATION ==========

def hash_password(password):
    """Hash password with SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Create admin users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create members table
    c.execute('''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create weekly leaderboard table
    c.execute('''
        CREATE TABLE IF NOT EXISTS weekly_leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            sessions_attended INTEGER DEFAULT 0,
            assessments_submitted INTEGER DEFAULT 0,
            bonus_points INTEGER DEFAULT 0,
            week_start DATE,
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(member_id, week_start)
        )
    ''')
    
    # Create monthly leaderboard table
    c.execute('''
        CREATE TABLE IF NOT EXISTS monthly_leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER,
            sessions_attended INTEGER DEFAULT 0,
            assessments_submitted INTEGER DEFAULT 0,
            bonus_points INTEGER DEFAULT 0,
            month_year TEXT,
            FOREIGN KEY (member_id) REFERENCES members(id),
            UNIQUE(member_id, month_year)
        )
    ''')
    
    # Create audit log table
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_username TEXT,
            action TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Check if default admin exists
    existing_admin = c.execute('SELECT * FROM admin_users WHERE username = ?', ('admin',)).fetchone()
    if not existing_admin:
        default_password = hash_password('synapse2024')
        c.execute('INSERT INTO admin_users (username, password_hash) VALUES (?, ?)', 
                  ('admin', default_password))
        print("⚠️  DEFAULT ADMIN CREATED - Username: admin, Password: synapse2024")
        print("⚠️  PLEASE CHANGE THIS PASSWORD IMMEDIATELY!")
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def log_action(action, details):
    """Log admin actions"""
    username = session.get('username', 'system')
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO audit_log (admin_username, action, details) VALUES (?, ?, ?)',
              (username, action, details))
    conn.commit()
    conn.close()

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({
                'success': False,
                'error': 'Authentication required',
                'code': 'AUTH_REQUIRED'
            }), 401
        return f(*args, **kwargs)
    return decorated_function

# ========== HELPER FUNCTIONS ==========

def calculate_points(sessions, assessments, bonus):
    return (sessions * 10) + (assessments * 20) + bonus

def get_week_start():
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    return week_start.isoformat()

def get_month_year():
    return datetime.now().strftime('%Y-%m')

# ========== AUTH ROUTES ==========

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Admin login"""
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400
    
    password_hash = hash_password(password)
    
    conn = get_db()
    user = conn.execute('SELECT * FROM admin_users WHERE username = ? AND password_hash = ?',
                       (username, password_hash)).fetchone()
    conn.close()
    
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        
        log_action('LOGIN', f'User {username} logged in')
        
        return jsonify({
            'success': True,
            'user': {
                'username': user['username'],
                'role': user['role']
            }
        })
    else:
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/api/auth/logout', methods=['POST'])
@require_auth
def logout():
    """Admin logout"""
    username = session.get('username')
    session.clear()
    log_action('LOGOUT', f'User {username} logged out')
    return jsonify({'success': True, 'message': 'Logged out'})

@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    """Check authentication status"""
    if 'user_id' in session:
        return jsonify({
            'success': True,
            'authenticated': True,
            'user': {
                'username': session.get('username'),
                'role': session.get('role')
            }
        })
    return jsonify({'success': True, 'authenticated': False})

# ========== PUBLIC ROUTES ==========

@app.route('/api/leaderboard/weekly', methods=['GET'])
def get_weekly_leaderboard():
    """Get weekly leaderboard"""
    week_start = get_week_start()
    
    conn = get_db()
    query = '''
        SELECT 
            m.id,
            m.name,
            m.avatar,
            COALESCE(w.sessions_attended, 0) as sessionsAttended,
            COALESCE(w.assessments_submitted, 0) as assessmentsSubmitted,
            COALESCE(w.bonus_points, 0) as bonusPoints
        FROM members m
        LEFT JOIN weekly_leaderboard w ON m.id = w.member_id AND w.week_start = ?
        ORDER BY 
            (COALESCE(w.sessions_attended, 0) * 10 + 
             COALESCE(w.assessments_submitted, 0) * 20 + 
             COALESCE(w.bonus_points, 0)) DESC
    '''
    
    rows = conn.execute(query, (week_start,)).fetchall()
    conn.close()
    
    data = []
    for row in rows:
        member = dict(row)
        member['totalPoints'] = calculate_points(
            member['sessionsAttended'],
            member['assessmentsSubmitted'],
            member['bonusPoints']
        )
        data.append(member)
    
    return jsonify({
        'success': True,
        'type': 'weekly',
        'week_start': week_start,
        'data': data
    })

@app.route('/api/leaderboard/monthly', methods=['GET'])
def get_monthly_leaderboard():
    """Get monthly leaderboard"""
    month_year = get_month_year()
    
    conn = get_db()
    query = '''
        SELECT 
            m.id,
            m.name,
            m.avatar,
            COALESCE(ml.sessions_attended, 0) as sessionsAttended,
            COALESCE(ml.assessments_submitted, 0) as assessmentsSubmitted,
            COALESCE(ml.bonus_points, 0) as bonusPoints
        FROM members m
        LEFT JOIN monthly_leaderboard ml ON m.id = ml.member_id AND ml.month_year = ?
        ORDER BY 
            (COALESCE(ml.sessions_attended, 0) * 10 + 
             COALESCE(ml.assessments_submitted, 0) * 20 + 
             COALESCE(ml.bonus_points, 0)) DESC
    '''
    
    rows = conn.execute(query, (month_year,)).fetchall()
    conn.close()
    
    data = []
    for row in rows:
        member = dict(row)
        member['totalPoints'] = calculate_points(
            member['sessionsAttended'],
            member['assessmentsSubmitted'],
            member['bonusPoints']
        )
        data.append(member)
    
    return jsonify({
        'success': True,
        'type': 'monthly',
        'month_year': month_year,
        'data': data
    })

@app.route('/api/members', methods=['GET'])
def get_members():
    """Get all members"""
    conn = get_db()
    members = conn.execute('SELECT id, name, avatar FROM members ORDER BY name').fetchall()
    conn.close()
    
    return jsonify({
        'success': True,
        'data': [dict(member) for member in members]
    })

# ========== ADMIN ROUTES ==========

@app.route('/api/admin/members', methods=['POST'])
@require_auth
def add_member():
    """Add a new member"""
    data = request.json
    
    if not data.get('name'):
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    
    name = data['name']
    avatar = data.get('avatar', f"https://ui-avatars.com/api/?name={name.replace(' ', '+')}&background=ff642c&color=fff&size=200")
    
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO members (name, avatar) VALUES (?, ?)', (name, avatar))
        member_id = c.lastrowid
        conn.commit()
        conn.close()
        
        log_action('ADD_MEMBER', f'Added member: {name} (ID: {member_id})')
        
        return jsonify({
            'success': True,
            'data': {'id': member_id, 'name': name, 'avatar': avatar}
        })
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': 'Member already exists'}), 400

@app.route('/api/admin/leaderboard/weekly/update', methods=['POST'])
@require_auth
def update_weekly_leaderboard():
    """Update weekly leaderboard"""
    data = request.json
    
    member_id = data.get('member_id')
    sessions = data.get('sessions_attended', 0)
    assessments = data.get('assessments_submitted', 0)
    bonus = data.get('bonus_points', 0)
    
    if not member_id:
        return jsonify({'success': False, 'error': 'member_id is required'}), 400
    
    week_start = get_week_start()
    
    conn = get_db()
    c = conn.cursor()
    
    member = c.execute('SELECT name FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        conn.close()
        return jsonify({'success': False, 'error': 'Member not found'}), 404
    
    c.execute('''
        INSERT INTO weekly_leaderboard 
        (member_id, sessions_attended, assessments_submitted, bonus_points, week_start)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(member_id, week_start) DO UPDATE SET
            sessions_attended = excluded.sessions_attended,
            assessments_submitted = excluded.assessments_submitted,
            bonus_points = excluded.bonus_points
    ''', (member_id, sessions, assessments, bonus, week_start))
    
    conn.commit()
    conn.close()
    
    log_action('UPDATE_WEEKLY', f'Updated weekly stats for {member["name"]}')
    
    return jsonify({'success': True, 'message': 'Weekly leaderboard updated'})

@app.route('/api/admin/leaderboard/monthly/update', methods=['POST'])
@require_auth
def update_monthly_leaderboard():
    """Update monthly leaderboard"""
    data = request.json
    
    member_id = data.get('member_id')
    sessions = data.get('sessions_attended', 0)
    assessments = data.get('assessments_submitted', 0)
    bonus = data.get('bonus_points', 0)
    
    if not member_id:
        return jsonify({'success': False, 'error': 'member_id is required'}), 400
    
    month_year = get_month_year()
    
    conn = get_db()
    c = conn.cursor()
    
    member = c.execute('SELECT name FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        conn.close()
        return jsonify({'success': False, 'error': 'Member not found'}), 404
    
    c.execute('''
        INSERT INTO monthly_leaderboard 
        (member_id, sessions_attended, assessments_submitted, bonus_points, month_year)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(member_id, month_year) DO UPDATE SET
            sessions_attended = excluded.sessions_attended,
            assessments_submitted = excluded.assessments_submitted,
            bonus_points = excluded.bonus_points
    ''', (member_id, sessions, assessments, bonus, month_year))
    
    conn.commit()
    conn.close()
     
    log_action('UPDATE_MONTHLY', f'Updated monthly stats for {member["name"]}')
    
    return jsonify({'success': True, 'message': 'Monthly leaderboard updated'})

@app.route('/api/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'success': True,
        'message': 'SYNAPSE Leaderboard API is running',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/admin/members/<int:member_id>', methods=['DELETE'])
@require_auth
def delete_member(member_id):
    """Delete a member from the system"""
    conn = get_db()
    c = conn.cursor()
    
    member = c.execute('SELECT name FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        conn.close()
        return jsonify({'success': False, 'error': 'Member not found'}), 404
    
    c.execute('DELETE FROM weekly_leaderboard WHERE member_id = ?', (member_id,))
    c.execute('DELETE FROM monthly_leaderboard WHERE member_id = ?', (member_id,))
    c.execute('DELETE FROM members WHERE id = ?', (member_id,))
    
    conn.commit()
    conn.close()
    
    log_action('DELETE_MEMBER', f'Deleted member: {member["name"]} (ID: {member_id})')
    
    return jsonify({'success': True, 'message': f'Member {member["name"]} deleted'})

@app.route('/api/admin/leaderboard/weekly/<int:member_id>', methods=['DELETE'])
@require_auth
def delete_weekly_entry(member_id):
    week_start = get_week_start()
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM weekly_leaderboard WHERE member_id = ? AND week_start = ?', (member_id, week_start))
    if not c.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Entry not found'}), 404
    
    c.execute('DELETE FROM weekly_leaderboard WHERE member_id = ? AND week_start = ?', (member_id, week_start))
    conn.commit()
    conn.close()
    
    log_action('DELETE_WEEKLY', f'Deleted weekly stats for member ID {member_id}')
    return jsonify({'success': True, 'message': 'Weekly leaderboard entry deleted'})

@app.route('/api/admin/leaderboard/monthly/<int:member_id>', methods=['DELETE'])
@require_auth
def delete_monthly_entry(member_id):
    month_year = get_month_year()
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT * FROM monthly_leaderboard WHERE member_id = ? AND month_year = ?', (member_id, month_year))
    if not c.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Entry not found'}), 404
    
    c.execute('DELETE FROM monthly_leaderboard WHERE member_id = ? AND month_year = ?', (member_id, month_year))
    conn.commit()
    conn.close()
    
    log_action('DELETE_MONTHLY', f'Deleted monthly stats for member ID {member_id}')
    return jsonify({'success': True, 'message': 'Monthly leaderboard entry deleted'})

if __name__ == '__main__':
    init_db()
    print("=" * 60)
    print("SYNAPSE Leaderboard API")
    print("=" * 60)
    print("Database initialized!")
    print("\nDefault admin: admin / synapse2024")
    print("Change password after first login!")
    print("=" * 60)
    
    # Use PORT environment variable for deployment
    port = int(os.environ.get('PORT', 5001))
    # Set debug=False for production
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    app.run(debug=debug, host='0.0.0.0', port=port)
