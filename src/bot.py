import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import database as db

try:
    from . import services
    from . import notifications
except (ImportError, ValueError):
    import services
    import notifications

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

NAME, CPF, EMAIL, SELECT_COUNTRY, UPLOAD_DOCS, UPDATE_EMAIL = range(6)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    logger.info("User %s started the conversation.", user.first_name)
    existing_user = db.get_user(user.id)
    if existing_user:
        has_email = "email" in existing_user.keys() and bool(existing_user["email"])
        message = (
            f"Olá, {existing_user['name']}! Somos a YOUVISA, aqui para auxiliar você em todo o processo de vistos."
        )
        if not has_email:
            message += (
                "\n\nPercebi que você ainda não tem um e-mail cadastrado. "
                "Você pode cadastrar ou atualizar agora para receber notificações sobre sua solicitação."
            )
        message += "\n\nO que você gostaria de fazer?"

        buttons = [["Solicitar Visto", "Meu Status"]]
        if not has_email:
            buttons.insert(0, ["Atualizar e-mail"])

        await update.message.reply_text(
            message,
            reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True),
        )
        return SELECT_COUNTRY

    await update.message.reply_text(
        "Olá! Somos a YOUVISA, sua plataforma para simplificar o processo de vistos. "
        "Estamos aqui para auxiliar você em todo o processo.\n\n"
        "Para começar, por favor me diga seu nome completo."
    )
    return NAME

async def name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text
    await update.message.reply_text("Prazer em te conhecer! Agora, por favor digite seu CPF (apenas números).")
    return CPF

async def cpf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["cpf"] = update.message.text
    user = update.message.from_user
    await update.message.reply_text(
        "Digite seu e-mail para receber atualizações sobre sua solicitação (ou digite - para pular)."
    )
    return EMAIL

async def email_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    if email and email != "-":
        context.user_data["email"] = email
    else:
        context.user_data["email"] = None
    user = update.message.from_user
    uid = db.add_user(user.id, context.user_data["name"], context.user_data["cpf"], context.user_data.get("email"))
    if uid is None:
        existing = db.get_user(user.id)
        if existing and context.user_data.get("email"):
            db.update_user_email(user.id, context.user_data["email"])
    await update.message.reply_text("Cadastro concluído! Agora, vamos iniciar sua solicitação de visto.")
    return await list_countries(update, context)

async def list_countries(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    countries = db.get_countries()
    if not countries:
        await update.message.reply_text("Desculpe, não temos países configurados ainda. Por favor contate o administrador.")
        return ConversationHandler.END

    context.user_data['countries_cache'] = countries
    buttons = [[c['name']] for c in countries]
    country_list_text = "\n".join([f"- {c['name']}" for c in countries])
    await update.message.reply_text(
        "Por favor selecione o país para o qual deseja o visto:\n\n"
        "Países disponíveis:\n"
        f"{country_list_text}",
        reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True),
    )
    return SELECT_COUNTRY

