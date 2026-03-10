"""
Serviço de notificações por e-mail (event-driven).
Dispara e-mails quando: documento recebido, mudança de status de documento/processo.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

def _get_smtp_config():
    return {
        'host': os.getenv('SMTP_HOST', ''),
        'port': int(os.getenv('SMTP_PORT', '587')),
        'user': os.getenv('SMTP_USER', ''),
        'password': os.getenv('SMTP_PASSWORD', ''),
        'from_addr': os.getenv('SMTP_FROM', os.getenv('SMTP_USER', '')),
        'use_tls': os.getenv('SMTP_USE_TLS', 'true').lower() == 'true',
    }

def send_email(to_email, subject, body_html, body_text=None):
    """Envia e-mail. Se SMTP não configurado, apenas loga (não quebra o fluxo)."""
    if not to_email or not str(to_email).strip():
        return False
    cfg = _get_smtp_config()
    if not cfg['host'] or not cfg['user']:
        print(f"[NOTIF] SMTP não configurado. E-mail não enviado para {to_email}: {subject}")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = cfg['from_addr']
        msg['To'] = to_email
        msg.attach(MIMEText(body_text or body_html, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        with smtplib.SMTP(cfg['host'], cfg['port']) as server:
            if cfg['use_tls']:
                server.starttls()
            if cfg['password']:
                server.login(cfg['user'], cfg['password'])
            server.sendmail(cfg['from_addr'], to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[NOTIF] Erro ao enviar e-mail para {to_email}: {e}")
        return False

def notify_document_received(admin_email, user_name, doc_type, task_id):
    """Notifica o administrador que o usuário X enviou o documento Y."""
    subject = f"[YOUVISA] Documento recebido: {doc_type}"
    body = f"""
    <p>O usuário <strong>{user_name}</strong> enviou o documento <strong>{doc_type}</strong>.</p>
    <p>Solicitação (task) ID: {task_id}. Acesse o painel administrativo para analisar.</p>
    """
    return send_email(admin_email, subject, body)

def notify_status_change(user_email, entity_label, old_status, new_status, detail=None):
    """Notifica o usuário sobre mudança de status (documento ou processo)."""
    subject = f"[YOUVISA] Atualização: {entity_label} - {new_status}"
    body = f"""
    <p>Sua solicitação YOUVISA foi atualizada.</p>
    <p><strong>{entity_label}</strong>: de {old_status or 'N/A'} para <strong>{new_status}</strong>.</p>
    """
    if detail:
        body += f"<p>{detail}</p>"
    body += "<p>Acesse o chatbot ou a plataforma para mais detalhes.</p>"
    return send_email(user_email, subject, body)
