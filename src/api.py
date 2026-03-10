"""
API REST para consulta de status (sincronização com interface web/mobile).
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import database as db

app = FastAPI(title="YOUVISA Status API")

class TaskStatusResponse(BaseModel):
    task_id: int
    status: str
    status_explicacao: str
    country: str
    documentos_enviados: list
    documentos_faltando: list

@app.get("/api/status/telegram/{telegram_id}", response_model=TaskStatusResponse)
def get_status_by_telegram(telegram_id: int):
    """Retorna o status da solicitação ativa do usuário (por telegram_id)."""
    user = db.get_user(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    task = db.get_user_active_task(user['id'])
    if not task:
        raise HTTPException(status_code=404, detail="Nenhuma solicitação ativa")
    docs = db.get_task_documents(task['id'])
    required = [d.strip() for d in (task.get('required_docs') or '').split(',')]
    sent = [d['doc_type'] for d in docs]
    missing = [d for d in required if d not in sent]
    try:
        from . import services
    except ImportError:
        import services
    explicacao = services.explain_status_simple(task.get('status', 'RECEBIDO'))
    return TaskStatusResponse(
        task_id=task['id'],
        status=task.get('status', 'RECEBIDO'),
        status_explicacao=explicacao,
        country=task.get('country_name', ''),
        documentos_enviados=sent,
        documentos_faltando=missing,
    )

@app.get("/api/status/task/{task_id}")
def get_status_by_task_id(task_id: int):
    """Retorna o status de uma solicitação por ID (para painel/admin)."""
    task = db.get_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    docs = db.get_task_documents(task_id)
    required = [d.strip() for d in (task.get('required_docs') or '').split(',')]
    sent = [d['doc_type'] for d in docs]
    missing = [d for d in required if d not in sent]
    try:
        from . import services
    except ImportError:
        import services
    explicacao = services.explain_status_simple(task.get('status', 'RECEBIDO'))
    return {
        "task_id": task_id,
        "status": task.get('status', 'RECEBIDO'),
        "status_explicacao": explicacao,
        "country": task.get('country_name', ''),
        "documentos_enviados": sent,
        "documentos_faltando": missing,
        "documentos": [{"doc_type": d['doc_type'], "status": d.get('status') or 'RECEBIDO'} for d in docs],
    }

@app.get("/api/history/{entity_type}/{entity_id}")
def get_status_history(entity_type: str, entity_id: int):
    """Histórico de transições de status (auditoria)."""
    if entity_type not in ("task", "document"):
        raise HTTPException(status_code=400, detail="entity_type deve ser task ou document")
    history = db.get_status_history(entity_type, entity_id)
    def row_to_dict(r):
        return {k: r[k] for k in r.keys()} if hasattr(r, 'keys') else dict(r)
    return {"entity_type": entity_type, "entity_id": entity_id, "history": [row_to_dict(h) for h in history]}

def run():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    run()
