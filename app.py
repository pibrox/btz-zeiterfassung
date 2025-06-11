from flask import Flask, render_template, request, redirect, url_for, session, g, flash, jsonify, make_response
import sqlite3
import os
import logging
from datetime import datetime, timedelta
import pytz
from flask_bcrypt import Bcrypt
import json
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# Helper function to parse datetime strings
def try_parse(date_string):
    """Try to parse a datetime string in various formats."""
    if not date_string:
        return None
    
    formats = [
        # Standard formats
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%S.%f',
        # Formats with timezone info
        '%Y-%m-%d %H:%M:%S%z',
        '%Y-%m-%d %H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S.%f%z'
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_string, fmt)
        except ValueError:
            continue
    
    # If standard parsing fails, try using dateutil as a fallback
    try:
        from dateutil import parser
        return parser.parse(date_string)
    except:
        pass
    
    return None

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this in production
bcrypt = Bcrypt(app)
DATABASE = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'attendance.db')

# Helper functions for templates
def get_duration(start_time, end_time):
    """Calculate the duration between two time strings."""
    try:
        start = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        end = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
        diff = end - start
        # Calculate total seconds including days
        total_seconds = int(diff.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours}:{minutes:02d}"
    except (ValueError, TypeError):
        return "-"

def format_minutes(minutes):
    """Format minutes to hours:minutes format."""
    if minutes is None:
        return "-"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}:{mins:02d}"

@app.context_processor
def utility_processor():
    """Add utility functions to templates."""
    return {
        'get_duration': get_duration,
        'format_minutes': format_minutes
    }

# Set the timezone to CEST (Central European Summer Time)
TIMEZONE = 'Europe/Berlin'

def get_local_time():
    """Get the current time in CEST (Central European Summer/Winter Time)"""
    return datetime.now(pytz.timezone(TIMEZONE))

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def check_and_fix_db_schema():
    """Check and fix database schema issues."""
    print("Checking and fixing database schema...")
    
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # Check if is_admin column exists in users table
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'is_admin' not in columns:
            print("Adding is_admin column to users table...")
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
                # Set the admin user as admin (username = 'admin')
                cursor.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
                db.commit()
                print("Added is_admin column successfully and set admin user")
            except sqlite3.Error as e:
                print(f"Error adding column: {e}")
        
        # Add new enhanced user fields
        enhanced_user_columns = {
            'first_name': 'TEXT',
            'last_name': 'TEXT', 
            'employee_id': 'TEXT',
            'user_role': 'TEXT DEFAULT "employee"',
            'department': 'TEXT',
            'account_status': 'TEXT DEFAULT "active"',
            'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'updated_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        
        for column_name, column_def in enhanced_user_columns.items():
            if column_name not in columns:
                print(f"Adding {column_name} column to users table...")
                try:
                    cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_def}')
                    db.commit()
                    print(f"Added {column_name} column successfully")
                except sqlite3.Error as e:
                    print(f"Error adding column {column_name}: {e}")
        
        # Update existing users with default values for new fields
        try:
            print("Updating existing users with default values...")
            
            # Set default user_role for existing users
            cursor.execute("UPDATE users SET user_role = 'employee' WHERE user_role IS NULL OR user_role = ''")
            
            # Set admin role for admin users
            cursor.execute("UPDATE users SET user_role = 'admin' WHERE is_admin = 1")
            
            # Set default account_status for existing users
            cursor.execute("UPDATE users SET account_status = 'active' WHERE account_status IS NULL OR account_status = ''")
            
            # Set created_at for existing users (use current timestamp as fallback)
            current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute("UPDATE users SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (current_time,))
            cursor.execute("UPDATE users SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''", (current_time,))
            
            db.commit()
            print("Updated existing users with default values")
            
        except sqlite3.Error as e:
            print(f"Error updating existing users: {e}")
                
        # Check if the arbzg_breaks_enabled column exists in user_settings
        cursor.execute("PRAGMA table_info(user_settings)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'arbzg_breaks_enabled' not in columns:
            print("Adding arbzg_breaks_enabled column to user_settings...")
            try:
                cursor.execute("ALTER TABLE user_settings ADD COLUMN arbzg_breaks_enabled BOOLEAN DEFAULT 1")
                cursor.execute("UPDATE user_settings SET arbzg_breaks_enabled = 1")
                db.commit()
                print("Added arbzg_breaks_enabled column successfully")
            except sqlite3.Error as e:
                print(f"Error adding column: {e}")
        
        # Add lunch period preference columns
        lunch_period_columns = [
            ('lunch_period_start_hour', 'INTEGER DEFAULT 11'),
            ('lunch_period_start_minute', 'INTEGER DEFAULT 30'),
            ('lunch_period_end_hour', 'INTEGER DEFAULT 14'),
            ('lunch_period_end_minute', 'INTEGER DEFAULT 0')
        ]
        
        for column_name, column_def in lunch_period_columns:
            if column_name not in columns:
                print(f"Adding {column_name} column to user_settings...")
                try:
                    cursor.execute(f"ALTER TABLE user_settings ADD COLUMN {column_name} {column_def}")
                    db.commit()
                    print(f"Added {column_name} column successfully")
                except sqlite3.Error as e:
                    print(f"Error adding column {column_name}: {e}")
        
        # Check if the description column exists in breaks table
        cursor.execute("PRAGMA table_info(breaks)")
        break_columns = [column[1] for column in cursor.fetchall()]
        
        if 'description' not in break_columns:
            print("Adding description column to breaks table...")
            try:
                cursor.execute("ALTER TABLE breaks ADD COLUMN description TEXT")
                db.commit()
                print("Added description column successfully")
            except sqlite3.Error as e:
                print(f"Error adding column: {e}")
        
        # Check if the original_username column exists in deletion_requests table
        cursor.execute("PRAGMA table_info(deletion_requests)")
        deletion_columns = [column[1] for column in cursor.fetchall()]
        
        if 'original_username' not in deletion_columns:
            print("Adding original_username column to deletion_requests table...")
            try:
                cursor.execute("ALTER TABLE deletion_requests ADD COLUMN original_username TEXT")
                db.commit()
                print("Added original_username column successfully")
            except sqlite3.Error as e:
                print(f"Error adding column: {e}")
        
        # Ensure all existing users have user_settings records
        try:
            print("Ensuring all users have user_settings records...")
            cursor.execute('''
                INSERT INTO user_settings (user_id, auto_break_detection_enabled, auto_break_threshold_minutes, exclude_breaks_from_billing, arbzg_breaks_enabled)
                SELECT u.id, 1, 30, 1, 1
                FROM users u
                WHERE u.id NOT IN (SELECT user_id FROM user_settings WHERE user_id IS NOT NULL)
            ''')
            db.commit()
            print("Created missing user_settings records")
        except sqlite3.Error as e:
            print(f"Error creating user_settings records: {e}")
        
        # Ensure all existing users have consent records with default 'pending' status
        try:
            print("Ensuring all users have consent records...")
            current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                INSERT INTO user_consents (user_id, consent_status, consent_date)
                SELECT u.id, 'pending', ?
                FROM users u
                WHERE u.id NOT IN (SELECT user_id FROM user_consents WHERE user_id IS NOT NULL)
            ''', (current_time,))
            db.commit()
            print("Created missing consent records")
        except sqlite3.Error as e:
            print(f"Error creating consent records: {e}")
        
        print("Database schema check and fix completed")


def migrate_existing_user_data():
    """Migrate existing user data to new enhanced schema"""
    print("Starting user data migration...")
    
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        try:
            # Get all users that need migration
            cursor.execute("SELECT id, username, is_admin FROM users")
            users = cursor.fetchall()
            
            current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
            
            for user in users:
                user_id, username, is_admin = user
                
                # Determine user role based on existing data
                if is_admin:
                    user_role = 'admin'
                elif username == 'admin':
                    user_role = 'admin'
                else:
                    user_role = 'employee'
                
                # Update user with enhanced data
                try:
                    cursor.execute('''
                        UPDATE users 
                        SET user_role = ?, 
                            account_status = 'active',
                            created_at = COALESCE(created_at, ?),
                            updated_at = ?
                        WHERE id = ?
                    ''', (user_role, current_time, current_time, user_id))
                    
                    print(f"Migrated user {username} (ID: {user_id}) with role: {user_role}")
                    
                except sqlite3.Error as e:
                    print(f"Error migrating user {username}: {e}")
            
            db.commit()
            print("User data migration completed successfully")
            
        except Exception as e:
            print(f"Error during user data migration: {e}")
            db.rollback()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            check_in TIMESTAMP,
            check_out TIMESTAMP,
            has_auto_breaks BOOLEAN DEFAULT 0,
            billable_minutes INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        # Create breaks table for automatic break detection
        cursor.execute('''CREATE TABLE IF NOT EXISTS breaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attendance_id INTEGER,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            duration_minutes INTEGER,
            is_excluded_from_billing BOOLEAN DEFAULT 0,
            is_auto_detected BOOLEAN DEFAULT 0,
            description TEXT,
            FOREIGN KEY(attendance_id) REFERENCES attendance(id)
        )''')
        # Create user settings table
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            auto_break_detection_enabled BOOLEAN DEFAULT 0,
            auto_break_threshold_minutes INTEGER DEFAULT 30,
            exclude_breaks_from_billing BOOLEAN DEFAULT 0,
            arbzg_breaks_enabled BOOLEAN DEFAULT 1,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        # Create consent table for GDPR compliance
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            consent_status TEXT,
            consent_date TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        # Create data deletion log table
        cursor.execute('''CREATE TABLE IF NOT EXISTS data_deletion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            deletion_date TIMESTAMP,
            record_count INTEGER
        )''')
        # Create table for deletion requests
        cursor.execute('''CREATE TABLE IF NOT EXISTS deletion_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            request_date TIMESTAMP,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            admin_notes TEXT,
            processed_by INTEGER,
            processed_date TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(processed_by) REFERENCES users(id)
        )''')
        # Create temp_passwords table for storing temporary plaintext passwords
        cursor.execute('''CREATE TABLE IF NOT EXISTS temp_passwords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            temp_password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        # Insert default admin if not exists
        cursor.execute('SELECT * FROM users WHERE username=?', ("admin",))
        if cursor.fetchone() is None:
            hashed_password = bcrypt.generate_password_hash("admin").decode('utf-8')
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', ("admin", hashed_password))
        
        # Check if user_settings table has defaults for system-wide settings (user_id = 0)
        cursor.execute('SELECT id FROM user_settings WHERE user_id = 0')
        if cursor.fetchone() is None:
            # Insert default system-wide settings
            cursor.execute('''INSERT INTO user_settings 
                          (user_id, auto_break_detection_enabled, auto_break_threshold_minutes, exclude_breaks_from_billing, arbzg_breaks_enabled)
                          VALUES (0, 1, 30, 1, 1)''')
            print("Inserted default system-wide break settings")
        
        db.commit()
        
        # Run the database schema checker and fixer
        check_and_fix_db_schema()

@app.route('/')
def index():
    print("Index route called")
    print(f"Session contents: {session}")
    
    # Check if user is logged in
    if not session.get('username'):
        print("No username in session, redirecting to login")
        return redirect(url_for('login'))
    
    print(f"User logged in: {session.get('username')}")
    
    # Use direct connection to ensure consistent row factory
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Admin can see all users
    if session.get('admin_logged_in'):
        print("Admin user detected")
        cursor.execute('SELECT id, username FROM users')
        users = cursor.fetchall()
    else:
        # Regular users can only see themselves
        print("Regular user detected")
        cursor.execute('SELECT id, username FROM users WHERE id = ?', (session.get('user_id'),))
        users = cursor.fetchall()
    
    conn.close()
    return render_template('index.html', users=users)

@app.route('/login', methods=['GET', 'POST'])
def login():
    print("Login route called with method:", request.method)
    
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        print(f"Login attempt for username: {username}")
        
        # Connect directly to database
        try:
            # Use direct connection instead of get_db() to avoid potential issues with g context
            conn = sqlite3.connect(DATABASE)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Check if the user exists
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            
            if user:
                # Try to validate the password
                try:
                    print(f"Found user: {username}, validating password")
                    if bcrypt.check_password_hash(user['password'], password):
                        # Set session variables
                        session.clear()  # Clear any existing session first
                        session['user_id'] = user['id']
                        session['username'] = user['username']
                        
                        # Check if user is admin - handle both old and new DB schema
                        if 'is_admin' in user.keys() and user['is_admin'] == 1:
                            session['admin_logged_in'] = True
                        elif username == 'admin':  # Fallback for old DB schema
                            session['admin_logged_in'] = True
                            
                        # Update last_login timestamp
                        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
                        cursor.execute('UPDATE users SET last_login = ? WHERE id = ?', (current_time, user['id']))
                        conn.commit()
                            
                        print(f"Login successful for {username}")
                        print(f"Session: {session}")
                        
                        conn.close()
                        flash('You have been logged in successfully!', 'success')
                        
                        # Log the successful login
                        logging.info(f"User {username} logged in successfully")
                        
                        return redirect(url_for('index'))
                    else:
                        conn.close()
                        print(f"Password validation failed for {username}")
                        flash('Invalid username or password', 'error')
                        return render_template('login.html')
                except Exception as e:
                    conn.close()
                    print(f"Password validation error: {str(e)}")
                    logging.error(f"Password verification error: {str(e)}")
                    flash('An error occurred during login', 'error')
                    return render_template('login.html')
            else:
                conn.close()
                print(f"User not found: {username}")
                flash('Invalid username or password', 'error')
                return render_template('login.html')
        except Exception as e:
            print(f"Database error: {str(e)}")
            logging.error(f"Database error during login: {str(e)}")
            flash('An error occurred while connecting to the database', 'error')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('index'))