async def select_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().lower()

    # Ações de menu principais
    if text == "meu status":
        db_user = db.get_user(update.message.from_user.id)
        if db_user:
            task = db.get_user_active_task(db_user['id'])
            if task:
                status = task['status'] if task['status'] else 'RECEBIDO'
                status_text = services.explain_status_simple(status)
                await update.message.reply_text(
                    f"**Status do seu processo:** {status_text}\n\nDestino: {task['country_name'] or 'N/A'}."
                )
            else:
                await update.message.reply_text("Você não possui uma solicitação ativa. Use 'Solicitar Visto' ou /start para iniciar.")
        else:
            await update.message.reply_text("Por favor, inicie o cadastro com /start.")
        return ConversationHandler.END

    if text == "solicitar visto":
        return await list_countries(update, context)

    # "Abrir novo processo" / "novo visto" etc.: levar direto para escolha de país (Solicitar Visto)
    try:
        intent = services.classify_intent(update.message.text)
        if intent == "abrir_novo_processo":
            return await list_countries(update, context)
    except Exception:
        pass

    if (
        text in ("atualizar e-mail", "atualizar email", "mudar e-mail", "mudar email", "alterar e-mail", "alterar email")
        or ("email" in text and ("atualizar" in text or "mudar" in text or "alterar" in text or "corrigir" in text))
    ):
        await update.message.reply_text(
            "Claro! Envie o novo e-mail que você quer cadastrar "
            "(ou digite - para deixar seu cadastro sem e-mail)."
        )
        return UPDATE_EMAIL

    countries = context.user_data.get('countries_cache') or db.get_countries()
    if countries:
        context.user_data['countries_cache'] = countries

    country = None
    if countries:
        for c in countries:
            name_lower = c['name'].lower()
            if text == name_lower:
                country = c
                break
            if name_lower in text or text in name_lower:
                country = c
                break
    if not country:
        country = db.get_country_by_name(update.message.text.strip())

    if not country:
        # Se não identificamos um país, tentar usar IA para entender a intenção
        # (status, próximos passos, documentos, etc.) antes de assumir país inválido.
        try:
            intent = services.classify_intent(update.message.text)
        except Exception:
            intent = "outro"

        if intent != "outro":
            # Monta contexto de usuário semelhante ao handler de chat livre.
            db_user = db.get_user(update.message.from_user.id)
            user_context = None
            if db_user:
                task = db.get_user_active_task(db_user['id'])
                if task:
                    uploaded_docs = db.get_task_documents(task['id'])
                    user_context = {
                        'active_task': {
                            'country_name': task['country_name'],
                            'required_docs': task['required_docs'],
                            'status': task['status'] or 'RECEBIDO',
                        },
                        'uploaded_docs': uploaded_docs,
                    }

            response = services.chat_with_bot(update.message.text, user_context)
            await update.message.reply_text(response)
            # Mantém o usuário no mesmo fluxo, com as ações ainda disponíveis.
            return SELECT_COUNTRY

        # Fallback: realmente não reconhecemos país nem intenção; mantém mensagem atual.
        countries = context.user_data.get('countries_cache') or db.get_countries()
        if countries:
            country_list_text = "\n".join([f"- {c['name']}" for c in countries])
            await update.message.reply_text(
                "Ainda não trabalhamos com esse país. Por favor escolha um da lista abaixo:\n\n"
                f"{country_list_text}"
            )
        else:
            await update.message.reply_text("Ainda não temos países configurados. Por favor contacte o administrador.")
        return await list_countries(update, context)

    user = update.message.from_user
    db_user = db.get_user(user.id)
    if not db_user:
        await update.message.reply_text("Erro: usuário não encontrado. Use /start para se cadastrar.")
        return ConversationHandler.END

    task_id = db.create_task(db_user['id'], country['id'])
    context.user_data['task_id'] = task_id
    context.user_data['required_docs'] = country['required_docs']

    await update.message.reply_text(
        f"Ótimo! Você está solicitando para {country['name']}.\n"
        f"Você precisa enviar os seguintes documentos: {country['required_docs']}.\n"
        "Por favor envie uma foto ou PDF de um dos documentos."
    )
    return UPLOAD_DOCS

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.message.from_user
    task_id = context.user_data.get('task_id')

    if not task_id:
        db_user = db.get_user(user.id)
        task = db.get_user_active_task(db_user['id']) if db_user else None
        if task:
            task_id = task['id']
            context.user_data['task_id'] = task_id
            context.user_data['required_docs'] = task['required_docs']
        else:
            await update.message.reply_text("Você não tem uma solicitação ativa. Digite /start para começar.")
            return ConversationHandler.END

    file = await update.message.effective_attachment[-1].get_file() if update.message.photo else await update.message.document.get_file()
    # Preservar extensão para PDF (extração de texto) e outras; foto usa .jpg
    if update.message.document and getattr(update.message.document, "file_name", None):
        ext = os.path.splitext(update.message.document.file_name)[1].lower() or ".jpg"
    else:
        ext = ".jpg"
    file_name = f"{task_id}_{file.file_unique_id}{ext}"
    file_bytes = await file.download_as_bytearray()

    saved_path = services.save_file(file_bytes, file_name, user.id)
    await update.message.reply_text("Analisando seu documento... Por favor aguarde.")

    doc_type = services.classify_document(saved_path, context.user_data['required_docs'])

    if doc_type == "UNKNOWN" or doc_type == "ERROR":
        await update.message.reply_text(
            "Não consegui identificar este documento como um dos necessários. "
            f"Por favor certifique-se que é um de: {context.user_data['required_docs']} e tente novamente."
        )
        return UPLOAD_DOCS

    # Extração de dados configurada para o tipo (falha silenciosa)
    extracted = services.extract_document_data(saved_path, doc_type)
    doc_id = db.add_document(task_id, doc_type, saved_path, extracted)

    # Notificar administrador (e-mail) que usuário X enviou documento Y
    admin_email = db.get_platform_setting("admin_email")
    db_user = db.get_user(user.id)
    user_name = (db_user and db_user["name"]) or user.first_name or "Usuário"
    if admin_email:
        notifications.notify_document_received(admin_email, user_name, doc_type, task_id)

    await update.message.reply_text(f"Documento recebido: {doc_type}!")

    uploaded_docs = db.get_task_documents(task_id)
    uploaded_types = set([d['doc_type'] for d in uploaded_docs])
    required_list = set([d.strip() for d in context.user_data['required_docs'].split(',')])
    missing = required_list - uploaded_types

    if not missing:
        db.update_task_status(task_id, "EM_ANALISE")
        await update.message.reply_text(
            "Parabéns! Recebemos todos os seus documentos. "
            "Sua solicitação está em análise. Você receberá atualizações por e-mail quando houver mudança de status."
        )
        # Notificar usuário por e-mail sobre mudança para EM_ANALISE
        user_email = (db_user and db_user["email"]) or None
        if user_email:
            notifications.notify_status_change(
                user_email, "Solicitação", "RECEBIDO", "EM_ANALISE",
                "Todos os documentos foram recebidos e sua solicitação entrou em análise."
            )
        return ConversationHandler.END
    else:
        await update.message.reply_text(f"Ainda falta: {', '.join(missing)}")

    return UPLOAD_DOCS

