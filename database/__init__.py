import sqlite3
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'youvisa.db'

# Máquina de estados: status de documento
DOCUMENT_STATUS = ('RECEBIDO', 'EM_ANALISE', 'APROVADO', 'REPROVADO', 'PENDENTE')
DOCUMENT_TRANSITIONS = {
    'RECEBIDO': ('EM_ANALISE', 'PENDENTE'),
    'EM_ANALISE': ('APROVADO', 'REPROVADO', 'PENDENTE'),
    'PENDENTE': ('EM_ANALISE', 'RECEBIDO'),
    'APROVADO': (),  # estado final
    'REPROVADO': ('EM_ANALISE',),  # pode reanalisar
}

# Máquina de estados: status do processo (task)
PROCESS_STATUS = ('RECEBIDO', 'EM_ANALISE', 'PENDENTE', 'APROVADO', 'FINALIZADO')
PROCESS_TRANSITIONS = {
    'RECEBIDO': ('EM_ANALISE', 'PENDENTE'),
    'EM_ANALISE': ('APROVADO', 'PENDENTE', 'RECEBIDO'),
    'PENDENTE': ('EM_ANALISE', 'RECEBIDO'),
    'APROVADO': ('FINALIZADO', 'EM_ANALISE'),
    'FINALIZADO': (),
}

def get_connection():
    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # Users (com email para notificações)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            name TEXT,
            cpf TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migração: adicionar coluna email se não existir
    try:
        c.execute('ALTER TABLE users ADD COLUMN email TEXT')
    except sqlite3.OperationalError:
        pass
    
    # Configuração da plataforma (email admin, etc.)
    c.execute('''
        CREATE TABLE IF NOT EXISTS platform_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Countries
    c.execute('''
        CREATE TABLE IF NOT EXISTS countries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            required_docs TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tasks (status do processo: RECEBIDO, EM_ANALISE, PENDENTE, APROVADO, FINALIZADO)
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            country_id INTEGER,
            status TEXT DEFAULT 'RECEBIDO',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(country_id) REFERENCES countries(id)
        )
    ''')
    # Migração: alterar status antigos para novo esquema
    try:
        c.execute("UPDATE tasks SET status = 'EM_ANALISE' WHERE status = 'IN_PROGRESS'")
        c.execute("UPDATE tasks SET status = 'RECEBIDO' WHERE status = 'PENDING'")
        c.execute("UPDATE tasks SET status = 'EM_ANALISE' WHERE status = 'READY'")
    except Exception:
        pass
    # Migração: adicionar updated_at em tasks se não existir (SQLite não aceita DEFAULT CURRENT_TIMESTAMP em ALTER)
    c.execute("PRAGMA table_info(tasks)")
    columns = [row[1] for row in c.fetchall()]
    if 'updated_at' not in columns:
        c.execute('ALTER TABLE tasks ADD COLUMN updated_at TIMESTAMP')
        c.execute("UPDATE tasks SET updated_at = COALESCE(created_at, datetime('now')) WHERE updated_at IS NULL")
    
    # Document types config: por tipo, quais campos extrair (JSON array de {nome, descricao})
    c.execute('''
        CREATE TABLE IF NOT EXISTS document_type_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT UNIQUE,
            extraction_schema TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Documents (com status e dados extraídos)
    c.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            doc_type TEXT,
            file_path TEXT,
            status TEXT DEFAULT 'RECEBIDO',
            extracted_data TEXT,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        )
    ''')
    try:
        c.execute('ALTER TABLE documents ADD COLUMN status TEXT DEFAULT \'RECEBIDO\'')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE documents ADD COLUMN extracted_data TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        c.execute('ALTER TABLE documents ADD COLUMN updated_at TIMESTAMP')
        c.execute("UPDATE documents SET updated_at = COALESCE(uploaded_at, datetime('now')) WHERE updated_at IS NULL")
    except sqlite3.OperationalError:
        pass
    
    # Histórico de transições de status (documento e processo) para auditoria
    c.execute('''
        CREATE TABLE IF NOT EXISTS status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT,
            entity_id INTEGER,
            from_status TEXT,
            to_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        )
    ''')
    
    # Contexto de conversa do chatbot (para manter coerência)
    c.execute('''
        CREATE TABLE IF NOT EXISTS conversation_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# ---------- Platform settings ----------