@app.route('/deletion_requests')
def deletion_requests():
    """Admin interface for managing data deletion requests"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können auf die Datenlöschungsanfragen zugreifen', 'error')
        return redirect(url_for('index'))
    
    db = get_db()
    db.row_factory = sqlite3.Row  # Enable named column access
    cursor = db.cursor()
    
    # Get all deletion requests with user details
    cursor.execute('''
        SELECT dr.id, dr.user_id, 
               COALESCE(dr.original_username, u.username) as username, 
               dr.request_date, dr.reason, dr.status, 
               dr.admin_notes, dr.processed_by, dr.processed_date,
               (SELECT COUNT(*) FROM attendance WHERE user_id = dr.user_id) as record_count
        FROM deletion_requests dr
        JOIN users u ON dr.user_id = u.id
        ORDER BY 
            CASE dr.status 
                WHEN 'pending' THEN 1 
                WHEN 'processing' THEN 2
                WHEN 'completed' THEN 3
                WHEN 'rejected' THEN 4
                ELSE 5
            END,
            dr.request_date DESC
    ''')
    requests = cursor.fetchall()
    
    # Debug: Print the fetched data
    print(f"DEBUG: Found {len(requests)} deletion requests")
    for i, req in enumerate(requests):
        print(f"DEBUG: Request {i}: {dict(req)}")
    
    # Get admin usernames for processed_by field
    admin_usernames = {}
    for req in requests:
        if req['processed_by']:  # If processed_by is not null
            if req['processed_by'] not in admin_usernames:
                cursor.execute('SELECT username FROM users WHERE id = ?', (req['processed_by'],))
                result = cursor.fetchone()
                admin_usernames[req['processed_by']] = result['username'] if result else "Unknown"
    
    message = request.args.get('message')
    message_type = request.args.get('message_type', 'info')
    
    print(f"DEBUG: Passing {len(requests)} requests to template")
    
    return render_template('deletion_requests.html', 
                          requests=requests, 
                          admin_usernames=admin_usernames,
                          message=message,
                          message_type=message_type)

@app.route('/process_deletion_request/<int:request_id>', methods=['POST'])
def process_deletion_request(request_id):
    if 'admin_logged_in' not in session:
        flash('Admin access required', 'error')
        return redirect(url_for('login'))
    
    action = request.form.get('action')
    admin_notes = request.form.get('admin_notes', '')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    now = datetime.now().isoformat()
    admin_id = session.get('user_id')
    
    if action == 'approve':
        # Get user_id from deletion request
        cursor.execute('SELECT user_id FROM deletion_requests WHERE id = ?', (request_id,))
        deletion_request = cursor.fetchone()
        
        if not deletion_request:
            flash('Deletion request not found', 'error')
            return redirect(url_for('deletion_requests'))
        
        user_id = deletion_request['user_id']
        
        # Get the original username before anonymizing
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        user_record = cursor.fetchone()
        original_username = user_record['username'] if user_record else f'user_{user_id}'
        
        # Start transaction
        db.execute('BEGIN TRANSACTION')
        
        try:
            # Store the original username in the deletion request before anonymizing
            cursor.execute('''
                UPDATE deletion_requests
                SET status = 'completed', 
                    processed_by = ?, 
                    processed_date = ?,
                    admin_notes = ?,
                    original_username = ?
                WHERE id = ?
            ''', (admin_id, now, admin_notes, original_username, request_id))
            
            # Delete user's attendance data
            cursor.execute('DELETE FROM attendance WHERE user_id = ?', (user_id,))
            
            # Anonymize the user rather than delete them
            cursor.execute('''
                UPDATE users 
                SET username = 'deleted_user_' || id, 
                    password = 'DELETED'
                WHERE id = ?
            ''', (user_id,))
            
            # Commit transaction
            db.commit()
            flash('User data deleted successfully', 'success')
            
        except Exception as e:
            # Roll back transaction in case of error
            db.rollback()
            logging.error(f"Error during deletion process: {str(e)}")
            flash('Error processing deletion request', 'error')
    
    elif action == 'reject':
        cursor.execute('''
            UPDATE deletion_requests
            SET status = 'rejected', 
                processed_by = ?, 
                processed_date = ?,
                admin_notes = ?
            WHERE id = ?
        ''', (admin_id, now, admin_notes, request_id))
        db.commit()
        flash('Deletion request rejected', 'success')
    
    else:
        flash('Invalid action', 'error')
    
    return redirect(url_for('deletion_requests'))

@app.route('/get_user_settings')
def get_user_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = session['user_id']
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get user settings
    cursor.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    settings = {}
    
    if result:
        # Convert row to dict
        settings = {key: result[key] for key in result.keys()}
    
    # Check if notification_preferences column exists in users table
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'notification_preferences' in columns:
        # Also check for notification preferences if they exist
        cursor.execute('SELECT notification_preferences FROM users WHERE id = ?', (user_id,))
        user_result = cursor.fetchone()
        
        # Add notification preferences if they exist
        if user_result and user_result['notification_preferences']:
            try:
                notification_prefs = json.loads(user_result['notification_preferences'])
                settings.update(notification_prefs)
            except:
                pass
    
    conn.close()
    return jsonify(settings)

@app.route('/get_today_attendance/<int:user_id>')
def get_today_attendance(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    # Only allow admin users or the user themselves to access this data
    if session['user_id'] != user_id and not session.get('admin_logged_in'):
        return jsonify({'error': 'Unauthorized'}), 403
        
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Get today's attendance records
    cursor.execute('''
        SELECT * FROM attendance 
        WHERE user_id = ? AND date(check_in) = ? 
        ORDER BY check_in ASC
    ''', (user_id, today))
    
    records = cursor.fetchall()
    
    # Format records for JSON response
    attendance_data = []
    for record in records:
        record_dict = {key: record[key] for key in record.keys()}
        
        # Get breaks for this attendance record
        cursor.execute('''
            SELECT * FROM breaks
            WHERE attendance_id = ?
            ORDER BY start_time ASC
        ''', (record['id'],))
        
        breaks = cursor.fetchall()
        breaks_list = []
        
        for break_record in breaks:
            break_dict = {key: break_record[key] for key in break_record.keys()}
            breaks_list.append(break_dict)
        
        record_dict['breaks'] = breaks_list
        attendance_data.append(record_dict)
    
    return jsonify(attendance_data)

@app.route('/admin')
def admin():
    """Admin dashboard page"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können auf diese Seite zugreifen', 'error')
        return redirect(url_for('index'))
    
    # Connect to database with row factory
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all users for user selection dropdowns
    cursor.execute('SELECT id, username FROM users ORDER BY username')
    users = cursor.fetchall()
    
    # Get attendance records for the table
    cursor.execute('''
        SELECT a.id, u.username, a.check_in, a.check_out, a.has_auto_breaks, a.billable_minutes
        FROM attendance a 
        JOIN users u ON a.user_id = u.id
        ORDER BY a.check_in DESC 
        LIMIT 100
    ''')
    records = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin.html', users=users, records=records)