async def update_email_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    user = update.message.from_user
    db_user = db.get_user(user.id)

    if not db_user:
        await update.message.reply_text("Não encontrei seu cadastro. Use /start para começar.")
        return ConversationHandler.END

    new_email = None
    if email and email != "-":
        new_email = email

    if new_email:
        db.update_user_email(user.id, new_email)
        await update.message.reply_text("Pronto, seu e-mail foi atualizado com sucesso.")
    else:
        await update.message.reply_text("Ok, vamos manter seu cadastro sem e-mail por enquanto.")

    refreshed_user = db.get_user(user.id)
    has_email = bool(refreshed_user and "email" in refreshed_user.keys() and refreshed_user["email"])

    buttons = [["Solicitar Visto", "Meu Status"]]
    if not has_email:
        buttons.insert(0, ["Atualizar e-mail"])

    await update.message.reply_text(
        "O que você gostaria de fazer agora?",
        reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True),
    )

    return SELECT_COUNTRY

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operação cancelada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def reenter_flow_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Quando o usuário está fora do fluxo (ex.: após concluir uma solicitação) e diz
    'solicitar visto' ou 'meu status', entra direto no fluxo em vez de pedir para usar o menu.
    """
    text = (update.message.text or "").strip().lower()
    user = update.message.from_user
    db_user = db.get_user(user.id)
    intent = None
    try:
        intent = services.classify_intent(update.message.text)
    except Exception:
        pass

    if "meu status" in text or intent == "status_processo":
        if db_user:
            task = db.get_user_active_task(db_user["id"])
            if task:
                status = task["status"] if task["status"] else "RECEBIDO"
                status_text = services.explain_status_simple(status)
                await update.message.reply_text(
                    f"**Status do seu processo:** {status_text}\n\nDestino: {task['country_name'] or 'N/A'}."
                )
            else:
                await update.message.reply_text(
                    "Você não possui uma solicitação ativa. Vou abrir o menu para você solicitar um novo visto."
                )
                return await list_countries(update, context)
        else:
            await update.message.reply_text("Por favor, inicie o cadastro com /start.")
        return ConversationHandler.END

    # "Solicitar visto" ou intenção abrir_novo_processo → entrar direto na escolha de país
    return await list_countries(update, context)


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.message.from_user
    db_user = db.get_user(user.id)

    user_context = None
    if db_user:
        task = db.get_user_active_task(db_user['id'])
        if task:
            uploaded_docs = db.get_task_documents(task['id'])
            user_context = {
                'active_task': {
                    'country_name': task['country_name'],
                    'required_docs': task['required_docs'],
                    'status': task['status'] or 'RECEBIDO',
                },
                'uploaded_docs': uploaded_docs,
            }

    response = services.chat_with_bot(update.message.text, user_context)
    await update.message.reply_text(response)

def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("Error: TELEGRAM_TOKEN not found in environment variables.")
        return

    application = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)],
            CPF: [MessageHandler(filters.TEXT & ~filters.COMMAND, cpf)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_step)],
            SELECT_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_country)],
            UPLOAD_DOCS: [
                MessageHandler(filters.PHOTO | filters.Document.PDF | filters.Document.IMAGE, handle_document)
            ],
            UPDATE_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_email_step)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(
                filters.TEXT & filters.Regex(r"(?i)solicitar\s*visto|meu\s*status"),
                reenter_flow_fallback,
            ),
        ],
    )

    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