def get_platform_setting(key):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT value FROM platform_settings WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    return row['value'] if row else None

def set_platform_setting(key, value):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO platform_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP
    ''', (key, value, value))
    conn.commit()
    conn.close()

# ---------- Users ----------
def add_user(telegram_id, name, cpf, email=None):
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (telegram_id, name, cpf, email) VALUES (?, ?, ?, ?)',
                  (telegram_id, name, cpf, email or ''))
        conn.commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def update_user_email(telegram_id, email):
    conn = get_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET email = ? WHERE telegram_id = ?', (email, telegram_id))
    conn.commit()
    conn.close()

def get_user(telegram_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
    user = c.fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    user = c.fetchone()
    conn.close()
    return user

# ---------- Countries ----------
def add_country(name, required_docs):
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute('INSERT INTO countries (name, required_docs) VALUES (?, ?)', (name, required_docs))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_countries():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM countries')
    countries = c.fetchall()
    conn.close()
    return countries

def get_country_by_name(name):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM countries WHERE name = ?', (name,))
    country = c.fetchone()
    conn.close()
    return country

# ---------- Tasks ----------
def create_task(user_id, country_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('INSERT INTO tasks (user_id, country_id, status) VALUES (?, ?, ?)',
              (user_id, country_id, 'RECEBIDO'))
    task_id = c.lastrowid
    conn.commit()
    conn.close()
    _log_status_change('task', task_id, None, 'RECEBIDO')
    return task_id

def get_user_active_task(user_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT t.*, c.name as country_name, c.required_docs 
        FROM tasks t 
        JOIN countries c ON t.country_id = c.id 
        WHERE t.user_id = ? AND t.status != 'FINALIZADO'
        ORDER BY t.created_at DESC LIMIT 1
    ''', (user_id,))
    task = c.fetchone()
    conn.close()
    return task

def get_task_by_id(task_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT t.*, c.name as country_name, c.required_docs 
        FROM tasks t 
        JOIN countries c ON t.country_id = c.id 
        WHERE t.id = ?
    ''', (task_id,))
    task = c.fetchone()
    conn.close()
    return task

def update_task_status(task_id, new_status, from_status=None):
    if new_status not in PROCESS_STATUS:
        return False
    if from_status and from_status not in PROCESS_TRANSITIONS.get(from_status, ()):
        if new_status not in PROCESS_TRANSITIONS.get(from_status, ()):
            return False
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT status FROM tasks WHERE id = ?', (task_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    current = row['status']
    if new_status not in PROCESS_TRANSITIONS.get(current, ()) and current != new_status:
        # Permite manter mesmo status para atualização de updated_at
        if current == new_status:
            c.execute('UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (task_id,))
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False
    c.execute('UPDATE tasks SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_status, task_id))
    conn.commit()
    conn.close()
    _log_status_change('task', task_id, current, new_status)
    return True

def _log_status_change(entity_type, entity_id, from_status, to_status, metadata=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO status_history (entity_type, entity_id, from_status, to_status, metadata)
        VALUES (?, ?, ?, ?, ?)
    ''', (entity_type, entity_id, from_status or '', to_status, json.dumps(metadata or {})))
    conn.commit()
    conn.close()