@app.route('/user_management')
def user_management():
    """User management page for admins with enhanced user data"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können auf die Benutzerverwaltung zugreifen', 'error')
        return redirect(url_for('index'))
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # First, ensure the users table has all the new columns
    try:
        cursor.execute("PRAGMA table_info(users)")
        existing_columns = [column[1] for column in cursor.fetchall()]
        
        # Add missing columns if they don't exist
        new_columns = {
            'first_name': 'TEXT',
            'last_name': 'TEXT', 
            'employee_id': 'TEXT',
            'user_role': 'TEXT DEFAULT "employee"',
            'department': 'TEXT',
            'account_status': 'TEXT DEFAULT "active"',
            'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'updated_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        
        for column_name, column_def in new_columns.items():
            if column_name not in existing_columns:
                try:
                    cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_def}')
                    logging.info(f"Added missing column {column_name} to users table")
                except sqlite3.Error as e:
                    logging.warning(f"Could not add column {column_name}: {e}")
        
        db.commit()
    except Exception as e:
        logging.error(f"Error updating users table schema: {e}")
    
    # Get all users with enhanced data and consent status
    try:
        cursor.execute('''
            SELECT 
                u.id, 
                u.username, 
                COALESCE(u.first_name, '') AS first_name,
                COALESCE(u.last_name, '') AS last_name,
                COALESCE(u.employee_id, '') AS employee_id,
                COALESCE(u.user_role, 'employee') AS user_role,
                COALESCE(u.department, '') AS department,
                COALESCE(u.account_status, 'active') AS account_status,
                COALESCE(u.is_admin, 0) AS is_admin,
                u.last_login,
                COALESCE(uc.consent_status, 'Unknown') AS consent_status,
                COALESCE(uc.consent_date, '') AS consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            ORDER BY u.username
        ''')
        users = cursor.fetchall()
        
        # Convert to list of dictionaries for easier template handling
        users_data = []
        for user in users:
            user_dict = dict(user)
            
            # Create display name
            if user_dict['first_name'] and user_dict['last_name']:
                user_dict['display_name'] = f"{user_dict['first_name']} {user_dict['last_name']}"
            else:
                user_dict['display_name'] = user_dict['username']
            
            # Format role display
            role_display = {
                'employee': 'Mitarbeiter',
                'supervisor': 'Vorgesetzter', 
                'hr': 'Personalwesen',
                'admin': 'Administrator'
            }
            user_dict['role_display'] = role_display.get(user_dict['user_role'], user_dict['user_role'])
            
            # Format status display
            status_display = {
                'active': 'Aktiv',
                'inactive': 'Inaktiv',
                'suspended': 'Gesperrt'
            }
            user_dict['status_display'] = status_display.get(user_dict['account_status'], user_dict['account_status'])
            
            # Format consent display
            consent_display = {
                'granted': 'Erteilt',
                'declined': 'Verweigert',
                'pending': 'Ausstehend',
                'Unknown': 'Unbekannt'
            }
            user_dict['consent_display'] = consent_display.get(user_dict['consent_status'], user_dict['consent_status'])
            
            users_data.append(user_dict)
        
        logging.info(f"Loaded {len(users_data)} users for management interface")
        
    except Exception as e:
        logging.error(f"Error fetching user data: {e}")
        users_data = []
        flash('Fehler beim Laden der Benutzerdaten', 'error')
    
    return render_template('user_management.html', users=users_data)

@app.route('/add_user', methods=['POST'])
def add_user():
    """Add a new user (admin only) with enhanced fields and system synchronization"""
    if not session.get('username'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzer hinzufügen'}), 403
        flash('Nur Administratoren können Benutzer hinzufügen', 'error')
        return redirect(url_for('index'))
    
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # Debug output
    logging.info(f"add_user called, is_ajax: {is_ajax}, headers: {request.headers}")
    
    # Get form data - both required and optional fields
    username = request.form.get('username')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    
    # New optional fields from enhanced form
    first_name = request.form.get('first_name', '').strip()
    last_name = request.form.get('last_name', '').strip()
    employee_id = request.form.get('employee_id', '').strip()
    user_role = request.form.get('user_role', 'employee')
    department = request.form.get('department', '').strip()
    privacy_consent = request.form.get('privacy_consent', 'pending')
    account_status = request.form.get('account_status', 'active')
    
    # Validate required form data
    if not username or not password or not confirm_password:
        error_msg = 'Benutzername, Passwort und Passwortbestätigung sind erforderlich'
        if is_ajax:
            return jsonify({'success': False, 'message': error_msg}), 400
        else:
            return render_template('user_management.html', error=error_msg)
            
    # Check if passwords match
    if password != confirm_password:
        error_msg = 'Die Passwörter stimmen nicht überein'
        if is_ajax:
            return jsonify({'success': False, 'message': error_msg}), 400
        else:
            return render_template('user_management.html', error=error_msg)
    
    # Validate password length
    if len(password) < 6:
        error_msg = 'Passwort muss mindestens 6 Zeichen lang sein'
        if is_ajax:
            return jsonify({'success': False, 'message': error_msg}), 400
        else:
            return render_template('user_management.html', error=error_msg)
    
    # Validate username format
    if not re.match(r'^[a-zA-Z0-9._-]+$', username):
        error_msg = 'Benutzername darf nur Buchstaben, Zahlen, Punkte, Unterstriche und Bindestriche enthalten'
        if is_ajax:
            return jsonify({'success': False, 'message': error_msg}), 400
        else:
            return render_template('user_management.html', error=error_msg)
    
    # Validate user role
    valid_roles = ['employee', 'supervisor', 'hr', 'admin']
    if user_role not in valid_roles:
        user_role = 'employee'  # Default fallback
    
    # Validate privacy consent
    valid_consent_values = ['pending', 'granted', 'declined']
    if privacy_consent not in valid_consent_values:
        privacy_consent = 'pending'  # Default fallback
    
    # Validate account status
    valid_status_values = ['active', 'inactive', 'suspended']
    if account_status not in valid_status_values:
        account_status = 'active'  # Default fallback
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Check if username already exists
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cursor.fetchone():
            conn.close()
            error_msg = f'Benutzername {username} existiert bereits'
            if is_ajax:
                return jsonify({'success': False, 'message': error_msg}), 400
            else:
                return render_template('user_management.html', error=error_msg)
        
        # Check if employee_id already exists (if provided)
        if employee_id:
            cursor.execute('SELECT id FROM users WHERE employee_id = ?', (employee_id,))
            if cursor.fetchone():
                conn.close()
                error_msg = f'Mitarbeiter-ID {employee_id} wird bereits verwendet'
                if is_ajax:
                    return jsonify({'success': False, 'message': error_msg}), 400
                else:
                    return render_template('user_management.html', error=error_msg)
        
        # Hash the password
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        
        # Determine if user should be admin based on role
        is_admin = 1 if user_role == 'admin' else 0
        
        # First, check if users table has the new columns, if not add them
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Add missing columns to users table if they don't exist
        new_columns = {
            'first_name': 'TEXT',
            'last_name': 'TEXT', 
            'employee_id': 'TEXT UNIQUE',
            'user_role': 'TEXT DEFAULT "employee"',
            'department': 'TEXT',
            'account_status': 'TEXT DEFAULT "active"',
            'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            'updated_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
        }
        
        for column_name, column_def in new_columns.items():
            if column_name not in columns:
                try:
                    cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_def}')
                    logging.info(f"Added column {column_name} to users table")
                except sqlite3.Error as e:
                    logging.warning(f"Could not add column {column_name}: {e}")
        
        # Insert new user with all available fields
        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        
        # Build dynamic insert query based on available columns
        cursor.execute("PRAGMA table_info(users)")
        available_columns = [column[1] for column in cursor.fetchall()]
        
        # Base fields that should always exist
        insert_fields = ['username', 'password', 'is_admin']
        insert_values = [username, hashed_password, is_admin]
        
        # Add optional fields if columns exist
        optional_fields = {
            'first_name': first_name,
            'last_name': last_name,
            'employee_id': employee_id if employee_id else None,
            'user_role': user_role,
            'department': department if department else None,
            'account_status': account_status,
            'created_at': current_time,
            'updated_at': current_time
        }
        
        for field, value in optional_fields.items():
            if field in available_columns:
                insert_fields.append(field)
                insert_values.append(value)
        
        # Create the insert query
        placeholders = ', '.join(['?' for _ in insert_values])
        fields_str = ', '.join(insert_fields)
        insert_query = f'INSERT INTO users ({fields_str}) VALUES ({placeholders})'
        
        cursor.execute(insert_query, insert_values)
        user_id = cursor.lastrowid
        
        logging.info(f"New user created: {username} (ID: {user_id}) with role: {user_role}")
        
        # Create user consent record
        try:
            cursor.execute('''INSERT INTO user_consents (user_id, consent_status, consent_date) 
                             VALUES (?, ?, ?)''', 
                          (user_id, privacy_consent, current_time))
            logging.info(f"Created consent record for user {username}: {privacy_consent}")
        except sqlite3.Error as e:
            logging.warning(f"Could not create consent record: {e}")
        
        # Create default user settings
        try:
            cursor.execute('''INSERT INTO user_settings 
                             (user_id, auto_break_detection_enabled, auto_break_threshold_minutes, 
                              exclude_breaks_from_billing, arbzg_breaks_enabled) 
                             VALUES (?, ?, ?, ?, ?)''', 
                          (user_id, 1, 30, 1, 1))
            logging.info(f"Created default settings for user {username}")
        except sqlite3.Error as e:
            logging.warning(f"Could not create user settings: {e}")
        
        # Store the plain text password temporarily in the database
        try:
            # Delete any existing temp password for this user first
            cursor.execute('DELETE FROM temp_passwords WHERE user_id = ?', (user_id,))
            # Insert new temp password
            cursor.execute('INSERT INTO temp_passwords (user_id, temp_password) VALUES (?, ?)', 
                          (user_id, password))
            logging.info(f"Temporary password stored for user {username} (ID: {user_id})")
        except Exception as e:
            logging.error(f"Error storing temp password: {str(e)}")
        
        # Commit all changes
        conn.commit()
        
        # ============================================================================
        # SYSTEM SYNCHRONIZATION - Trigger updates for other systems
        # ============================================================================
        
        try:
            # Log the user creation event for audit purposes
            logging.info(f"USER_CREATED: ID={user_id}, username={username}, role={user_role}, "
                        f"department={department}, created_by={session.get('username')}")
            
            # Trigger cache refresh for user lists (if caching is implemented)
            refresh_user_cache()
            
            # Notify other system components about the new user
            notify_systems_user_created(user_id, {
                'username': username,
                'first_name': first_name,
                'last_name': last_name,
                'employee_id': employee_id,
                'user_role': user_role,
                'department': department,
                'account_status': account_status,
                'privacy_consent': privacy_consent,
                'created_at': current_time
            })
            
            # Update any external systems or APIs
            sync_user_to_external_systems(user_id, username, user_role)
            
        except Exception as e:
            logging.error(f"Error during system synchronization: {str(e)}")
            # Don't fail the user creation if sync fails, just log it
        
        # Generate datasheet URL
        datasheet_url = url_for('user_datasheet', user_id=user_id)
        print_credentials_url = url_for('print_user_credentials', user_id=user_id)
        
        # Prepare success response
        success_message = f'Benutzer {username} wurde erfolgreich angelegt'
        if first_name and last_name:
            success_message = f'Benutzer {first_name} {last_name} ({username}) wurde erfolgreich angelegt'
        
        if is_ajax:
            return jsonify({
                'success': True, 
                'message': success_message,
                'user_id': user_id,
                'username': username,
                'full_name': f"{first_name} {last_name}".strip(),
                'user_role': user_role,
                'department': department,
                'account_status': account_status,
                'privacy_consent': privacy_consent,
                'datasheet_url': datasheet_url,
                'print_credentials_url': print_credentials_url,
                'temp_password': password  # Include the temporary password for immediate access
            })
        else:
            # Enhanced flash message with multiple options for non-AJAX requests
            flash(f'{success_message}. '
                  f'<div style="margin-top: 15px; text-align: center;">'
                  f'<a href="{print_credentials_url}" target="_blank" '
                  f'style="display: inline-block; background: #1976d2; color: white; padding: 12px 20px; '
                  f'text-decoration: none; border-radius: 6px; margin: 5px; font-weight: 600;">'
                  f'<i class="fas fa-print"></i> Zugangsdaten drucken</a>'
                  f'<a href="{datasheet_url}" target="_blank" '
                  f'style="display: inline-block; background: #10b981; color: white; padding: 12px 20px; '
                  f'text-decoration: none; border-radius: 6px; margin: 5px; font-weight: 600;">'
                  f'<i class="fas fa-file-alt"></i> Vollständiges Datenblatt</a>'
                  f'</div>', 'success')
            return redirect(url_for('user_management'))
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Error creating user: {str(e)}")
        error_msg = f'Fehler beim Anlegen des Benutzers: {str(e)}'
        if is_ajax:
            return jsonify({'success': False, 'message': error_msg}), 500
        else:
            return render_template('user_management.html', error=error_msg)
    finally:
        conn.close()


def refresh_user_cache():
    """Refresh any cached user data across the system"""
    try:
        # This function can be expanded to clear Redis cache, 
        # update in-memory caches, etc.
        logging.info("User cache refresh triggered")
        
        # Example: Clear any session-based caches
        # if hasattr(g, 'user_cache'):
        #     delattr(g, 'user_cache')
        
        # Example: Trigger cache invalidation in external systems
        # requests.post('http://cache-service/invalidate/users')
        
    except Exception as e:
        logging.error(f"Error refreshing user cache: {str(e)}")


def notify_systems_user_created(user_id, user_data):
    """Notify other systems/services about the new user creation"""
    try:
        # This function can be expanded to notify:
        # - Email services for welcome emails
        # - HR systems for employee onboarding
        # - Access control systems for permission setup
        # - Reporting systems for user analytics
        # - Mobile app backends
        # - Third-party integrations
        
        logging.info(f"System notification triggered for new user: {user_id}")
        
        # Example notifications:
        
        # 1. Email notification (if email service is configured)
        # send_welcome_email(user_data)
        
        # 2. HR system integration
        # sync_to_hr_system(user_data)
        
        # 3. Access control system
        # setup_user_permissions(user_id, user_data['user_role'])
        
        # 4. Audit logging
        audit_log_user_creation(user_id, user_data)
        
        # 5. Real-time notifications to admin dashboard
        # broadcast_admin_notification('user_created', user_data)
        
    except Exception as e:
        logging.error(f"Error notifying systems about user creation: {str(e)}")


def sync_user_to_external_systems(user_id, username, user_role):
    """Synchronize user data to external systems and APIs"""
    try:
        # This function handles synchronization with:
        # - External databases
        # - API endpoints
        # - Cloud services
        # - Third-party applications
        
        logging.info(f"External system sync triggered for user: {username} (ID: {user_id})")
        
        # Example synchronizations:
        
        # 1. Sync to external user management API
        # sync_to_external_api(user_id, username, user_role)
        
        # 2. Update LDAP/Active Directory
        # sync_to_ldap(username, user_role)
        
        # 3. Sync to cloud identity providers
        # sync_to_cloud_identity(user_id, username)
        
        # 4. Update backup systems
        # trigger_backup_sync()
        
        # 5. Sync to analytics platforms
        # sync_to_analytics(user_id, user_role)
        
    except Exception as e:
        logging.error(f"Error syncing user to external systems: {str(e)}")


def audit_log_user_creation(user_id, user_data):
    """Create detailed audit log entry for user creation"""
    try:
        audit_entry = {
            'event_type': 'USER_CREATED',
            'user_id': user_id,
            'username': user_data['username'],
            'created_by': session.get('username'),
            'timestamp': get_local_time().isoformat(),
            'user_data': user_data,
            'ip_address': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', '')
        }
        
        # Log to application log
        logging.info(f"AUDIT: {json.dumps(audit_entry)}")
        
        # Could also store in dedicated audit table:
        # store_audit_log(audit_entry)
        
    except Exception as e:
        logging.error(f"Error creating audit log: {str(e)}")

@app.route('/user_datasheet/<int:user_id>')
def user_datasheet(user_id):
    """Generate a printable user data sheet"""
    logging.info(f">>> Entering user_datasheet function for user_id: {user_id}")
    if not session.get('username'):
        logging.info(">>> User not logged in, redirecting")
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können Benutzerdatenblätter erstellen', 'error')
        return redirect(url_for('index'))
    
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get user data with all details
        cursor.execute('''
            SELECT u.*, 
                   CASE WHEN u.first_name IS NOT NULL AND u.last_name IS NOT NULL 
                        THEN u.first_name || ' ' || u.last_name 
                        ELSE u.username END as display_name,
                   uc.consent_status, uc.consent_date,
                   tp.temp_password
            FROM users u
            LEFT JOIN user_consents uc ON u.id = uc.user_id
            LEFT JOIN temp_passwords tp ON u.id = tp.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            flash('Benutzer nicht gefunden', 'error')
            return redirect(url_for('user_management'))
        
        # Get user statistics
        cursor.execute('''
            SELECT 
                COUNT(*) as total_days,
                SUM(CASE WHEN total_minutes > 0 THEN 1 ELSE 0 END) as worked_days,
                AVG(CASE WHEN total_minutes > 0 THEN total_minutes ELSE NULL END) as avg_minutes,
                SUM(total_minutes) as total_minutes
            FROM daily_summaries 
            WHERE user_id = ?
        ''', (user_id,))
        
        stats = cursor.fetchone()
        
        # Generate QR code for user profile
        import qrcode
        from io import BytesIO
        import base64
        
        qr_data = f"BTZ-User: {user['username']} | ID: {user['id']} | Role: {user.get('user_role', 'employee')}"
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        qr_img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        qr_img.save(buffer, format='PNG')
        qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        conn.close()
        
        return render_template('user_datasheet.html', 
                             user=user, 
                             stats=stats,
                             qr_code=qr_code_base64,
                             current_datetime=get_local_time().strftime('%d.%m.%Y %H:%M'))
    
    except Exception as e:
        logging.error(f"Error generating user datasheet: {str(e)}")
        flash(f'Fehler beim Erstellen des Datenblatts: {str(e)}', 'error')
        return redirect(url_for('user_management'))
    finally:
        conn.close()

@app.route('/print_user_credentials/<int:user_id>')
def print_user_credentials(user_id):
    """Display user credentials in a print-ready format - simplified version of datasheet"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können Benutzerdaten einsehen', 'error')
        return redirect(url_for('index'))
    
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get user data with temporary password
        cursor.execute('''
            SELECT u.*, 
                   CASE WHEN u.first_name IS NOT NULL AND u.last_name IS NOT NULL 
                        THEN u.first_name || ' ' || u.last_name 
                        ELSE u.username END as display_name,
                   tp.temp_password
            FROM users u
            LEFT JOIN temp_passwords tp ON u.id = tp.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            flash('Benutzer nicht gefunden', 'error')
            return redirect(url_for('user_management'))
        
        # If no temporary password exists, generate one for printing
        if not user['temp_password']:
            import secrets
            import string
            temp_password = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(8))
            
            # Store it in the database
            cursor.execute('DELETE FROM temp_passwords WHERE user_id = ?', (user_id,))
            cursor.execute('INSERT INTO temp_passwords (user_id, temp_password) VALUES (?, ?)', 
                          (user_id, temp_password))
            conn.commit()
            
            # Create a new user dict with the temp password
            user_dict = dict(user)
            user_dict['temp_password'] = temp_password
            
            # Convert to sqlite3.Row-like object for template compatibility
            class UserRow:
                def __init__(self, data):
                    self.data = data
                def __getitem__(self, key):
                    return self.data[key]
                def get(self, key, default=None):
                    return self.data.get(key, default)
            
            user = UserRow(user_dict)
        
        return render_template('print_credentials.html', 
                             user=user,
                             current_datetime=get_local_time().strftime('%d.%m.%Y %H:%M'))
    
    except Exception as e:
        logging.error(f"Error displaying user credentials: {str(e)}")
        flash(f'Fehler beim Anzeigen der Zugangsdaten: {str(e)}', 'error')
        return redirect(url_for('user_management'))
    finally:
        conn.close()

@app.route('/break_settings')
def break_settings():
    """Break settings page for admins"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können auf die Pauseneinstellungen zugreifen', 'error')
        return redirect(url_for('index'))
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Get system-wide settings
    cursor.execute('SELECT * FROM user_settings WHERE user_id = 0')
    settings = cursor.fetchone()
    
    return render_template('break_settings.html', settings=settings)

@app.route('/data_access')
def data_access():
    """Data access page for users"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Get user information
    cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    # Get total attendance records
    cursor.execute('SELECT COUNT(*) as count FROM attendance WHERE user_id = ?', (user_id,))
    record_count = cursor.fetchone()['count']
    
    # Get latest consent status
    cursor.execute('''
        SELECT consent_status, consent_date FROM user_consents
        WHERE user_id = ?
        ORDER BY id DESC LIMIT 1
    ''', (user_id,))
    consent_data = cursor.fetchone()
    
    # Create user data dict with consent information
    user_data = {key: user[key] for key in user.keys()}
    user_data['user_id'] = user_id
    
    if consent_data:
        user_data['consent_status'] = consent_data['consent_status']
        user_data['consent_date'] = consent_data['consent_date']
    else:
        user_data['consent_status'] = 'Nicht festgelegt'
        user_data['consent_date'] = None
    
    return render_template('data_access.html', 
                          user_data=user_data, 
                          user=user,
                          record_count=record_count,
                          user_id=user_id)

@app.route('/my_attendance')
def my_attendance():
    """View user's own attendance records with edit options"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    # Get page number for pagination
    page = request.args.get('page', 1, type=int)
    per_page = 15  # Records per page
    offset = (page - 1) * per_page
    
    # Get filter parameters
    selected_month = request.args.get('month', '')
    selected_date = request.args.get('date', '')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Base query
    query = '''
        SELECT * FROM attendance 
        WHERE user_id = ?
    '''
    params = [user_id]
    
    # Apply filters
    if selected_month:
        year, month = selected_month.split('-')
        query += " AND strftime('%Y-%m', check_in) = ?"
        params.append(f"{year}-{month}")
    elif selected_date:
        query += " AND date(check_in) = date(?)"
        params.append(selected_date)
    
    # Count total records for pagination
    count_query = f"SELECT COUNT(*) as count FROM ({query})"
    cursor.execute(count_query, params)
    total_records = cursor.fetchone()['count']
    total_pages = (total_records + per_page - 1) // per_page
    
    # Add sorting and pagination
    query += " ORDER BY check_in DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    
    # Get paginated records
    cursor.execute(query, params)
    records = cursor.fetchall()
    
    # Get available months for filter
    cursor.execute('''
        SELECT DISTINCT strftime('%Y-%m', check_in) as month
        FROM attendance
        WHERE user_id = ?
        ORDER BY month DESC
    ''', (user_id,))
    months_raw = cursor.fetchall()
    
    # Format months for display
    months_available = []
    for month in months_raw:
        month_value = month['month']
        year, month_num = month_value.split('-')
        month_name = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 
                     'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'][int(month_num) - 1]
        months_available.append(f"{year}-{month_num}")
    
    return render_template('my_attendance.html',
                          records=records,
                          months_available=months_available,
                          selected_month=selected_month,
                          selected_date=selected_date,
                          current_page=page,
                          total_pages=total_pages)

@app.route('/edit_attendance/<int:attendance_id>', methods=['GET', 'POST'])
def edit_attendance(attendance_id):
    """Edit a specific attendance record"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Check if the attendance record exists and belongs to the current user
    cursor.execute('''
        SELECT * FROM attendance
        WHERE id = ? AND user_id = ?
    ''', (attendance_id, user_id))
    
    attendance = cursor.fetchone()
    
    # If not found or not belonging to the user, redirect
    if not attendance:
        flash('Der angeforderte Arbeitszeiteintrag wurde nicht gefunden oder gehört nicht zu Ihrem Konto', 'error')
        return redirect(url_for('my_attendance'))
    
    # Handle POST request to update the record
    if request.method == 'POST':
        # Get form data
        check_in = request.form.get('check_in')
        check_out = request.form.get('check_out')
        current_password = request.form.get('current_password')
        
        # Validate form data
        if not check_in:
            flash('Check-In Zeit ist erforderlich', 'error')
            return render_template('edit_attendance.html', attendance=attendance)
        
        # Format datetime strings correctly
        check_in = check_in.replace('T', ' ') + ':00'
        
        if check_out:
            check_out = check_out.replace('T', ' ') + ':00'
            
            # Validate that check_out is after check_in
            try:
                check_in_dt = datetime.strptime(check_in, '%Y-%m-%d %H:%M:%S')
                check_out_dt = datetime.strptime(check_out, '%Y-%m-%d %H:%M:%S')
                
                if check_out_dt <= check_in_dt:
                    flash('Check-Out Zeit muss nach der Check-In Zeit liegen', 'error')
                    return render_template('edit_attendance.html', attendance=attendance)
            except ValueError:
                flash('Ungültiges Datumsformat', 'error')
                return render_template('edit_attendance.html', attendance=attendance)
        
        # Verify user password
        cursor.execute('SELECT password FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user or not bcrypt.check_password_hash(user['password'], current_password):
            flash('Falsches Passwort', 'error')
            return render_template('edit_attendance.html', attendance=attendance)
        
        try:
            # Begin transaction
            db.execute('BEGIN')
            
            # Update the attendance record
            if check_out:
                # Calculate billable minutes if check-out provided
                check_in_dt = datetime.strptime(check_in, '%Y-%m-%d %H:%M:%S')
                check_out_dt = datetime.strptime(check_out, '%Y-%m-%d %H:%M:%S')
                billable_minutes = int((check_out_dt - check_in_dt).total_seconds() / 60)
                
                # If there are breaks, we need to recalculate them
                # First, delete old auto breaks regardless of the has_auto_breaks flag
                cursor.execute('''
                    DELETE FROM breaks
                    WHERE attendance_id = ? AND is_auto_detected = 1
                ''', (attendance_id,))
                
                # Get user break settings
                cursor.execute('''
                    SELECT us.arbzg_breaks_enabled
                    FROM user_settings us
                    WHERE us.user_id = ?
                ''', (user_id,))
                
                settings = cursor.fetchone()
                
                # Use system default settings if user has none
                if not settings:
                    cursor.execute('''
                        SELECT arbzg_breaks_enabled
                        FROM user_settings
                        WHERE user_id = 0
                    ''')
                    settings = cursor.fetchone()
                
                # Process ArbZG-compliant breaks if enabled
                if settings and (settings['arbzg_breaks_enabled'] if 'arbzg_breaks_enabled' in settings else 1):
                    # Calculate total work duration in minutes without breaks
                    total_work_minutes = billable_minutes
                    
                    # Define required breaks per ArbZG
                    required_break_minutes = 0
                    break_desc = ""
                    
                    if total_work_minutes > 9 * 60:  # More than 9 hours
                        required_break_minutes = 45
                        break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 9 Stunden"
                    elif total_work_minutes > 6 * 60:  # More than 6 hours
                        required_break_minutes = 30
                        break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 6 Stunden"
                        
                        if required_break_minutes > 0:
                            # Try to place break during lunchtime (12:00-13:00) or at middle of the day
                            halfway_point = check_in_dt + (check_out_dt - check_in_dt) / 2
                            lunch_start = check_in_dt.replace(hour=12, minute=0, second=0)
                            lunch_end = check_in_dt.replace(hour=13, minute=0, second=0)
                            
                            # If lunch time is within the work period
                            if check_in_dt <= lunch_end and check_out_dt >= lunch_start:
                                break_start = max(check_in_dt, lunch_start)
                                break_end = min(check_out_dt, break_start + timedelta(minutes=required_break_minutes))
                                
                                # Make sure break doesn't exceed lunch period
                                if break_end > lunch_end:
                                    break_end = lunch_end
                            else:
                                # Place break at middle of the day
                                break_start = halfway_point - timedelta(minutes=required_break_minutes / 2)
                                break_end = break_start + timedelta(minutes=required_break_minutes)
                                
                                # Ensure break is within working hours
                                if break_start < check_in_dt:
                                    break_start = check_in_dt
                                    break_end = min(check_out_dt, break_start + timedelta(minutes=required_break_minutes))
                                
                                if break_end > check_out_dt:
                                    break_end = check_out_dt
                                    break_start = max(check_in_dt, break_end - timedelta(minutes=required_break_minutes))
                            
                            # Format for database
                            break_start_str = break_start.strftime('%Y-%m-%d %H:%M:%S')
                            break_end_str = break_end.strftime('%Y-%m-%d %H:%M:%S')
                            
                            # Add the break
                            cursor.execute("""
                                INSERT INTO breaks (attendance_id, start_time, end_time, duration_minutes,
                                                is_excluded_from_billing, is_auto_detected, description)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (attendance_id, break_start_str, break_end_str, required_break_minutes,
                                1, 1, break_desc))
                            
                            # Adjust billable minutes
                            billable_minutes -= required_break_minutes
                            
                            # Set has_auto_breaks flag to true
                            cursor.execute('''
                                UPDATE attendance
                                SET has_auto_breaks = 1
                                WHERE id = ?
                            ''', (attendance_id,))
                
                # Update attendance record with check-out and billable minutes
                cursor.execute('''
                    UPDATE attendance
                    SET check_in = ?, check_out = ?, billable_minutes = ?
                    WHERE id = ?
                ''', (check_in, check_out, billable_minutes, attendance_id))
            else:
                # Update only the check-in time if no check-out provided
                cursor.execute('''
                    UPDATE attendance
                    SET check_in = ?
                    WHERE id = ?
                ''', (check_in, attendance_id))
            
            # Commit transaction
            db.commit()
            
            flash('Arbeitszeiteintrag wurde erfolgreich aktualisiert', 'success')
            return redirect(url_for('my_attendance'))
            
        except Exception as e:
            db.rollback()
            logging.error(f"Error updating attendance record: {str(e)}")
            flash(f'Fehler beim Aktualisieren des Eintrags: {str(e)}', 'error')
            return render_template('edit_attendance.html', attendance=attendance)
    
    # For GET request, get breaks and render the edit form
    # Get breaks for this attendance
    cursor.execute('''
        SELECT id, start_time, end_time, duration_minutes, 
            is_excluded_from_billing, 
            is_auto_detected, 
            description
        FROM breaks
        WHERE attendance_id = ?
        ORDER BY start_time
    ''', (attendance_id,))
    
    breaks = []
    for row in cursor.fetchall():
        break_type = "auto" if row['is_auto_detected'] else "manual"
        if row['is_auto_detected'] and row['description'] and 'ArbZG' in row['description']:
            break_type = 'arbzg'
            
        breaks.append({
            'id': row['id'],
            'start_time': row['start_time'].replace('T', ' ')[:16] if row['start_time'] else '',
            'end_time': row['end_time'].replace('T', ' ')[:16] if row['end_time'] else '',
            'duration_minutes': row['duration_minutes'],
            'is_excluded': bool(row['is_excluded_from_billing']),
            'is_auto': bool(row['is_auto_detected']),
            'break_type': break_type,
            'description': row['description'] or ''
        })
    
    return render_template('edit_attendance.html', attendance=attendance, breaks=breaks)

@app.route('/delete_attendance/<int:attendance_id>', methods=['POST'])
def delete_attendance(attendance_id):
    """Delete a specific attendance record"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Check if the attendance record exists and belongs to the current user
    cursor.execute('''
        SELECT id FROM attendance
        WHERE id = ? AND user_id = ?
    ''', (attendance_id, user_id))
    
    attendance = cursor.fetchone()
    
    # If not found or not belonging to the user, redirect
    if not attendance:
        flash('Der angeforderte Arbeitszeiteintrag wurde nicht gefunden oder gehört nicht zu Ihrem Konto', 'error')
        return redirect(url_for('my_attendance'))
    
    # Verify user password
    current_password = request.form.get('current_password')
    if not current_password:
        flash('Passwort ist erforderlich', 'error')
        return redirect(url_for('edit_attendance', attendance_id=attendance_id))
    
    cursor.execute('SELECT password FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    
    if not user or not bcrypt.check_password_hash(user['password'], current_password):
        flash('Falsches Passwort', 'error')
        return redirect(url_for('edit_attendance', attendance_id=attendance_id))
    
    try:
        # Begin transaction
        db.execute('BEGIN')
        
        # First delete all breaks associated with this attendance
        cursor.execute('DELETE FROM breaks WHERE attendance_id = ?', (attendance_id,))
        
        # Then delete the attendance record itself
        cursor.execute('DELETE FROM attendance WHERE id = ?', (attendance_id,))
        
        # Commit transaction
        db.commit()
        
        flash('Arbeitszeiteintrag wurde erfolgreich gelöscht', 'success')
        return redirect(url_for('my_attendance'))
        
    except Exception as e:
        db.rollback()
        logging.error(f"Error deleting attendance record: {str(e)}")
        flash(f'Fehler beim Löschen des Eintrags: {str(e)}', 'error')
        return redirect(url_for('edit_attendance', attendance_id=attendance_id))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzer löschen'}), 403
    
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Prüfen, ob der Benutzer existiert
        cursor.execute('SELECT id FROM users WHERE id = ?', (user_id,))
        if not cursor.fetchone():
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
            
        # Temporäre Passwörter löschen
        cursor.execute('DELETE FROM temp_passwords WHERE user_id = ?', (user_id,))
        
        # Benutzer löschen
        cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
        
        # Alle abhängigen Daten löschen
        cursor.execute('DELETE FROM user_consents WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM attendance WHERE user_id = ?', (user_id,))
        cursor.execute('DELETE FROM user_settings WHERE user_id = ?', (user_id,))
        
        db.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Benutzer wurde erfolgreich gelöscht'
        })
    
    except Exception as e:
        logging.error(f"Error deleting user: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/request_data_deletion', methods=['POST'])
def request_data_deletion():
    """Process user's request for data deletion"""
    if not session.get('username'):
        flash('Sie müssen angemeldet sein, um eine Datenlöschung zu beantragen.', 'error')
        return redirect(url_for('login'))
    
    # Get form data
    username = request.form.get('username')
    password = request.form.get('password')
    reason = request.form.get('reason') or 'Keine Begründung angegeben'
    confirm_deletion = request.form.get('confirm_deletion')
    
    # Basic validation
    if not username or not password:
        flash('Benutzername und Passwort sind erforderlich.', 'error')
        return redirect(url_for('data_access'))
    
    if not confirm_deletion:
        flash('Bitte bestätigen Sie die Löschungsanfrage.', 'error')
        return redirect(url_for('data_access'))
    
    # Connect to database
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Check if username exists
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    
    if not user:
        flash('Benutzername nicht gefunden.', 'error')
        return redirect(url_for('data_access'))
    
    # Check if passwords match
    if not bcrypt.check_password_hash(user['password'], password):
        flash('Falsches Passwort.', 'error')
        return redirect(url_for('data_access'))
    
    # Verify if user is requesting deletion for their own account
    if user['id'] != session.get('user_id'):
        flash('Sie können nur die Löschung Ihres eigenen Benutzerkontos beantragen.', 'error')
        return redirect(url_for('data_access'))
    
    # Check if deletion request already exists
    cursor.execute('SELECT * FROM deletion_requests WHERE user_id = ? AND status IN ("pending", "processing")', 
                   (user['id'],))
    existing_request = cursor.fetchone()
    
    if existing_request:
        flash('Sie haben bereits eine Datenlöschungsanfrage gestellt. Bitte warten Sie auf die Bearbeitung.', 'info')
        return redirect(url_for('data_access'))
    
    try:
        # Create the deletion request
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute('''
            INSERT INTO deletion_requests 
            (user_id, request_date, reason, status)
            VALUES (?, ?, ?, ?)
        ''', (user['id'], current_time, reason, 'pending'))
        
        db.commit()
        logging.info(f"Data deletion request created for user {username} (ID: {user['id']})")
        
        flash('Ihre Datenlöschungsanfrage wurde erfolgreich eingereicht und wird in Kürze bearbeitet.', 'success')
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating deletion request: {str(e)}")
        flash(f'Fehler bei der Erstellung der Löschungsanfrage: {str(e)}', 'error')
    
    return redirect(url_for('data_access'))

@app.route('/privacy_policy')
def privacy_policy():
    """Privacy policy page"""
    return render_template('privacy_policy.html')

@app.route('/manual_attendance')
def manual_attendance():
    """Manual attendance entry page"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    username = session.get('username')
    
    # If admin, get all users for selection dropdown
    users = []
    if session.get('admin_logged_in'):
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT id, username FROM users ORDER BY username')
        users = cursor.fetchall()
        conn.close()
    
    return render_template('manual_attendance.html', 
                          user_id=user_id, 
                          username=username,
                          users=users)

@app.route('/add_manual_attendance', methods=['POST'])
def add_manual_attendance():
    """Handle manual attendance record submissions"""
    if not session.get('username'):
        flash('Sie müssen angemeldet sein, um Anwesenheitsaufzeichnungen hinzuzufügen', 'error')
        return redirect(url_for('login'))
    
    # Get form data
    user_id = request.form.get('user_id')
    date = request.form.get('date')
    check_in_time = request.form.get('check_in')
    check_out_time = request.form.get('check_out')
    current_password = request.form.get('current_password')
    
    # Validate inputs
    if not user_id or not date or not check_in_time or not current_password:
        flash('Alle Pflichtfelder müssen ausgefüllt werden', 'error')
        return redirect(url_for('manual_attendance'))
    
    # Only admin can add records for other users
    if str(user_id) != str(session.get('user_id')) and not session.get('admin_logged_in'):
        flash('Sie können nur Aufzeichnungen für sich selbst hinzufügen', 'error')
        return redirect(url_for('manual_attendance'))
    
    # Connect to database with row factory for easier access
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Verify password
    cursor.execute('SELECT password FROM users WHERE id = ?', (session.get('user_id'),))
    user = cursor.fetchone()
    
    if not user or not bcrypt.check_password_hash(user['password'], current_password):
        conn.close()
        flash('Falsches Passwort', 'error')
        return redirect(url_for('manual_attendance'))
    
    # Format datetime strings
    full_date = date
    
    # Create full datetime strings
    check_in_datetime = f"{full_date} {check_in_time}:00"
    check_out_datetime = f"{full_date} {check_out_time}:00" if check_out_time else None
    
    # Validate dates
    now = datetime.now()
    check_in_dt = datetime.strptime(check_in_datetime, '%Y-%m-%d %H:%M:%S')
    
    if check_in_dt > now:
        conn.close()
        flash('Check-In Zeit kann nicht in der Zukunft liegen', 'error')
        return redirect(url_for('manual_attendance'))
    
    if check_out_datetime:
        check_out_dt = datetime.strptime(check_out_datetime, '%Y-%m-%d %H:%M:%S')
        if check_out_dt > now:
            conn.close()
            flash('Check-Out Zeit kann nicht in der Zukunft liegen', 'error')
            return redirect(url_for('manual_attendance'))
        
        if check_out_dt <= check_in_dt:
            conn.close()
            flash('Check-Out Zeit muss nach der Check-In Zeit liegen', 'error')
            return redirect(url_for('manual_attendance'))
    
    # Calculate billable minutes if checkout time is provided
    billable_minutes = None
    has_auto_breaks = False
    
    if check_out_datetime:
        check_out_dt = datetime.strptime(check_out_datetime, '%Y-%m-%d %H:%M:%S')
        duration_minutes = int((check_out_dt - check_in_dt).total_seconds() / 60)
        billable_minutes = duration_minutes
    
    # Insert new attendance record
    try:
        # Begin transaction
        conn.execute('BEGIN TRANSACTION')
        
        if check_out_datetime:
            cursor.execute('''
                INSERT INTO attendance (user_id, check_in, check_out, billable_minutes, has_auto_breaks)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, check_in_datetime, check_out_datetime, billable_minutes, has_auto_breaks))
        else:
            cursor.execute('''
                INSERT INTO attendance (user_id, check_in, has_auto_breaks)
                VALUES (?, ?, ?)
            ''', (user_id, check_in_datetime, has_auto_breaks))
        
        attendance_id = cursor.lastrowid
        
        # Process ArbZG-compliant breaks if both check-in and check-out are provided
        if check_out_datetime:
            # Get user break settings
            cursor.execute('''
                SELECT us.arbzg_breaks_enabled
                FROM user_settings us
                WHERE us.user_id = ?
            ''', (user_id,))
            
            settings = cursor.fetchone()
            
            # Use system default settings if user has none
            if not settings:
                cursor.execute('''
                    SELECT arbzg_breaks_enabled
                    FROM user_settings
                    WHERE user_id = 0
                ''')
                settings = cursor.fetchone()
            
            # Process ArbZG-compliant breaks if enabled
            if settings and (settings['arbzg_breaks_enabled'] if 'arbzg_breaks_enabled' in settings else 1):
                # Calculate total work duration in minutes
                total_work_minutes = billable_minutes
                
                # Define required breaks per ArbZG
                required_break_minutes = 0
                break_desc = ""
                
                if total_work_minutes > 9 * 60:  # More than 9 hours
                    required_break_minutes = 45
                    break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 9 Stunden"
                elif total_work_minutes > 6 * 60:  # More than 6 hours
                    required_break_minutes = 30
                    break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 6 Stunden"
                
                if required_break_minutes > 0:
                    # Try to place break during lunchtime (12:00-13:00) or at middle of the day
                    halfway_point = check_in_dt + (check_out_dt - check_in_dt) / 2
                    lunch_start = check_in_dt.replace(hour=12, minute=0, second=0)
                    lunch_end = check_in_dt.replace(hour=13, minute=0, second=0)
                    
                    # If lunch time is within the work period
                    if check_in_dt <= lunch_end and check_out_dt >= lunch_start:
                        break_start = max(check_in_dt, lunch_start)
                        break_end = min(check_out_dt, break_start + timedelta(minutes=required_break_minutes))
                        
                        # Make sure break doesn't exceed lunch period
                        if break_end > lunch_end:
                            break_end = lunch_end
                    else:
                        # Place break at middle of the day
                        break_start = halfway_point - timedelta(minutes=required_break_minutes / 2)
                        break_end = break_start + timedelta(minutes=required_break_minutes)
                        
                        # Ensure break is within working hours
                        if break_start < check_in_dt:
                            break_start = check_in_dt
                            break_end = min(check_out_dt, break_start + timedelta(minutes=required_break_minutes))
                        
                        if break_end > check_out_dt:
                            break_end = check_out_dt
                            break_start = max(check_in_dt, break_end - timedelta(minutes=required_break_minutes))
                    
                    # Format for database
                    break_start_str = break_start.strftime('%Y-%m-%d %H:%M:%S')
                    break_end_str = break_end.strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Add the break
                    cursor.execute("""
                        INSERT INTO breaks (attendance_id, start_time, end_time, duration_minutes,
                                        is_excluded_from_billing, is_auto_detected, description)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (attendance_id, break_start_str, break_end_str, required_break_minutes,
                        1, 1, break_desc))
                    
                    # Adjust billable minutes
                    billable_minutes -= required_break_minutes
                    has_auto_breaks = True
                    
                    # Update the attendance record with adjusted billable minutes and auto breaks flag
                    cursor.execute('''
                        UPDATE attendance
                        SET billable_minutes = ?, has_auto_breaks = ?
                        WHERE id = ?
                    ''', (billable_minutes, has_auto_breaks, attendance_id))
        
        # Commit transaction
        conn.commit()
        conn.close()
        
        # Format the work time for display (hours:minutes) if applicable
        if check_out_datetime and billable_minutes is not None:
            hours = billable_minutes // 60
            minutes = billable_minutes % 60
            work_time = f"{hours}:{minutes:02d}"
            flash(f'Anwesenheitsaufzeichnung erfolgreich hinzugefügt. Arbeitszeit: {work_time} Stunden', 'success')
        else:
            flash('Anwesenheitsaufzeichnung erfolgreich hinzugefügt', 'success')
        
        return redirect(url_for('index'))
    
    except sqlite3.Error as e:
        conn.rollback()
        conn.close()
        flash(f'Fehler beim Hinzufügen der Aufzeichnung: {str(e)}', 'error')
        return redirect(url_for('manual_attendance'))

@app.route('/user_break_preferences')
def user_break_preferences():
    """User break preferences page"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    # Get user settings
    cursor.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,))
    settings = cursor.fetchone()
    
    # If no settings exist yet, create default settings
    if not settings:
        # Get system defaults
        cursor.execute('SELECT * FROM user_settings WHERE user_id = 0')
        system_settings = cursor.fetchone()
        
        if system_settings:
            # Use system defaults
            cursor.execute('''
                INSERT INTO user_settings 
                (user_id, auto_break_detection_enabled, auto_break_threshold_minutes, 
                exclude_breaks_from_billing, arbzg_breaks_enabled)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, 
                system_settings['auto_break_detection_enabled'],
                system_settings['auto_break_threshold_minutes'],
                system_settings['exclude_breaks_from_billing'],
                system_settings['arbzg_breaks_enabled']))
        else:
            # Use hardcoded defaults
            cursor.execute('''
                INSERT INTO user_settings 
                (user_id, auto_break_detection_enabled, auto_break_threshold_minutes, 
                exclude_breaks_from_billing, arbzg_breaks_enabled)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, 1, 30, 1, 1))
        
        db.commit()
        
        # Get the newly created settings
        cursor.execute('SELECT * FROM user_settings WHERE user_id = ?', (user_id,))
        settings = cursor.fetchone()
    
    return render_template('user_break_preferences.html', settings=settings)

