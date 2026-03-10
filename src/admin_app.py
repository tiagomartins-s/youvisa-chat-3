import os
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import database as db

db.init_db()  # Garante schema e migrações (ex.: updated_at em tasks)

try:
    from . import notifications
    from . import services
except (ImportError, ValueError):
    import notifications
    import services

st.set_page_config(page_title="Admin YOUVISA", layout="wide")
st.title("Painel Administrativo YOUVISA")

tab1, tab2, tab3 = st.tabs(["Usuários", "Solicitações", "Configuração"])

with tab1:
    st.header("Usuários Cadastrados")
    conn = db.get_connection()
    users = pd.read_sql_query("SELECT id, telegram_id, name, cpf, email, created_at FROM users", conn)
    conn.close()
    st.dataframe(users)

def _get_document_text(doc):
    """Retorna o texto extraído do documento (extracted_data). Suporta formato novo {"texto": "..."} e antigo (dict de campos)."""
    raw = doc.get('extracted_data') if hasattr(doc, 'get') else (doc['extracted_data'] if 'extracted_data' in doc.keys() else None)
    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return str(raw)
    if not isinstance(data, dict):
        return str(data)
    if "texto" in data and data["texto"]:
        return data["texto"]
    # Formato antigo: vários campos
    return "\n".join(f"{k}: {v}" for k, v in data.items() if v)


def _render_task_detail(row):
    """Renderiza a página de detalhe de uma solicitação (dados do usuário, status, documentos)."""
    row = dict(row) if hasattr(row, 'keys') else row
    task_id = row['task_id']
    user_email = (row.get("user_email") or "").strip() or None
    current_proc_status = (row.get('status') or 'RECEBIDO')

    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**CPF:** {row['user_cpf']}")
        st.write(f"**E-mail:** {row.get('user_email') or '-'}")
        st.write(f"**Criado em:** {row['created_at']}")
        st.write(f"**Documentos Necessários:** {row['required_docs']}")

        st.write("**Histórico de status:**")
        hist = db.get_status_history("task", task_id)
        if hist:
            for h in reversed(hist):
                st.caption(f"{h['created_at']}: {h['from_status'] or '—'} → **{h['to_status']}**")
        else:
            st.caption("Nenhuma transição registrada.")

        proc_allowed_next = db.PROCESS_TRANSITIONS.get(current_proc_status, ())
        proc_options = [current_proc_status] + list(proc_allowed_next)
        new_proc_status = st.selectbox(
            "Status da solicitação",
            proc_options,
            index=0,
            key=f"task_status_inline_{task_id}",
        )
        if st.button(
            "Atualizar status da solicitação",
            key=f"update_task_status_inline_{task_id}",
        ):
            if new_proc_status != current_proc_status:
                if db.update_task_status(task_id, new_proc_status):
                    st.success(f"Status da solicitação atualizado para {new_proc_status}.")
                    if user_email:
                        notifications.notify_status_change(
                            user_email,
                            "Sua solicitação",
                            current_proc_status,
                            new_proc_status,
                        )
                    st.rerun()
                else:
                    st.error("Transição de status da solicitação não permitida.")

    with col2:
        docs = db.get_task_documents(task_id)
        if docs:
            docs = [dict(d) for d in docs]
            st.write("**Documentos Enviados:**")
            for doc in docs:
                doc_status_key = f"doc_status_display_{doc['id']}"
                d_current_db = (doc.get('status') or 'RECEBIDO')
                d_current = st.session_state.get(doc_status_key, d_current_db)

                st.write(f"- {doc['doc_type']} (status: {d_current})")

                d_allowed = db.DOCUMENT_TRANSITIONS.get(d_current, ())
                reject_mode_key = f"reject_mode_{doc['id']}"
                show_text_key = f"show_text_{doc['id']}"
                b1, b2, b3, b4 = st.columns(4)
                with b1:
                    if os.path.exists(doc['file_path']):
                        with open(doc['file_path'], "rb") as f:
                            st.download_button(
                                label="Baixar",
                                data=f,
                                file_name=os.path.basename(doc['file_path']),
                                mime="application/octet-stream",
                                key=f"dl_{task_id}_{doc['id']}",
                            )
                with b2:
                    if st.button("Visualizar texto", key=f"view_text_{task_id}_{doc['id']}"):
                        st.session_state[show_text_key] = not st.session_state.get(show_text_key, False)
                with b3:
                    if st.button(
                        "Avançar status",
                        key=f"advance_doc_{task_id}_{doc['id']}",
                        help="Move o documento para o próximo status permitido.",
                    ):
                        if not d_allowed:
                            st.warning("Não há próximo status disponível para este documento.")
                        else:
                            d_new = d_allowed[0]
                            if db.update_document_status(doc['id'], d_new):
                                st.success(f"Status do documento avançado para {d_new}.")
                                st.session_state[doc_status_key] = d_new
                                if user_email:
                                    notifications.notify_status_change(
                                        user_email,
                                        f"Documento {doc['doc_type']}",
                                        d_current,
                                        d_new,
                                    )
                                st.rerun()
                            else:
                                st.error("Transição de status não permitida para este documento.")
                with b4:
                    in_reject_mode = st.session_state.get(reject_mode_key, False)
                    if not in_reject_mode:
                        if st.button(
                            "Recusar",
                            key=f"open_reject_{task_id}_{doc['id']}",
                            help="Iniciar recusa do documento.",
                        ):
                            st.session_state[reject_mode_key] = True
                    else:
                        st.caption("Recusa em andamento...")

                if st.session_state.get(show_text_key, False):
                    doc_text = _get_document_text(doc)
                    if not doc_text and os.path.exists(doc['file_path']):
                        try:
                            extracted = services.extract_document_data(doc['file_path'], doc.get('doc_type') or 'documento')
                            if extracted and extracted.get('texto'):
                                db.update_document_extracted_data(doc['id'], extracted)
                                doc_text = extracted.get('texto', '')
                        except Exception as ex:
                            st.caption(f"Não foi possível extrair o texto: {ex}")
                    if doc_text:
                        st.text_area("Texto identificado no documento pela IA", value=doc_text, height=200, key=f"text_block_{task_id}_{doc['id']}", disabled=True)
                    else:
                        st.caption("Nenhum texto extraído para este documento.")

                if st.session_state.get(reject_mode_key, False):
                    reject_reason = st.text_area(
                        "Motivo da recusa",
                        key=f"reject_reason_{task_id}_{doc['id']}",
                        help="Explique para o usuário o que precisa ser corrigido no documento.",
                    )
                    col_r1, col_r2 = st.columns(2)
                    with col_r1:
                        if st.button(
                            "Finalizar recusa",
                            key=f"confirm_reject_{task_id}_{doc['id']}",
                        ):
                            if not reject_reason.strip():
                                st.error("Informe o motivo da recusa antes de recusar o documento.")
                            else:
                                new_status = "REPROVADO"
                                if db.update_document_status(doc['id'], new_status):
                                    st.success("Documento marcado como REPROVADO.")
                                    st.session_state[doc_status_key] = new_status
                                    st.session_state[reject_mode_key] = False
                                    if user_email:
                                        notifications.notify_status_change(
                                            user_email,
                                            f"Documento {doc['doc_type']}",
                                            d_current,
                                            new_status,
                                            detail=f"Motivo da recusa: {reject_reason.strip()}",
                                        )
                                    st.rerun()
                                else:
                                    st.error("Transição para REPROVADO não é permitida a partir do status atual.")
                    with col_r2:
                        if st.button(
                            "Cancelar recusa",
                            key=f"cancel_reject_{task_id}_{doc['id']}",
                        ):
                            st.session_state[reject_mode_key] = False
                            try:
                                del st.session_state[f"reject_reason_{task_id}_{doc['id']}"]
                            except KeyError:
                                pass
        else:
            st.warning("Nenhum documento enviado ainda.")