# ---------- Documents ----------
def add_document(task_id, doc_type, file_path, extracted_data=None):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO documents (task_id, doc_type, file_path, status, extracted_data)
        VALUES (?, ?, ?, 'RECEBIDO', ?)
    ''', (task_id, doc_type, file_path, json.dumps(extracted_data) if extracted_data is not None else None))
    doc_id = c.lastrowid
    conn.commit()
    conn.close()
    _log_status_change('document', doc_id, None, 'RECEBIDO')
    return doc_id

def get_document(doc_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM documents WHERE id = ?', (doc_id,))
    doc = c.fetchone()
    conn.close()
    return doc

def get_task_documents(task_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM documents WHERE task_id = ?', (task_id,))
    docs = c.fetchall()
    conn.close()
    return docs

def update_document_status(doc_id, new_status):
    doc = get_document(doc_id)
    if not doc:
        return False
    current = doc['status'] or 'RECEBIDO'
    if new_status not in DOCUMENT_STATUS:
        return False
    allowed = DOCUMENT_TRANSITIONS.get(current, ())
    if current != new_status and new_status not in allowed:
        return False
    conn = get_connection()
    c = conn.cursor()
    c.execute('UPDATE documents SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (new_status, doc_id))
    conn.commit()
    conn.close()
    _log_status_change('document', doc_id, current, new_status)
    return True

def update_document_extracted_data(doc_id, extracted_data):
    conn = get_connection()
    c = conn.cursor()
    c.execute('UPDATE documents SET extracted_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
              (json.dumps(extracted_data) if isinstance(extracted_data, dict) else extracted_data, doc_id))
    conn.commit()
    conn.close()

# ---------- Document type config (extraction schema) ----------
def get_document_type_config(doc_type):
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM document_type_config WHERE doc_type = ?', (doc_type,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_document_type_configs():
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT * FROM document_type_config')
    rows = c.fetchall()
    conn.close()
    return rows

def set_document_type_config(doc_type, extraction_schema):
    """extraction_schema: list of {field_name, description} or JSON string"""
    conn = get_connection()
    c = conn.cursor()
    data = json.dumps(extraction_schema) if isinstance(extraction_schema, list) else extraction_schema
    c.execute('''
        INSERT INTO document_type_config (doc_type, extraction_schema) VALUES (?, ?)
        ON CONFLICT(doc_type) DO UPDATE SET extraction_schema = excluded.extraction_schema
    ''', (doc_type, data))
    conn.commit()
    conn.close()

# ---------- Status history ----------
def get_status_history(entity_type, entity_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT * FROM status_history WHERE entity_type = ? AND entity_id = ?
        ORDER BY created_at DESC
    ''', (entity_type, entity_id))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- Conversation context ----------
def add_conversation_turn(user_id, session_id, role, content):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO conversation_context (user_id, session_id, role, content)
        VALUES (?, ?, ?, ?)
    ''', (user_id, session_id or 'default', role, content))
    conn.commit()
    conn.close()

def get_recent_conversation(user_id, session_id=None, limit=10):
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT role, content FROM conversation_context
        WHERE user_id = ? AND (session_id = ? OR ? IS NULL)
        ORDER BY created_at DESC LIMIT ?
    ''', (user_id, session_id or 'default', session_id, limit))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))

# ---------- Admin: all tasks with details ----------
def get_all_tasks_details():
    conn = get_connection()
    import pandas as pd
    query = '''
        SELECT 
            t.id as task_id,
            u.name as user_name,
            u.cpf as user_cpf,
            u.email as user_email,
            c.name as country,
            c.required_docs,
            t.status,
            t.created_at,
            t.updated_at
        FROM tasks t
        JOIN users u ON t.user_id = u.id
        JOIN countries c ON t.country_id = c.id
        ORDER BY t.created_at DESC
    '''
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def get_task_details_by_id(task_id):
    """Retorna os detalhes de uma task (mesmo formato que uma linha de get_all_tasks_details)."""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        SELECT 
            t.id as task_id,
            u.name as user_name,
            u.cpf as user_cpf,
            u.email as user_email,
            c.name as country,
            c.required_docs,
            t.status,
            t.created_at,
            t.updated_at
        FROM tasks t
        JOIN users u ON t.user_id = u.id
        JOIN countries c ON t.country_id = c.id
        WHERE t.id = ?
    ''', (task_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