@app.route('/get_attendance_status')
def get_attendance_status():
    """API endpoint to check if a user is checked in or out"""
    if not session.get('username'):
        return jsonify({'error': 'Not logged in'}), 401
    
    # Get user ID from query parameter
    user_id = request.args.get('user_id')
    
    # Admin users can check status for other users
    if session.get('admin_logged_in') is not True and str(session.get('user_id')) != str(user_id):
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get current date in the application's timezone
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
    
    # Check for active check-in (no check_out time)
    cursor.execute('''
        SELECT * FROM attendance
        WHERE user_id = ? AND date(check_in) = ?
        AND check_out IS NULL
        ORDER BY id DESC LIMIT 1
    ''', (user_id, today))
    
    active_record = cursor.fetchone()
    
    # Also get the most recent completed record for today
    cursor.execute('''
        SELECT * FROM attendance
        WHERE user_id = ? AND date(check_in) = ?
        AND check_out IS NOT NULL
        ORDER BY id DESC LIMIT 1
    ''', (user_id, today))
    
    completed_record = cursor.fetchone()
    
    result = {
        'is_checked_in': False,
        'is_checked_out': False,
        'check_in_time': None,
        'check_out_time': None
    }
    
    if active_record:
        # User is currently checked in (active session)
        result['is_checked_in'] = True
        result['check_in_time'] = active_record['check_in']
        result['is_checked_out'] = False
    elif completed_record:
        # User has completed a session today but is not currently checked in
        result['is_checked_in'] = False
        result['is_checked_out'] = True
        result['check_in_time'] = completed_record['check_in']
        result['check_out_time'] = completed_record['check_out']
    
    conn.close()
    return jsonify(result)