with tab2:
    st.header("Solicitações de Visto (Tasks)")

    view_task_id = st.session_state.get("admin_view_task_id")

    if view_task_id is not None:
        # Página de detalhe de uma solicitação
        row = db.get_task_details_by_id(view_task_id)
        if row is None:
            if "admin_view_task_id" in st.session_state:
                del st.session_state["admin_view_task_id"]
            st.rerun()
        else:
            if st.button("← Voltar à lista", key="back_to_tasks_list"):
                del st.session_state["admin_view_task_id"]
                st.rerun()
            st.subheader(f"{row['user_name']} — {row['country']} | Status: {row.get('status') or 'RECEBIDO'}")
            _render_task_detail(row)
    else:
        # Lista de solicitações (mais recentes primeiro)
        tasks_df = db.get_all_tasks_details()
        if not tasks_df.empty:
            for index, row in tasks_df.iterrows():
                r = row.to_dict() if hasattr(row, 'to_dict') else dict(row)
                status = (r.get('status') or 'RECEBIDO')
                col_info, col_btn = st.columns([5, 1])
                with col_info:
                    st.write(f"**{r['user_name']}** — {r['country']} | **Status:** {status} | Criado em: {r['created_at']}")
                with col_btn:
                    if st.button("Ver", key=f"view_task_{r['task_id']}"):
                        st.session_state["admin_view_task_id"] = int(r['task_id'])
                        st.rerun()
        else:
            st.info("Nenhuma solicitação encontrada.")

with tab3:
    st.header("Configuração")

    st.subheader("E-mail da plataforma (notificações)")
    admin_email = db.get_platform_setting("admin_email") or ""
    new_admin_email = st.text_input("E-mail para receber avisos de novos documentos", value=admin_email)
    if st.button("Salvar e-mail da plataforma"):
        db.set_platform_setting("admin_email", new_admin_email.strip())
        st.success("E-mail salvo. Você receberá avisos quando um usuário enviar documento.")

    st.subheader("Adicionar Novo País")
    with st.form("add_country_form"):
        country_name = st.text_input("Nome do País")
        required_docs = st.text_area("Documentos Necessários (separados por vírgula)", help="Ex: Passaporte, Carteira de Motorista, Extrato Bancário")
        submitted = st.form_submit_button("Adicionar País")
        if submitted:
            if country_name and required_docs:
                if db.add_country(country_name, required_docs):
                    st.success(f"{country_name} adicionado com sucesso!")
                else:
                    st.error(f"O país {country_name} já existe.")
            else:
                st.error("Preencha todos os campos.")

    st.subheader("Países Existentes")
    countries = db.get_countries()
    if countries:
        for c in countries:
            st.text(f"{c['name']}: {c['required_docs']}")