@app.route('/checkin', methods=['POST'])
def checkin():
    """Handle check-in requests"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Get user ID from form or session
    user_id = request.form.get('user_id') or session.get('user_id')
    
    # Admin users can check in for other users
    if session.get('admin_logged_in') is not True and str(session.get('user_id')) != str(user_id):
        flash('You can only check in for yourself', 'error')
        return redirect(url_for('index'))
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Check if already checked in
    check_in_time = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    today = check_in_time.split('T')[0]
    
    cursor.execute('''
        SELECT * FROM attendance
        WHERE user_id = ? AND date(check_in) = ?
        AND check_out IS NULL
    ''', (user_id, today))
    
    existing_check_in = cursor.fetchone()
    
    if existing_check_in:
        conn.close()
        flash('You are already checked in', 'error')
        return redirect(url_for('index'))
    
    # Insert check-in record
    cursor.execute('''
        INSERT INTO attendance (user_id, check_in, has_auto_breaks)
                VALUES (?, ?, ?)
    ''', (user_id, check_in_time, True))
    
    conn.commit()
    conn.close()
    
    flash('Check-in successful', 'success')
    return redirect(url_for('index'))

@app.route('/checkout', methods=['POST'])
def checkout():
    """Handle check-out requests"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Get user ID from form or session
    user_id = request.form.get('user_id') or session.get('user_id')
    
    # Admin users can check out for other users
    if session.get('admin_logged_in') is not True and str(session.get('user_id')) != str(user_id):
        flash('You can only check out for yourself', 'error')
        return redirect(url_for('index'))
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Find the active check-in record
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d')
    
    cursor.execute('''
        SELECT id FROM attendance
        WHERE user_id = ? AND date(check_in) = ?
        AND check_out IS NULL
        ORDER BY check_in DESC LIMIT 1
    ''', (user_id, today))
    
    active_record = cursor.fetchone()
    
    if not active_record:
        conn.close()
        flash('No active check-in found', 'error')
        return redirect(url_for('index'))
    
    # Update with check-out time
    check_out_time = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    attendance_id = active_record['id']
    
    # Get check-in time to calculate billable minutes
    cursor.execute('SELECT check_in FROM attendance WHERE id = ?', (attendance_id,))
    check_in_result = cursor.fetchone()
    
    check_in_time = try_parse(check_in_result['check_in'])
    check_out_dt = try_parse(check_out_time)
    
    # Calculate billable minutes (excluding breaks)
    billable_minutes = int((check_out_dt - check_in_time).total_seconds() / 60)
    
    # Get breaks for this attendance record
    cursor.execute('''
        SELECT duration_minutes, is_excluded_from_billing FROM breaks
        WHERE attendance_id = ?
    ''', (attendance_id,))
    
    breaks = cursor.fetchall()
    
    # Subtract break times from billable minutes if they are excluded from billing
    # Log the breaks for debugging purposes
    print(f"Processing {len(breaks)} breaks for attendance ID {attendance_id}")
    total_break_minutes = 0
    for break_record in breaks:
        excluded = break_record['is_excluded_from_billing']
        duration = break_record['duration_minutes']
        print(f"Break: {duration} minutes, excluded from billing: {excluded}")
        
        # Check if break should be excluded from billing (is_excluded_from_billing = 1)
        if excluded == 1:
            total_break_minutes += duration
            billable_minutes -= duration
            print(f"Subtracted {duration} minutes, new billable minutes: {billable_minutes}")
    
    print(f"Total break minutes subtracted initially: {total_break_minutes}")
    
    # Update the attendance record
    cursor.execute('''
        UPDATE attendance
        SET check_out = ?, billable_minutes = ?
        WHERE id = ?
    ''', (check_out_time, billable_minutes, attendance_id))
    
    conn.commit()
    
    # Process automatic breaks if enabled
    cursor.execute('''
        SELECT us.auto_break_detection_enabled, us.auto_break_threshold_minutes,
               us.exclude_breaks_from_billing
        FROM user_settings us
        WHERE us.user_id = ?
    ''', (user_id,))
    
    settings = cursor.fetchone()
    
    if not settings:
        # Use system default settings
        cursor.execute('''
            SELECT auto_break_detection_enabled, auto_break_threshold_minutes,
                   exclude_breaks_from_billing
            FROM user_settings
            WHERE user_id = 0
        ''')
        settings = cursor.fetchone()
    
    # Process auto breaks if enabled
    if settings and settings['auto_break_detection_enabled']:
        threshold_minutes = settings['auto_break_threshold_minutes']
        exclude_from_billing = settings['exclude_breaks_from_billing']
        
        # Auto break detection logic - not implemented here
        
    # Process ArbZG-compliant breaks
    print("Checking ArbZG break requirements...")
    if settings and (settings['arbzg_breaks_enabled'] if 'arbzg_breaks_enabled' in settings else 1):
        # Calculate total work duration in minutes
        total_work_minutes = int((check_out_dt - check_in_time).total_seconds() / 60)
        print(f"Total work minutes: {total_work_minutes}")
        
        # Define required breaks per ArbZG
        required_break_minutes = 0
        if total_work_minutes > 9 * 60:  # More than 9 hours
            required_break_minutes = 45
            break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 9 Stunden"
            print(f"Required break: 45 minutes (>9 hours)")
        elif total_work_minutes > 6 * 60:  # More than 6 hours
            required_break_minutes = 30
            break_desc = "Gesetzliche Pause (ArbZG §4) für Arbeitszeit über 6 Stunden"
            print(f"Required break: 30 minutes (>6 hours)")
        else:
            print(f"No break required (work time < 6 hours)")
        
        if required_break_minutes > 0:
            # Check existing breaks
            cursor.execute("""
                SELECT SUM(duration_minutes) 
                FROM breaks 
                WHERE attendance_id = ?
            """, (attendance_id,))
            
            existing_breaks_row = cursor.fetchone()
            existing_break_minutes = existing_breaks_row[0] if existing_breaks_row and existing_breaks_row[0] is not None else 0
            print(f"Existing break minutes: {existing_break_minutes}")
            
            # Calculate missing break minutes
            missing_break_minutes = required_break_minutes - existing_break_minutes
            print(f"Missing break minutes: {missing_break_minutes}")
            
            # If breaks are missing, add them
            if missing_break_minutes > 0:
                # Try to place break during lunchtime (12:00-13:00) or at end of day
                lunch_start = check_in_time.replace(hour=12, minute=0, second=0)
                lunch_end = check_in_time.replace(hour=13, minute=0, second=0)
                
                # If lunch time is within the work period
                if check_in_time <= lunch_end and check_out_dt >= lunch_start:
                    break_start = max(check_in_time, lunch_start)
                    # Fix: lunch_start.replace() doesn't add minutes but sets them to a value
                    break_end = min(check_out_dt, break_start + timedelta(minutes=missing_break_minutes))
                    
                    # Make sure break doesn't exceed lunch period
                    if break_end > lunch_end:
                        break_end = lunch_end
                else:
                    # Place break at end of workday
                    break_end = check_out_dt
                    break_start = break_end - timedelta(minutes=missing_break_minutes)
                
                # Format for database
                break_start_str = break_start.isoformat()
                break_end_str = break_end.isoformat()
                
                # Add the break
                cursor.execute("""
                    INSERT INTO breaks (attendance_id, start_time, end_time, duration_minutes,
                                    is_excluded_from_billing, is_auto_detected, description)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (attendance_id, break_start_str, break_end_str, missing_break_minutes,
                    1, 1, break_desc))
                    
                conn.commit()
                
                # Recalculate billable minutes after adding the break
                billable_minutes -= missing_break_minutes
                print(f"Added ArbZG break: {missing_break_minutes} minutes, new billable time: {billable_minutes} minutes")
                
                # Update the attendance record with the new billable minutes and mark as having auto breaks
                cursor.execute('''
                    UPDATE attendance
                    SET billable_minutes = ?, has_auto_breaks = 1
                    WHERE id = ?
                ''', (billable_minutes, attendance_id))
                conn.commit()
                
                print(f"Updated database record: attendance ID {attendance_id} now has {billable_minutes} billable minutes and has_auto_breaks=1")
    
    # Format the work time for display (hours:minutes)
    hours = billable_minutes // 60
    minutes = billable_minutes % 60
    work_time = f"{hours}:{minutes:02d}"
    
    conn.close()
    flash(f'Check-out erfolgreich. Gesamtarbeitszeit: {work_time} Stunden', 'success')
    return redirect(url_for('index'))

@app.route('/update_user_break_preferences', methods=['POST'])
def update_user_break_preferences():
    """Update user's break preferences"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    
    # Get form data
    auto_break_detection = request.form.get('auto_break_detection') == 'on'
    auto_break_threshold = int(request.form.get('auto_break_threshold', 30))
    exclude_breaks = request.form.get('exclude_breaks') == 'on'
    arbzg_breaks_enabled = request.form.get('arbzg_breaks_enabled') == 'on'
    
    # Get lunch period settings if available
    lunch_settings = {}
    if 'lunch_period_start_hour' in request.form:
        lunch_settings = {
            'lunch_period_start_hour': int(request.form.get('lunch_period_start_hour', 11)),
            'lunch_period_start_minute': int(request.form.get('lunch_period_start_minute', 30)),
            'lunch_period_end_hour': int(request.form.get('lunch_period_end_hour', 14)),
            'lunch_period_end_minute': int(request.form.get('lunch_period_end_minute', 0))
        }
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Check if user has settings
    cursor.execute('SELECT id FROM user_settings WHERE user_id = ?', (user_id,))
    if cursor.fetchone():
        # Update existing settings
        query = '''
            UPDATE user_settings SET
            auto_break_detection_enabled = ?,
            auto_break_threshold_minutes = ?,
            exclude_breaks_from_billing = ?,
            arbzg_breaks_enabled = ?
        '''
        params = [auto_break_detection, auto_break_threshold, exclude_breaks, arbzg_breaks_enabled]
        
        # Add lunch period settings if available
        if lunch_settings:
            query += ''',
                lunch_period_start_hour = ?,
                lunch_period_start_minute = ?,
                lunch_period_end_hour = ?,
                lunch_period_end_minute = ?
            '''
            params.extend([
                lunch_settings['lunch_period_start_hour'],
                lunch_settings['lunch_period_start_minute'],
                lunch_settings['lunch_period_end_hour'],
                lunch_settings['lunch_period_end_minute']
            ])
        
        query += ' WHERE user_id = ?'
        params.append(user_id)
        
        cursor.execute(query, params)
    else:
        # Create new settings
        query = '''
            INSERT INTO user_settings (
                user_id, auto_break_detection_enabled, auto_break_threshold_minutes,
                exclude_breaks_from_billing, arbzg_breaks_enabled
        '''
        params = [user_id, auto_break_detection, auto_break_threshold, exclude_breaks, arbzg_breaks_enabled]
        
        # Add lunch period settings if available
        if lunch_settings:
            query += ''',
                lunch_period_start_hour, lunch_period_start_minute,
                lunch_period_end_hour, lunch_period_end_minute
            '''
            params.extend([
                lunch_settings['lunch_period_start_hour'],
                lunch_settings['lunch_period_start_minute'],
                lunch_settings['lunch_period_end_hour'],
                lunch_settings['lunch_period_end_minute']
            ])
        
        query += ') VALUES (' + ', '.join(['?' for _ in params]) + ')'
        
        cursor.execute(query, params)
    
    conn.commit()
    conn.close()
    
    flash('Pauseneinstellungen wurden aktualisiert', 'success')
    return redirect(url_for('user_break_preferences'))

@app.route('/update_user_settings', methods=['POST'])
def update_user_settings():
    """Update system-wide user settings (admin only)"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können Systemeinstellungen ändern', 'error')
        return redirect(url_for('index'))
    
    # Get form data
    auto_break_detection = request.form.get('auto_break_detection') == 'on'
    auto_break_threshold = int(request.form.get('auto_break_threshold', 30))
    exclude_breaks = request.form.get('exclude_breaks') == 'on'
    arbzg_breaks_enabled = request.form.get('arbzg_breaks_enabled') == 'on'
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Update system settings (user_id = 0)
    cursor.execute('''
        UPDATE user_settings SET
        auto_break_detection_enabled = ?,
        auto_break_threshold_minutes = ?,
        exclude_breaks_from_billing = ?,
        arbzg_breaks_enabled = ?
        WHERE user_id = 0
    ''', (auto_break_detection, auto_break_threshold, exclude_breaks, arbzg_breaks_enabled))
    
    conn.commit()
    conn.close()
    
    flash('Systemeinstellungen wurden aktualisiert', 'success')
    return redirect(url_for('break_settings'))

@app.route('/reset_password/<int:user_id>', methods=['POST'])
def reset_password(user_id):
    """Reset a user's password (admin only)"""
    if not session.get('username'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Nur Administratoren können Passwörter zurücksetzen'}), 403
        flash('Nur Administratoren können Passwörter zurücksetzen', 'error')
        return redirect(url_for('index'))
    
    # Check if JSON request (from modal dialog)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if is_ajax and request.is_json:
        data = request.get_json()
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        # Verify both passwords match
        if new_password != confirm_password:
            return jsonify({'success': False, 'message': 'Die Passwörter stimmen nicht überein'}), 400
    else:
        # Handle legacy form submission
        new_password = request.form.get('new_password')
    
    # Validate password length
    if not new_password or len(new_password) < 4:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Passwort muss mindestens 4 Zeichen lang sein'}), 400
        flash('Passwort muss mindestens 4 Zeichen lang sein', 'error')
        return redirect(url_for('user_management'))
    
    # Hash the new password
    hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Update the user's password
    cursor.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
    
    # Get username for feedback
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    username = user[0] if user else 'Unknown'
    
    conn.commit()
    conn.close()
    
    success_message = f'Passwort für {username} wurde erfolgreich geändert'
    
    # Store the temporary password in encrypted format for datasheet generation
    # Use the same encryption method but with a different salt to allow decryption
    try:
        # We need to reconnect to the database here since we closed it above
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row  # Set row factory for consistent behavior
        cursor = conn.cursor()
        
        # Generate a secure random key for this password that we can use to verify
        import base64
        import hashlib
        
        # Create a verification hash - will be used to verify this is the correct password
        verification_hash = hashlib.sha256(new_password.encode()).hexdigest()
        
        # Update the user's plain password and verification hash
        cursor.execute('''
            UPDATE users 
            SET plain_password = ?,
                verification_hash = ?,
                last_password_change = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (hashed_password, verification_hash, user_id))
        conn.commit()
        conn.close()
        logging.info(f"Encrypted temporary password stored in database for password reset, user ID: {user_id}")
    except Exception as e:
        logging.error(f"Error storing encrypted temp password for reset: {str(e)}")
        if 'conn' in locals() and conn:
            conn.close()
    
    # Generate datasheet URL
    datasheet_url = url_for('user_datasheet', user_id=user_id)
    
    if is_ajax:
        return jsonify({
            'success': True, 
            'message': success_message,
            'datasheet_url': datasheet_url
        })
    
    flash(success_message, 'success')
    return redirect(url_for('user_management'))

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    """Allow users to change their own password"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # If it's a POST request, process the form data
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Verify both new passwords match
        if new_password != confirm_password:
            flash('Die neuen Passwörter stimmen nicht überein', 'error')
            return render_template('change_password.html')
        
        # Validate password length
        if not new_password or len(new_password) < 4:
            flash('Das neue Passwort muss mindestens 4 Zeichen lang sein', 'error')
            return render_template('change_password.html')
        
        # Connect to database
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get current user's data
        user_id = session.get('user_id')
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            flash('Benutzer nicht gefunden', 'error')
            return render_template('change_password.html')
        
        # Verify current password
        if not bcrypt.check_password_hash(user['password'], current_password):
            conn.close()
            flash('Das aktuelle Passwort ist nicht korrekt', 'error')
            return render_template('change_password.html')
        
        # Hash the new password
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        
        # Update the password
        cursor.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
        conn.commit()
        conn.close()
        
        # Store the temporary password in encrypted format for password print button
        try:
            # Reconnect to the database
            conn = sqlite3.connect(DATABASE)
            conn.row_factory = sqlite3.Row  # Set row factory for consistent behavior
            cursor = conn.cursor()
            
            # Generate a verification hash for the password
            import hashlib
            verification_hash = hashlib.sha256(new_password.encode()).hexdigest()
            
            # Update the user's plain password and verification hash
            cursor.execute('''
                UPDATE users 
                SET plain_password = ?,
                    verification_hash = ?,
                    last_password_change = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (hashed_password, verification_hash, user_id))
            conn.commit()
            conn.close()
            logging.info(f"Encrypted temporary password stored in database after user-initiated password change, user ID: {user_id}")
        except Exception as e:
            logging.error(f"Error storing encrypted temp password after password change: {str(e)}")
            if 'conn' in locals() and conn:
                conn.close()
        
        flash('Ihr Passwort wurde erfolgreich geändert', 'success')
        return redirect(url_for('index'))
    
    # For GET requests, just show the form
    return render_template('change_password.html')

@app.route('/generate_report', methods=['POST'])
def generate_report():
    if not session.get('username'):
        return redirect(url_for('login'))

    user_id = request.form.get('user_id', '')
    date = request.form.get('date', '')
    week = request.form.get('week', '')
    month = request.form.get('month', '')
    entire_period = request.form.get('entire_period', 'false')
    
    # Check if this is an AJAX request
    is_ajax_request = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # Build the user_report URL
    if user_id and session.get('admin_logged_in'):
        # Admin accessing another user's report
        username = user_id
    else:
        # Regular user or admin viewing their own report
        username = session.get('username')
    
    # Build URL with parameters
    params = []
    if date:
        params.append(f'date={date}')
    if week:
        params.append(f'week={week}')
    if month:
        params.append(f'month={month}')
    if entire_period == 'true':
        params.append('entire_period=1')
    
    url = f'/user_report/{username}'
    if params:
        url += '?' + '&'.join(params)
    
    if is_ajax_request:
        # Return JSON response for AJAX requests (modal loading)
        return jsonify({
            'success': True,
            'url': url,
            'username': username
        })
    else:
        # For non-AJAX requests, redirect to user_report (this will be caught by the modal-only check)
        return redirect(url)

@app.route('/report_auth/<username>', methods=['GET', 'POST'])
def report_auth(username):
    # This route is now deprecated but kept for backward compatibility
    # Redirect directly to user_report without authentication
    if not session.get('username') or not session.get('admin_logged_in'):
        flash('Unauthorized access', 'error')
        return redirect(url_for('index'))
    
    # Get parameters and redirect directly to user_report
    date = request.args.get('date', '') or request.form.get('date', '')
    week = request.args.get('week', '') or request.form.get('week', '')
    month = request.args.get('month', '') or request.form.get('month', '')
    entire_period = request.args.get('entire_period', 'false') or request.form.get('entire_period', 'false')
    
    return redirect(url_for('user_report', 
                           username=username,
                           date=date,
                           week=week,
                           month=month,
                           entire_period=entire_period))

@app.route('/user_report/<username>', methods=['GET', 'POST'])
def user_report(username):
    # Check if user is authorized
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # If not admin and not viewing own report, deny access
    if username != session.get('username') and not session.get('admin_logged_in'):
        flash('Unauthorized access', 'error')
        return redirect(url_for('index'))
    
    # MODAL ONLY: Check if this is an AJAX request for modal loading
    # If it's a direct browser navigation, redirect to appropriate dashboard
    is_ajax_request = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
        'application/json' in request.headers.get('Accept', '') or
        request.headers.get('Sec-Fetch-Dest') == 'empty' or
        request.headers.get('Sec-Fetch-Mode') == 'cors' or
        'text/html' not in request.headers.get('Accept', '')
    )
    
    # Log the request for debugging
    logging.info(f"user_report request - Username: {username}, Method: {request.method}")
    logging.info(f"Headers: X-Requested-With={request.headers.get('X-Requested-With')}, Accept={request.headers.get('Accept')}")
    logging.info(f"Sec-Fetch-Dest={request.headers.get('Sec-Fetch-Dest')}, Sec-Fetch-Mode={request.headers.get('Sec-Fetch-Mode')}")
    logging.info(f"Is AJAX: {is_ajax_request}")
    
    # For GET requests from browsers (not AJAX), redirect to dashboard
    if not is_ajax_request and request.method == 'GET':
        # Direct browser navigation - redirect to appropriate dashboard
        if session.get('admin_logged_in'):
            flash('Berichte können nur über das Dashboard geöffnet werden.', 'info')
            return redirect(url_for('admin'))
        else:
            flash('Berichte können nur über das Dashboard geöffnet werden.', 'info')
            return redirect(url_for('index'))
    
    # Remove password authentication requirement - allow direct access for admins
    
    # Get parameters
    date = request.args.get('date', '') if request.method == 'GET' else request.form.get('date', '')
    week = request.args.get('week', '') if request.method == 'GET' else request.form.get('week', '')
    month = request.args.get('month', '') if request.method == 'GET' else request.form.get('month', '')
    entire_period = request.args.get('entire_period', 'false') if request.method == 'GET' else request.form.get('entire_period', 'false')
    
    # Connect to database with row factory
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Prepare query based on parameters
    query = '''
        SELECT a.check_in, a.check_out, a.has_auto_breaks, a.billable_minutes
        FROM attendance a
        JOIN users u ON a.user_id = u.id
        WHERE u.username = ?
    '''
    params = [username]
    all_users = False
    
    # If username is empty and admin requested all users
    if not username and session.get('admin_logged_in'):
        query = '''
            SELECT u.username, a.check_in, a.check_out, a.has_auto_breaks, a.billable_minutes
            FROM attendance a
            JOIN users u ON a.user_id = u.id
        '''
        params = []
        all_users = True
    
    # Filter by date if provided
    if date:
        query += " AND DATE(a.check_in) = DATE(?)"
        params.append(date)
    
    # Filter by week if provided
    elif week:
        # Parse week string (format: YYYY-Www)
        year, week_num = week.split('-W')
        query += " AND strftime('%Y', a.check_in) = ? AND strftime('%W', a.check_in) = ?"
        params.extend([year, week_num.zfill(2)])
    
    # Filter by month if provided
    elif month:
        # Parse month string (format: YYYY-MM)
        year, month_num = month.split('-')
        query += " AND strftime('%Y', a.check_in) = ? AND strftime('%m', a.check_in) = ?"
        params.extend([year, month_num.zfill(2)])
    
    # Sort by date
    query += " ORDER BY a.check_in DESC"
    
    # Execute query
    cursor.execute(query, params)
    attendance_records = cursor.fetchall()
    
    # Calculate durations
    records_with_duration = []
    total_minutes = 0
    
    for rec in attendance_records:
        if all_users:
            checkin = rec['check_in']
            checkout = rec['check_out']
        else:
            checkin = rec['check_in']
            checkout = rec['check_out']
        
        if checkin and checkout:
            duration = get_duration(checkin, checkout)
            # Add to total (extract hours and minutes)
            try:
                hours, minutes = map(int, duration.split(':'))
                total_minutes += (hours * 60 + minutes)
            except:
                pass
        else:
            duration = "-"
        
        records_with_duration.append((rec, duration))
    
    # Calculate total hours and minutes
    total_hours = total_minutes // 60
    remaining_minutes = total_minutes % 60
    total_duration = f"{total_hours}:{remaining_minutes:02d}"
    
    conn.close()
    
    # Format the current time for the report
    current_time = datetime.now().strftime('%d.%m.%Y %H:%M:%S')
    
    return render_template(
        'user_report.html',
        username=username,
        records=records_with_duration,
        all_users=all_users,
        total_duration=total_duration,
        current_time=current_time
    )

@app.route('/get_breaks/<int:attendance_id>')
def get_breaks(attendance_id):
    """Get breaks for a specific attendance record"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401

    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get breaks for the attendance record
        cursor.execute('''
            SELECT id, start_time, end_time, duration_minutes, 
                is_excluded_from_billing AS is_excluded, 
                is_auto_detected AS is_auto, 
                description
            FROM breaks
            WHERE attendance_id = ?
            ORDER BY start_time
        ''', (attendance_id,))
        
        breaks = []
        for row in cursor.fetchall():
            # Determine break type for better display
            break_type = None
            if row['is_auto']:
                if row['description'] and 'ArbZG' in row['description']:
                    if 'Mittagspause' in row['description']:
                        break_type = 'lunch'
                    else:
                        break_type = 'arbzg'
                else:
                    break_type = 'auto'
            else:
                break_type = 'manual'
            
            breaks.append({
                'id': row['id'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'duration': row['duration_minutes'],
                'is_excluded': bool(row['is_excluded']),
                'is_auto': bool(row['is_auto']),
                'break_type': break_type,
                'description': row['description'] or ''
            })
        
        # Check if attendance record has auto breaks flag set
        cursor.execute('SELECT has_auto_breaks FROM attendance WHERE id = ?', (attendance_id,))
        attendance = cursor.fetchone()
        
        conn.close()
        
        # Include information about the attendance record
        return jsonify({
            'success': True, 
            'breaks': breaks,
            'has_auto_breaks': bool(attendance and attendance['has_auto_breaks']),
            'total_count': len(breaks)
        })
        
    except Exception as e:
        conn.close()
        logging.error(f"Error retrieving breaks: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler beim Abrufen der Pausen: {str(e)}'}), 500

@app.route('/update_consent', methods=['POST'])
def update_consent():
    """Update user consent status (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Nicht angemeldet'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Einwilligungen verwalten'}), 403
    
    # Get request data
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        consent_status = data.get('consent_status')
        
        if not user_id or not consent_status:
            return jsonify({'success': False, 'message': 'Benutzer-ID und Einwilligungsstatus sind erforderlich'}), 400
            
        # Connect to database
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute('SELECT id FROM users WHERE id = ?', (user_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        # Insert new consent record with timestamp
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT INTO user_consents (user_id, consent_status, consent_date)
            VALUES (?, ?, ?)
        ''', (user_id, consent_status, now))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'Einwilligungsstatus wurde auf "{consent_status}" aktualisiert'
        })
    
    except Exception as e:
        logging.error(f"Error updating consent: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/get_password/<int:user_id>', methods=['GET'])
def get_user_password(user_id):
    """
    Retrieve a user's password for display.
    Only accessible by admins.
    """
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können auf Passwörter zugreifen'}), 403
    
    # Add cache control headers to prevent caching
    response = make_response()
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    try:
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Fetch user information
        cursor.execute('SELECT username, password FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        # For security, we only show passwords stored in plaintext format
        # Hashed passwords cannot be decrypted, but we can check if there's a temporary password
        if user['password'].startswith('$2b$'):  # bcrypt hash prefix
            # Get the plain password from the users table
            cursor.execute('SELECT plain_password, verification_hash FROM users WHERE id = ?', (user_id,))
            temp_password_record = cursor.fetchone()
            
            if temp_password_record:
                # Check if we have the verification hash (new format)
                if 'verification_hash' in temp_password_record.keys() and temp_password_record['verification_hash']:
                    # This is a new-style entry with the verification hash
                    verification_hash = temp_password_record['verification_hash']
                    
                    # Generate a new random password
                    import secrets
                    import string
                    import hashlib
                    
                    # Generate a secure random password of length 10
                    alphabet = string.ascii_letters + string.digits
                    new_password = ''.join(secrets.choice(alphabet) for i in range(10))
                    
                    # Hash the new password for storage
                    hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
                    
                    # Update the user's password
                    cursor.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
                    
                    # Create a new verification hash for the new password
                    new_verification_hash = hashlib.sha256(new_password.encode()).hexdigest()
                    
                    # Update the user record
                    cursor.execute('''
                        UPDATE users 
                        SET plain_password = ?, verification_hash = ?, last_password_change = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    ''', (hashed_password, new_verification_hash, user_id))
                    
                    db.commit()
                    
                    logging.info(f"New password generated for user ID: {user_id} during get_password call (using verification hash)")
                    
                    return jsonify({
                        'success': True, 
                        'password': new_password,
                        'message': 'Ein neues Passwort wurde generiert.'
                    })
                else:
                    # This is a legacy entry without verification hash, or with plaintext password
                    # We need to check if the password is in plaintext or hashed
                    legacy_password = temp_password_record['temp_password']
                    
                    if legacy_password.startswith('$2b$'):
                        # This is a hashed temporary password without verification hash
                        # Generate a new password
                        import secrets
                        import string
                        import hashlib
                        
                        # Generate a secure random password of length 10
                        alphabet = string.ascii_letters + string.digits
                        new_password = ''.join(secrets.choice(alphabet) for i in range(10))
                        
                        # Hash the new password for storage
                        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
                        
                        # Update the user's password
                        cursor.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
                        
                        # Create a verification hash for the new password
                        verification_hash = hashlib.sha256(new_password.encode()).hexdigest()
                        
                        # Update the user record with the new format
                        cursor.execute('''
                            UPDATE users 
                            SET plain_password = ?, verification_hash = ?, last_password_change = CURRENT_TIMESTAMP 
                            WHERE id = ?
                        ''', (hashed_password, verification_hash, user_id))
                        
                        db.commit()
                        
                        logging.info(f"New password generated for user ID: {user_id} during get_password call (migrating from legacy format)")
                        
                        return jsonify({
                            'success': True, 
                            'password': new_password,
                            'message': 'Ein neues Passwort wurde generiert.'
                        })
                    else:
                        # This is a plaintext legacy password - use it directly
                        logging.info(f"Retrieved legacy plaintext password for user ID: {user_id} during get_password call")
                        return jsonify({'success': True, 'password': legacy_password})
            else:
                # If there's no temporary password, reset the password and store a new temporary one
                import secrets
                import string
                import hashlib
                
                # Generate a secure random password of length 10
                alphabet = string.ascii_letters + string.digits
                new_password = ''.join(secrets.choice(alphabet) for i in range(10))
                
                # Hash the password for storage
                hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
                
                # Create a verification hash for the new password
                verification_hash = hashlib.sha256(new_password.encode()).hexdigest()
                
                # Update the user's password
                cursor.execute('UPDATE users SET password = ? WHERE id = ?', (hashed_password, user_id))
                
                # Store the hashed password with verification hash
                cursor.execute('''
                    UPDATE users 
                    SET plain_password = ?, verification_hash = ?, last_password_change = CURRENT_TIMESTAMP 
                    WHERE id = ?
                ''', (hashed_password, verification_hash, user_id))
                
                db.commit()
                
                logging.info(f"Password reset during get_password call for user ID: {user_id}")
                
                return jsonify({
                    'success': True, 
                    'password': new_password,
                    'message': 'Das Passwort wurde zurückgesetzt und ein neues Passwort generiert.'
                })
        
        return jsonify({'success': True, 'password': user['password']})
        
    except Exception as e:
        logging.error(f"Error retrieving password for user {user_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500
    # Note: No finally block needed as db connection is handled by Flask's teardown_appcontext

@app.route('/api/user_datasheet/<int:user_id>')
def api_user_datasheet(user_id):
    """Get user datasheet data as JSON for popup modal"""
    if not session.get('username'):
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'Admin access required'}), 403
    
    # Connect to database
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get the temp password from the database
        cursor.execute('SELECT temp_password FROM temp_passwords WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        
        temp_password = result['temp_password'] if result else None
        
        if not temp_password:
            return jsonify({'error': 'Datenblatt ist nicht mehr verfügbar'}), 404
        
        # Get user information
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            return jsonify({'error': 'Benutzer nicht gefunden'}), 404
        
        # Create data for the response
        username = user['username']
        creation_date = datetime.now().strftime("%d.%m.%Y %H:%M")
        current_date = datetime.now().strftime("%d.%m.%Y")
        
        # Get the base URL for login
        if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
            login_url = f"http://{request.host}"
        else:
            login_url = f"https://{request.host}"
        
        # Delete the temp password after use (one-time use)
        cursor.execute('DELETE FROM temp_passwords WHERE user_id = ?', (user_id,))
        conn.commit()
        
        return jsonify({
            'success': True,
            'username': username,
            'password': temp_password,
            'creation_date': creation_date,
            'current_date': current_date,
            'login_url': login_url
        })
    
    except Exception as e:
        logging.error(f"Error getting datasheet data: {str(e)}")
        return jsonify({'error': f'Fehler beim Laden der Daten: {str(e)}'}), 500
    finally:
        conn.close()

@app.route('/my_credentials')
def my_credentials():
    """Show user's own credentials page"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    user_id = session.get('user_id')
    username = session.get('username')
    
    if not user_id or not username:
        flash('Benutzerinformationen nicht gefunden', 'error')
        return redirect(url_for('index'))
    
    # Get the base URL for login
    if request.host.startswith('localhost') or request.host.startswith('127.0.0.1'):
        login_url = f"http://{request.host}"
    else:
        login_url = f"https://{request.host}"
    
    # Get current date
    current_date = datetime.now().strftime("%d.%m.%Y")
    
    return render_template('my_credentials.html',
                         username=username,
                         user_id=user_id,
                         login_url=login_url,
                         current_date=current_date)

@app.route('/api/search_users', methods=['GET'])
def api_search_users():
    """API endpoint for searching users with filters"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können auf die Benutzerverwaltung zugreifen'}), 403
    
    # Get search parameters
    search_term = request.args.get('search', '').strip()
    consent_filter = request.args.get('consent', '').strip()
    sort_by = request.args.get('sort', 'username-asc')
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', 0, type=int)
    
    db = get_db()
    db.row_factory = sqlite3.Row
    cursor = db.cursor()
    
    try:
        # Build the query
        query = '''
            SELECT u.id, u.username, COALESCE(uc.consent_status, 'Unknown') AS consent_status
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status 
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
        '''
        
        params = []
        conditions = []
        
        # Add search filter
        if search_term:
            conditions.append('(u.username LIKE ? OR u.id LIKE ?)')
            search_pattern = f'%{search_term}%'
            params.extend([search_pattern, search_pattern])
        
        # Add consent filter
        if consent_filter:
            conditions.append('COALESCE(uc.consent_status, "Unknown") = ?')
            params.append(consent_filter)
        
        # Add WHERE clause if there are conditions
        if conditions:
            query += ' WHERE ' + ' AND '.join(conditions)
        
        # Add sorting
        if sort_by == 'username-desc':
            query += ' ORDER BY u.username DESC'
        elif sort_by == 'id-asc':
            query += ' ORDER BY u.id ASC'
        elif sort_by == 'id-desc':
            query += ' ORDER BY u.id DESC'
        else:  # default: username-asc
            query += ' ORDER BY u.username ASC'
        
        # Add pagination if specified
        if limit:
            query += f' LIMIT {limit} OFFSET {offset}'
        
        cursor.execute(query, params)
        users = cursor.fetchall()
        
        # Get total count for pagination
        count_query = '''
            SELECT COUNT(*) as total
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status 
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
        '''
        
        if conditions:
            count_query += ' WHERE ' + ' AND '.join(conditions)
        
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()['total']
        
        # Convert to list of dictionaries
        users_list = []
        for user in users:
            users_list.append({
                'id': user['id'],
                'username': user['username'],
                'consent_status': user['consent_status']
            })
        
        return jsonify({
            'success': True,
            'users': users_list,
            'total': total_count,
            'filtered': len(users_list),
            'search_term': search_term,
            'consent_filter': consent_filter,
            'sort_by': sort_by
        })
        
    except Exception as e:
        logging.error(f"Error searching users: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler bei der Suche: {str(e)}'}), 500

@app.route('/api/sync_user_data', methods=['POST'])
def api_sync_user_data():
    """API endpoint to trigger user data synchronization across systems"""
    if not session.get('username') or not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        sync_type = data.get('sync_type', 'full')  # 'full', 'partial', 'cache_only'
        
        if not user_id:
            return jsonify({'success': False, 'message': 'user_id is required'}), 400
        
        # Get user data
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        cursor.execute('''
            SELECT u.*, uc.consent_status, uc.consent_date
            FROM users u
            LEFT JOIN user_consents uc ON u.id = uc.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'User not found'}), 404
        
        user_data = dict(user)
        
        # Trigger synchronization based on type
        sync_results = {}
        
        if sync_type in ['full', 'cache_only']:
            # Refresh cache
            refresh_user_cache()
            sync_results['cache'] = 'refreshed'
        
        if sync_type == 'full':
            # Full synchronization with external systems
            try:
                sync_user_to_external_systems(user_id, user_data['username'], user_data.get('user_role', 'employee'))
                sync_results['external_systems'] = 'synchronized'
            except Exception as e:
                sync_results['external_systems'] = f'error: {str(e)}'
            
            # Notify other systems
            try:
                notify_systems_user_updated(user_id, user_data)
                sync_results['notifications'] = 'sent'
            except Exception as e:
                sync_results['notifications'] = f'error: {str(e)}'
        
        # Log the sync operation
        logging.info(f"User sync triggered by {session.get('username')} for user {user_id}: {sync_results}")
        
        return jsonify({
            'success': True,
            'message': 'User synchronization completed',
            'user_id': user_id,
            'sync_type': sync_type,
            'results': sync_results
        })
        
    except Exception as e:
        logging.error(f"Error in user sync API: {str(e)}")
        return jsonify({'success': False, 'message': f'Sync error: {str(e)}'}), 500


@app.route('/api/webhook/user_events', methods=['POST'])
def webhook_user_events():
    """Webhook endpoint for external systems to receive user events"""
    try:
        # Verify webhook authentication (implement your security mechanism)
        auth_header = request.headers.get('Authorization')
        if not verify_webhook_auth(auth_header):
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.get_json()
        event_type = data.get('event_type')
        user_data = data.get('user_data', {})
        
        # Process different event types
        if event_type == 'user_created':
            handle_external_user_created(user_data)
        elif event_type == 'user_updated':
            handle_external_user_updated(user_data)
        elif event_type == 'user_deleted':
            handle_external_user_deleted(user_data)
        else:
            return jsonify({'error': 'Unknown event type'}), 400
        
        logging.info(f"Webhook processed: {event_type} for user {user_data.get('username', 'unknown')}")
        
        return jsonify({
            'success': True,
            'message': 'Event processed successfully',
            'event_type': event_type
        })
        
    except Exception as e:
        logging.error(f"Webhook error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500


@app.route('/api/system_status', methods=['GET'])
def api_system_status():
    """API endpoint to check system synchronization status"""
    if not session.get('username') or not session.get('admin_logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        db = get_db()
        cursor = db.cursor()
        
        # Get system statistics
        cursor.execute("SELECT COUNT(*) as total_users FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as active_users FROM users WHERE account_status = 'active'")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) as pending_consents FROM user_consents WHERE consent_status = 'pending'")
        pending_consents = cursor.fetchone()[0]
        
        # Check last sync times (this would be stored in a sync_log table in a real implementation)
        last_cache_refresh = get_last_cache_refresh_time()
        last_external_sync = get_last_external_sync_time()
        
        status = {
            'system_health': 'healthy',
            'database_status': 'connected',
            'user_statistics': {
                'total_users': total_users,
                'active_users': active_users,
                'pending_consents': pending_consents
            },
            'synchronization_status': {
                'last_cache_refresh': last_cache_refresh,
                'last_external_sync': last_external_sync,
                'sync_queue_size': 0  # Would be actual queue size in production
            },
            'timestamp': get_local_time().isoformat()
        }
        
        return jsonify(status)
        
    except Exception as e:
        logging.error(f"System status error: {str(e)}")
        return jsonify({
            'system_health': 'error',
            'error': str(e),
            'timestamp': get_local_time().isoformat()
        }), 500


def verify_webhook_auth(auth_header):
    """Verify webhook authentication"""
    # Implement your authentication mechanism here
    # For example: API key, JWT token, HMAC signature, etc.
    
    # Simple API key example:
    if not auth_header:
        return False
    
    # Extract API key from header (format: "Bearer <api_key>")
    try:
        auth_type, api_key = auth_header.split(' ', 1)
        if auth_type.lower() != 'bearer':
            return False
        
        # In production, store this securely in environment variables
        valid_api_keys = ['your-webhook-api-key-here']  # Replace with actual keys
        return api_key in valid_api_keys
        
    except ValueError:
        return False


def handle_external_user_created(user_data):
    """Handle user creation event from external system"""
    try:
        # Process external user creation
        # This could involve creating local user records, 
        # updating caches, sending notifications, etc.
        
        logging.info(f"External user created: {user_data}")
        
        # Example: Update local cache or trigger local processes
        refresh_user_cache()
        
    except Exception as e:
        logging.error(f"Error handling external user creation: {str(e)}")


def handle_external_user_updated(user_data):
    """Handle user update event from external system"""
    try:
        # Process external user update
        logging.info(f"External user updated: {user_data}")
        
        # Example: Sync changes to local database if needed
        user_id = user_data.get('user_id')
        if user_id:
            refresh_user_cache()
            
    except Exception as e:
        logging.error(f"Error handling external user update: {str(e)}")


def handle_external_user_deleted(user_data):
    """Handle user deletion event from external system"""
    try:
        # Process external user deletion
        logging.info(f"External user deleted: {user_data}")
        
        # Example: Clean up local references, update caches
        refresh_user_cache()
        
    except Exception as e:
        logging.error(f"Error handling external user deletion: {str(e)}")


def notify_systems_user_updated(user_id, user_data):
    """Notify other systems about user updates"""
    try:
        # Similar to notify_systems_user_created but for updates
        logging.info(f"System notification triggered for updated user: {user_id}")
        
        # Example notifications for user updates:
        # - Email notifications for role changes
        # - Access control updates
        # - Audit logging
        audit_log_user_update(user_id, user_data)
        
    except Exception as e:
        logging.error(f"Error notifying systems about user update: {str(e)}")


def audit_log_user_update(user_id, user_data):
    """Create audit log entry for user updates"""
    try:
        audit_entry = {
            'event_type': 'USER_UPDATED',
            'user_id': user_id,
            'username': user_data.get('username'),
            'updated_by': session.get('username'),
            'timestamp': get_local_time().isoformat(),
            'user_data': user_data,
            'ip_address': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', '')
        }
        
        logging.info(f"AUDIT: {json.dumps(audit_entry)}")
        
    except Exception as e:
        logging.error(f"Error creating audit log for user update: {str(e)}")


def get_last_cache_refresh_time():
    """Get the timestamp of the last cache refresh"""
    # In a real implementation, this would be stored in database or cache
    # For now, return a placeholder
    return get_local_time().strftime('%Y-%m-%d %H:%M:%S')


def get_last_external_sync_time():
    """Get the timestamp of the last external system sync"""
    # In a real implementation, this would be stored in database
    # For now, return a placeholder
    return get_local_time().strftime('%Y-%m-%d %H:%M:%S')

# Add new user management endpoints after the existing user management functions

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    """Edit user information (admin only)"""
    if not session.get('username'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzer bearbeiten'}), 403
        flash('Nur Administratoren können Benutzer bearbeiten', 'error')
        return redirect(url_for('index'))
    
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if request.method == 'GET':
        # Return user data for editing
        try:
            db = get_db()
            db.row_factory = sqlite3.Row
            cursor = db.cursor()
            
            cursor.execute('''
                SELECT 
                    u.id, u.username, u.first_name, u.last_name, u.employee_id,
                    u.user_role, u.department, u.account_status,
                    COALESCE(uc.consent_status, 'pending') AS consent_status
                FROM users u
                LEFT JOIN (
                    SELECT user_id, consent_status
                    FROM user_consents 
                    WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
                ) uc ON u.id = uc.user_id
                WHERE u.id = ?
            ''', (user_id,))
            
            user = cursor.fetchone()
            if not user:
                if is_ajax:
                    return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
                flash('Benutzer nicht gefunden', 'error')
                return redirect(url_for('user_management'))
            
            user_data = dict(user)
            
            # If AJAX request, return JSON
            if is_ajax:
                return jsonify({'success': True, 'user': user_data})
            
            # If direct access, render HTML template
            return render_template('edit_user.html', user=user_data)
            
        except Exception as e:
            logging.error(f"Error fetching user data for edit: {str(e)}")
            if is_ajax:
                return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500
            flash('Fehler beim Laden der Benutzerdaten', 'error')
            return redirect(url_for('user_management'))
    
    elif request.method == 'POST':
        # Update user data
        try:
            # Get form data
            data = request.get_json() if is_ajax else request.form
            
            username = data.get('username')
            first_name = (data.get('first_name') or '').strip()
            last_name = (data.get('last_name') or '').strip()
            employee_id = (data.get('employee_id') or '').strip()
            user_role = data.get('user_role', 'employee')
            department = (data.get('department') or '').strip()
            account_status = data.get('account_status', 'active')
            consent_status = data.get('consent_status', 'pending')
            
            # Validate required fields
            if not username:
                error_msg = 'Benutzername ist erforderlich'
                if is_ajax:
                    return jsonify({'success': False, 'message': error_msg}), 400
                flash(error_msg, 'error')
                return redirect(url_for('user_management'))
            
            # Validate username format
            if not re.match(r'^[a-zA-Z0-9._-]+$', username):
                error_msg = 'Benutzername darf nur Buchstaben, Zahlen, Punkte, Unterstriche und Bindestriche enthalten'
                if is_ajax:
                    return jsonify({'success': False, 'message': error_msg}), 400
                flash(error_msg, 'error')
                return redirect(url_for('user_management'))
            
            # Validate user role
            valid_roles = ['employee', 'supervisor', 'hr', 'admin']
            if user_role not in valid_roles:
                user_role = 'employee'
            
            # Validate account status
            valid_status_values = ['active', 'inactive', 'suspended']
            if account_status not in valid_status_values:
                account_status = 'active'
            
            # Validate consent status
            valid_consent_values = ['pending', 'granted', 'declined']
            if consent_status not in valid_consent_values:
                consent_status = 'pending'
            
            db = get_db()
            cursor = db.cursor()
            
            # Check if username already exists for another user
            cursor.execute('SELECT id FROM users WHERE username = ? AND id != ?', (username, user_id))
            if cursor.fetchone():
                error_msg = f'Benutzername {username} wird bereits verwendet'
                if is_ajax:
                    return jsonify({'success': False, 'message': error_msg}), 400
                flash(error_msg, 'error')
                return redirect(url_for('user_management'))
            
            # Check if employee_id already exists for another user (if provided)
            if employee_id:
                cursor.execute('SELECT id FROM users WHERE employee_id = ? AND id != ?', (employee_id, user_id))
                if cursor.fetchone():
                    error_msg = f'Mitarbeiter-ID {employee_id} wird bereits verwendet'
                    if is_ajax:
                        return jsonify({'success': False, 'message': error_msg}), 400
                    flash(error_msg, 'error')
                    return redirect(url_for('user_management'))
            
            # Update user data
            current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
            is_admin = 1 if user_role == 'admin' else 0
            
            cursor.execute('''
                UPDATE users SET 
                    username = ?, first_name = ?, last_name = ?, employee_id = ?,
                    user_role = ?, department = ?, account_status = ?, is_admin = ?
                WHERE id = ?
            ''', (username, first_name, last_name, employee_id, user_role, 
                  department, account_status, is_admin, user_id))
            
            # Update consent status if changed
            cursor.execute('''
                INSERT INTO user_consents (user_id, consent_status, consent_date)
                VALUES (?, ?, ?)
            ''', (user_id, consent_status, current_time))
            
            db.commit()
            
            success_message = f'Benutzer {username} wurde erfolgreich aktualisiert'
            logging.info(f"User {user_id} updated by admin {session.get('username')}")
            
            if is_ajax:
                return jsonify({'success': True, 'message': success_message})
            flash(success_message, 'success')
            return redirect(url_for('user_management'))
            
        except Exception as e:
            logging.error(f"Error updating user: {str(e)}")
            error_msg = f'Fehler beim Aktualisieren des Benutzers: {str(e)}'
            if is_ajax:
                return jsonify({'success': False, 'message': error_msg}), 500
            flash(error_msg, 'error')
            return redirect(url_for('user_management'))

@app.route('/toggle_user_status/<int:user_id>', methods=['POST'])
def toggle_user_status(user_id):
    """Toggle user account status between active and inactive (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzerstatus ändern'}), 403
    
    try:
        data = request.get_json()
        current_status = data.get('current_status', 'active')
        
        # Toggle status
        new_status = 'inactive' if current_status == 'active' else 'active'
        
        db = get_db()
        cursor = db.cursor()
        
        # Check if user exists
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        username = user[0]
        
        # Update user status
        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            UPDATE users SET account_status = ? WHERE id = ?
        ''', (new_status, user_id))
        
        db.commit()
        
        action = 'aktiviert' if new_status == 'active' else 'deaktiviert'
        success_message = f'Benutzer {username} wurde {action}'
        
        logging.info(f"User {user_id} ({username}) status changed to {new_status} by admin {session.get('username')}")
        
        return jsonify({
            'success': True, 
            'message': success_message,
            'new_status': new_status
        })
        
    except Exception as e:
        logging.error(f"Error toggling user status: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/view_user_details/<int:user_id>', methods=['GET'])
def view_user_details(user_id):
    """Get detailed user information (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzerdetails einsehen'}), 403
    
    try:
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Get user details with consent history
        cursor.execute('''
            SELECT 
                u.id, u.username, u.first_name, u.last_name, u.employee_id,
                u.user_role, u.department, u.account_status, u.is_admin,
                u.created_at, u.updated_at,
                COALESCE(uc.consent_status, 'Unknown') AS current_consent_status,
                COALESCE(uc.consent_date, '') AS current_consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        user_data = dict(user)
        
        # Get consent history
        cursor.execute('''
            SELECT consent_status, consent_date
            FROM user_consents
            WHERE user_id = ?
            ORDER BY consent_date DESC
            LIMIT 10
        ''', (user_id,))
        
        consent_history = [dict(row) for row in cursor.fetchall()]
        user_data['consent_history'] = consent_history
        
        # Get attendance summary
        cursor.execute('''
            SELECT 
                COUNT(*) as total_days,
                COUNT(CASE WHEN check_out IS NOT NULL THEN 1 END) as completed_days,
                AVG(CASE 
                    WHEN check_out IS NOT NULL 
                    THEN (julianday(check_out) - julianday(check_in)) * 24 * 60 
                END) as avg_hours_per_day
            FROM attendance
            WHERE user_id = ? AND check_in >= date('now', '-30 days')
        ''', (user_id,))
        
        attendance_stats = cursor.fetchone()
        if attendance_stats:
            user_data['attendance_stats'] = dict(attendance_stats)
        
        # Format display values
        role_display = {
            'employee': 'Mitarbeiter',
            'supervisor': 'Vorgesetzter', 
            'hr': 'Personalwesen',
            'admin': 'Administrator'
        }
        user_data['role_display'] = role_display.get(user_data['user_role'], user_data['user_role'])
        
        status_display = {
            'active': 'Aktiv',
            'inactive': 'Inaktiv',
            'suspended': 'Gesperrt'
        }
        user_data['status_display'] = status_display.get(user_data['account_status'], user_data['account_status'])
        
        consent_display = {
            'granted': 'Erteilt',
            'declined': 'Verweigert',
            'pending': 'Ausstehend',
            'Unknown': 'Unbekannt'
        }
        user_data['consent_display'] = consent_display.get(user_data['current_consent_status'], user_data['current_consent_status'])
        
        # Create display name
        if user_data['first_name'] and user_data['last_name']:
            user_data['display_name'] = f"{user_data['first_name']} {user_data['last_name']}"
        else:
            user_data['display_name'] = user_data['username']
        
        return jsonify({'success': True, 'user': user_data})
        
    except Exception as e:
        logging.error(f"Error fetching user details: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/export_users', methods=['GET'])
def export_users():
    """Export user data as CSV (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzerdaten exportieren'}), 403
    
    try:
        import csv
        import io
        
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Get all users with consent status
        cursor.execute('''
            SELECT 
                u.id, u.username, u.first_name, u.last_name, u.employee_id,
                u.user_role, u.department, u.account_status, u.is_admin,
                u.created_at, u.updated_at,
                COALESCE(uc.consent_status, 'Unknown') AS consent_status,
                COALESCE(uc.consent_date, '') AS consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            ORDER BY u.username
        ''')
        
        users = cursor.fetchall()
        
        # Create CSV content
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'ID', 'Benutzername', 'Vorname', 'Nachname', 'Mitarbeiter-ID',
            'Rolle', 'Abteilung', 'Status', 'Admin', 'Erstellt am', 'Aktualisiert am',
            'Datenschutz-Status', 'Datenschutz-Datum'
        ])
        
        # Write user data
        for user in users:
            writer.writerow([
                user['id'], user['username'], user['first_name'] or '', 
                user['last_name'] or '', user['employee_id'] or '',
                user['user_role'], user['department'] or '', user['account_status'],
                'Ja' if user['is_admin'] else 'Nein', user['created_at'], user['updated_at'],
                user['consent_status'], user['consent_date']
            ])
        
        # Create response
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename=benutzer_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        logging.info(f"User data exported by admin {session.get('username')}")
        
        return response
        
    except Exception as e:
        logging.error(f"Error exporting users: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler beim Export: {str(e)}'}), 500

@app.route('/bulk_consent_action', methods=['POST'])
def bulk_consent_action():
    """Perform bulk consent actions (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Massenaktionen durchführen'}), 403
    
    try:
        data = request.get_json()
        action = data.get('action')  # 'granted' or 'declined'
        user_ids = data.get('user_ids', [])  # Optional: specific user IDs, empty for all users
        
        if action not in ['granted', 'declined']:
            return jsonify({'success': False, 'message': 'Ungültige Aktion'}), 400
        
        db = get_db()
        cursor = db.cursor()
        
        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        
        if user_ids:
            # Update specific users
            placeholders = ','.join(['?' for _ in user_ids])
            cursor.execute(f'SELECT id, username FROM users WHERE id IN ({placeholders})', user_ids)
            users = cursor.fetchall()
            
            for user_id, username in users:
                cursor.execute('''
                    INSERT INTO user_consents (user_id, consent_status, consent_date)
                    VALUES (?, ?, ?)
                ''', (user_id, action, current_time))
            
            affected_count = len(users)
        else:
            # Update all users
            cursor.execute('SELECT id FROM users')
            all_user_ids = [row[0] for row in cursor.fetchall()]
            
            for user_id in all_user_ids:
                cursor.execute('''
                    INSERT INTO user_consents (user_id, consent_status, consent_date)
                    VALUES (?, ?, ?)
                ''', (user_id, action, current_time))
            
            affected_count = len(all_user_ids)
        
        db.commit()
        
        action_text = 'erteilt' if action == 'granted' else 'verweigert'
        success_message = f'Datenschutz-Einwilligung wurde für {affected_count} Benutzer {action_text}'
        
        logging.info(f"Bulk consent action '{action}' performed on {affected_count} users by admin {session.get('username')}")
        
        return jsonify({
            'success': True, 
            'message': success_message,
            'affected_count': affected_count
        })
        
    except Exception as e:
        logging.error(f"Error performing bulk consent action: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/search_users', methods=['GET'])
def search_users():
    """Search users with filters (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzer suchen'}), 403
    
    try:
        # Get search parameters
        search_term = request.args.get('q', '').strip()
        role_filter = request.args.get('role', '')
        status_filter = request.args.get('status', '')
        department_filter = request.args.get('department', '')
        consent_filter = request.args.get('consent', '')
        
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Build query with filters
        query = '''
            SELECT 
                u.id, u.username, u.first_name, u.last_name, u.employee_id,
                u.user_role, u.department, u.account_status,
                COALESCE(uc.consent_status, 'Unknown') AS consent_status,
                COALESCE(uc.consent_date, '') AS consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            WHERE 1=1
        '''
        
        params = []
        
        # Add search term filter
        if search_term:
            query += '''
                AND (
                    u.username LIKE ? OR 
                    u.first_name LIKE ? OR 
                    u.last_name LIKE ? OR 
                    u.employee_id LIKE ? OR
                    u.department LIKE ?
                )
            '''
            search_pattern = f'%{search_term}%'
            params.extend([search_pattern] * 5)
        
        # Add role filter
        if role_filter:
            query += ' AND u.user_role = ?'
            params.append(role_filter)
        
        # Add status filter
        if status_filter:
            query += ' AND u.account_status = ?'
            params.append(status_filter)
        
        # Add department filter
        if department_filter:
            query += ' AND u.department LIKE ?'
            params.append(f'%{department_filter}%')
        
        # Add consent filter
        if consent_filter:
            query += ' AND COALESCE(uc.consent_status, "Unknown") = ?'
            params.append(consent_filter)
        
        query += ' ORDER BY u.username'
        
        cursor.execute(query, params)
        users = cursor.fetchall()
        
        # Format user data
        users_data = []
        for user in users:
            user_dict = dict(user)
            
            # Create display name
            if user_dict['first_name'] and user_dict['last_name']:
                user_dict['display_name'] = f"{user_dict['first_name']} {user_dict['last_name']}"
            else:
                user_dict['display_name'] = user_dict['username']
            
            # Format role display
            role_display = {
                'employee': 'Mitarbeiter',
                'supervisor': 'Vorgesetzter', 
                'hr': 'Personalwesen',
                'admin': 'Administrator'
            }
            user_dict['role_display'] = role_display.get(user_dict['user_role'], user_dict['user_role'])
            
            users_data.append(user_dict)
        
        return jsonify({
            'success': True, 
            'users': users_data,
            'count': len(users_data)
        })
        
    except Exception as e:
        logging.error(f"Error searching users: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/user_options/<int:user_id>')
def user_options(user_id):
    """User options page - comprehensive user management interface"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können auf die Benutzeroptionen zugreifen', 'error')
        return redirect(url_for('index'))
    
    try:
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Get comprehensive user data
        cursor.execute('''
            SELECT 
                u.id, 
                u.username, 
                COALESCE(u.first_name, '') AS first_name,
                COALESCE(u.last_name, '') AS last_name,
                COALESCE(u.employee_id, '') AS employee_id,
                COALESCE(u.user_role, 'employee') AS user_role,
                COALESCE(u.department, '') AS department,
                COALESCE(u.account_status, 'active') AS account_status,
                COALESCE(u.is_admin, 0) AS is_admin,
                u.last_login,
                COALESCE(uc.consent_status, 'pending') AS consent_status,
                COALESCE(uc.consent_date, '') AS consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            flash('Benutzer nicht gefunden', 'error')
            return redirect(url_for('user_management'))
        
        # Convert to dictionary and enhance data
        user_data = dict(user)
        
        # Create display name
        if user_data['first_name'] and user_data['last_name']:
            user_data['display_name'] = f"{user_data['first_name']} {user_data['last_name']}"
        else:
            user_data['display_name'] = user_data['username']
        
        # Format role display
        role_display = {
            'employee': 'Mitarbeiter',
            'supervisor': 'Vorgesetzter', 
            'hr': 'Personalwesen',
            'admin': 'Administrator'
        }
        user_data['role_display'] = role_display.get(user_data['user_role'], user_data['user_role'])
        
        # Parse datetime fields safely (created_at field doesn't exist in current schema)
        
        if user_data['last_login']:
            try:
                parsed_date = try_parse(user_data['last_login'])
                if parsed_date:
                    user_data['last_login'] = parsed_date
            except:
                pass
        
        logging.info(f"User options page accessed for user {user_id} by admin {session.get('username')}")
        
        return render_template('user_options.html', user=user_data)
        
    except Exception as e:
        logging.error(f"Error loading user options for user {user_id}: {str(e)}")
        flash('Fehler beim Laden der Benutzeroptionen', 'error')
        return redirect(url_for('user_management'))

@app.route('/update_user_status/<int:user_id>', methods=['POST'])
def update_user_status(user_id):
    """Update user account status (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können Benutzerstatus ändern'}), 403
    
    try:
        data = request.get_json()
        new_status = data.get('account_status')
        
        # Validate status
        valid_statuses = ['active', 'inactive', 'suspended']
        if new_status not in valid_statuses:
            return jsonify({'success': False, 'message': 'Ungültiger Status'}), 400
        
        db = get_db()
        cursor = db.cursor()
        
        # Check if user exists
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        username = user[0]
        
        # Update user status
        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            UPDATE users SET account_status = ? WHERE id = ?
        ''', (new_status, user_id))
        
        db.commit()
        
        status_names = {
            'active': 'aktiviert',
            'inactive': 'deaktiviert',
            'suspended': 'gesperrt'
        }
        
        success_message = f'Benutzer {username} wurde erfolgreich {status_names[new_status]}'
        logging.info(f"User {user_id} status changed to {new_status} by admin {session.get('username')}")
        
        return jsonify({'success': True, 'message': success_message})
        
    except Exception as e:
        logging.error(f"Error updating user status: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/generate_temp_password/<int:user_id>', methods=['POST'])
def generate_temp_password(user_id):
    """Generate temporary password for user (admin only)"""
    if not session.get('username'):
        return jsonify({'success': False, 'message': 'Sie müssen angemeldet sein'}), 401
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Nur Administratoren können temporäre Passwörter generieren'}), 403
    
    try:
        import secrets
        import string
        
        db = get_db()
        cursor = db.cursor()
        
        # Check if user exists
        cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'message': 'Benutzer nicht gefunden'}), 404
        
        username = user[0]
        
        # Generate secure temporary password
        alphabet = string.ascii_letters + string.digits
        temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))
        
        # Hash the temporary password
        hashed_password = bcrypt.generate_password_hash(temp_password).decode('utf-8')
        
        # Update user password
        current_time = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            UPDATE users SET password = ? WHERE id = ?
        ''', (hashed_password, user_id))
        
        db.commit()
        
        logging.info(f"Temporary password generated for user {user_id} by admin {session.get('username')}")
        
        return jsonify({
            'success': True, 
            'message': f'Temporäres Passwort für {username} generiert',
            'temp_password': temp_password
        })
        
    except Exception as e:
        logging.error(f"Error generating temporary password: {str(e)}")
        return jsonify({'success': False, 'message': f'Fehler: {str(e)}'}), 500

@app.route('/export_user_data/<int:user_id>')
def export_user_data(user_id):
    """Export all data for a specific user as CSV (admin only)"""
    if not session.get('username'):
        return redirect(url_for('login'))
    
    # Check if user is an admin
    if not session.get('admin_logged_in'):
        flash('Nur Administratoren können Benutzerdaten exportieren', 'error')
        return redirect(url_for('index'))
    
    try:
        import csv
        from io import StringIO
        
        db = get_db()
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        
        # Get user data
        cursor.execute('''
            SELECT 
                u.id, u.username, u.first_name, u.last_name, u.employee_id,
                u.user_role, u.department, u.account_status, u.is_admin,
                u.last_login,
                COALESCE(uc.consent_status, 'Unknown') AS consent_status,
                COALESCE(uc.consent_date, '') AS consent_date
            FROM users u
            LEFT JOIN (
                SELECT user_id, consent_status, consent_date
                FROM user_consents 
                WHERE id IN (SELECT MAX(id) FROM user_consents GROUP BY user_id)
            ) uc ON u.id = uc.user_id
            WHERE u.id = ?
        ''', (user_id,))
        
        user = cursor.fetchone()
        if not user:
            flash('Benutzer nicht gefunden', 'error')
            return redirect(url_for('user_management'))
        
        # Get attendance data for the user
        cursor.execute('''
            SELECT date, check_in, check_out, total_hours, break_duration
            FROM attendance 
            WHERE user_id = ? 
            ORDER BY date DESC
        ''', (user_id,))
        
        attendance_records = cursor.fetchall()
        
        # Create CSV output
        output = StringIO()
        
        # Write user information
        output.write("=== BENUTZERDATEN ===\n")
        output.write(f"ID,{user['id']}\n")
        output.write(f"Benutzername,{user['username']}\n")
        output.write(f"Vorname,{user['first_name'] or ''}\n")
        output.write(f"Nachname,{user['last_name'] or ''}\n")
        output.write(f"Mitarbeiter-ID,{user['employee_id'] or ''}\n")
        output.write(f"Rolle,{user['user_role']}\n")
        output.write(f"Abteilung,{user['department'] or ''}\n")
        output.write(f"Kontostatus,{user['account_status']}\n")
        output.write(f"Administrator,{'Ja' if user['is_admin'] else 'Nein'}\n")
        output.write(f"Erstellt am,Nicht verfügbar\n")
        output.write(f"Letzter Login,{user['last_login'] or 'Nie'}\n")
        output.write(f"Datenschutz-Einwilligung,{user['consent_status']}\n")
        output.write(f"Einwilligung am,{user['consent_date']}\n")
        output.write("\n")
        
        # Write attendance data
        output.write("=== ANWESENHEITSDATEN ===\n")
        output.write("Datum,Ankunft,Abgang,Gesamtstunden,Pausenzeit\n")
        
        for record in attendance_records:
            output.write(f"{record['date']},{record['check_in'] or ''},{record['check_out'] or ''},{record['total_hours'] or ''},{record['break_duration'] or ''}\n")
        
        # Create response
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename=benutzer_{user["username"]}_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        logging.info(f"User data exported for user {user_id} by admin {session.get('username')}")
        
        return response
        
    except Exception as e:
        logging.error(f"Error exporting user data: {str(e)}")
        flash('Fehler beim Exportieren der Benutzerdaten', 'error')
        return redirect(url_for('user_management'))

if __name__ == '__main__':
    print("Initializing BTZ-Zeiterfassung application...")
    init_db()
    print("Database initialization complete")
    print("Starting web server...")
    app.run(host='0.0.0.0', port=8091, debug=True)
