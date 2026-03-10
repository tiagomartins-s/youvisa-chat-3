import os
import json
import base64
from dotenv import load_dotenv
from openai import OpenAI
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import database as db

load_dotenv()

client = None

def get_client():
    global client
    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is not set. "
                "Please set it before using OpenAI services."
            )
        client = OpenAI(api_key=api_key)
    return client

STORAGE_DIR = "storage"
if not os.path.exists(STORAGE_DIR):
    os.makedirs(STORAGE_DIR)

def save_file(file_bytes, file_name, user_id):
    user_dir = os.path.join(STORAGE_DIR, str(user_id))
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)
    file_path = os.path.join(user_dir, file_name)
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    return file_path

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def _is_pdf(file_path):
    """Indica se o arquivo é PDF (extensão ou magic bytes)."""
    if (file_path or "").lower().endswith(".pdf"):
        return True
    try:
        with open(file_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except Exception:
        return False


def _extract_text_from_pdf(file_path):
    """Extrai texto de PDF com PyPDF2. Retorna string ou ''."""
    try:
        import PyPDF2
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            parts = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    parts.append(t)
            return "\n".join(parts) if parts else ""
    except Exception as e:
        print(f"PDF extraction error: {e}")
        return ""

def classify_document(file_path, required_docs):
    base64_image = encode_image(file_path)
    prompt = f"""
    Você é um classificador de documentos para um sistema de vistos.
    Os documentos necessários são: {required_docs}.
    Analise a imagem fornecida. Ela se parece com um dos documentos necessários?
    Se sim, retorne APENAS o nome exato do tipo do documento da lista.
    Se não, ou se não estiver claro, retorne "UNKNOWN".
    """
    try:
        response = get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=300,
        )
        result = response.choices[0].message.content.strip()
        required_list = [d.strip() for d in required_docs.split(',')]
        if result in required_list:
            return result
        return "UNKNOWN"
    except Exception as e:
        print(f"Error calling OpenAI: {e}")
        return "ERROR"

def _image_mime_from_path(file_path):
    """Retorna 'image/png' ou 'image/jpeg' com base nos magic bytes."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if header[:2] in (b"\xff\xd8",):
            return "image/jpeg"
        if header[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        return "image/jpeg"
    except Exception:
        return "image/jpeg"


def extract_document_data(file_path, doc_type):
    """
    Extrai todo o texto visível do documento (imagem via visão ou PDF via PyPDF2).
    Retorna dict com chave "texto"; falha silenciosa retorna {"texto": ""}.
    """
    if not file_path or not os.path.isfile(file_path):
        return {"texto": ""}

    # PDF: extração direta com PyPDF2
    if _is_pdf(file_path):
        text = _extract_text_from_pdf(file_path)
        return {"texto": text or ""}

    # Imagem: visão (OpenAI)
    try:
        base64_image = encode_image(file_path)
        mime = _image_mime_from_path(file_path)
        prompt = """
    Analise o documento de imagem (pode ser foto de documento de identidade, carteira de motorista, passaporte, etc.).
    Extraia e transcreva TODO o texto que você conseguir identificar no documento, na ordem em que aparece.
    Preserve quebras de linha quando fizer sentido. Não invente dados que não estejam visíveis.
    Retorne em formato JSON com uma única chave "texto" contendo toda a transcrição.

    Responda somente com um JSON válido, sem texto adicional. Exemplo: {"texto": "Nome: João\\nCPF: 123.456.789-00\\n..."}
    """
        response = get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{base64_image}"},
                        },
                    ],
                }
            ],
            max_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        if isinstance(data, dict) and "texto" in data:
            return {"texto": (data["texto"] or "")}
        if isinstance(data, dict):
            return {"texto": json.dumps(data, ensure_ascii=False)}
        return {"texto": str(data) if data else ""}
    except Exception as e:
        print(f"Extraction error for {doc_type}: {e}")
        return {"texto": ""}

def explain_status_simple(status_technical):
    """
    Usa IA para traduzir status técnico em linguagem simples (guard rails: não inferir prazos/decisões).
    """
    status_map = {
        'RECEBIDO': 'Seu documento/solicitação foi recebido e está na fila para análise.',
        'EM_ANALISE': 'Sua solicitação está sendo analisada pela nossa equipe.',
        'PENDENTE': 'Estamos aguardando alguma informação ou ação. Você será contactado se precisar de algo.',
        'APROVADO': 'Este item foi aprovado.',
        'REPROVADO': 'Este documento não foi aprovado. Você pode enviar um novo ou entrar em contato.',
        'FINALIZADO': 'O processo foi concluído.',
    }
    # Resposta determinística primeiro (governança)
    if status_technical in status_map:
        return status_map[status_technical]
    # Fallback com IA limitada: apenas rephrasar, sem inferir prazos ou decisões
    try:
        response = get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """Você traduz status técnicos em uma frase curta e amigável.
NUNCA invente prazos, datas ou decisões finais. Use apenas o status fornecido.
Responda em uma única frase, em português."""},
                {"role": "user", "content": f"Traduza este status para o cliente: {status_technical}"}
            ],
            max_tokens=80,
        )
        return response.choices[0].message.content.strip() or status_technical
    except Exception:
        return status_technical

def classify_intent(user_message):
    """
    Classifica intenção: status_processo, documentos_faltando, proximo_passo,
    abrir_novo_processo, saudacao, outro.
    """
    m = (user_message or "").strip().lower()
    if not m:
        return "outro"
    # Palavras-chave determinísticas (guard rails)
    saudacao_keywords = ["opa", "olá", "ola", "oi", "bom dia", "boa tarde", "boa noite", "e aí", "eai", "hey", "fala"]
    novo_processo_keywords = [
        "novo processo", "abrir processo", "abrir um processo", "novo visto", "quero um visto",
        "solicitar visto", "solicitar um visto", "nova solicitação", "nova solicitacao",
        "quero solicitar", "abrir nova", "começar outro", "outro visto", "outro processo",
        "novo pedido", "novo pedido de visto", "quero abrir", "vamos abrir",
    ]
    status_keywords = ["status", "andamento", "situação", "situacao", "como está", "como esta", "qual o status", "meu processo", "minha solicitação", "minha solicitacao"]
    missing_keywords = ["falta", "faltando", "documento", "enviei tudo", "falta algum", "pendente"]
    next_keywords = [
        "próximo", "proximo",
        "próximo passo", "proximo passo",
        "o que fazer",
        "o que eu posso fazer",
        "o que posso fazer",
        "o que preciso",
        "agora",
        "depois",
    ]
    # Ordem importa: saudação e novo processo antes de status (evitar confusão com "meu processo")
    if any(k in m for k in saudacao_keywords) and len(m) < 50:
        return "saudacao"
    if any(k in m for k in novo_processo_keywords):
        return "abrir_novo_processo"
    if any(k in m for k in status_keywords):
        return "status_processo"
    if any(k in m for k in missing_keywords):
        return "documentos_faltando"
    if any(k in m for k in next_keywords):
        return "proximo_passo"
    try:
        response = get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """Classifique a intenção do usuário em UMA das opções exatas:
saudacao - cumprimento inicial (oi, olá, opa, bom dia, etc.)
abrir_novo_processo - quer abrir/iniciar nova solicitação de visto ou novo processo
status_processo - quer saber status/andamento do processo atual
documentos_faltando - quer saber se falta documento
proximo_passo - quer saber o que fazer a seguir
outro - qualquer outra coisa
Responda APENAS com uma dessas palavras, nada mais."""},
                {"role": "user", "content": user_message}
            ],
            max_tokens=25,
        )
        intent = response.choices[0].message.content.strip().lower()
        if intent in ("status_processo", "documentos_faltando", "proximo_passo", "abrir_novo_processo", "saudacao", "outro"):
            return intent
    except Exception:
        pass
    return "outro"

# Mensagem fixa de apresentação YOUVISA (saudação inicial)
YOUVISA_SAUDACAO = (
    "Olá! Somos a **YOUVISA**, sua plataforma para simplificar o processo de vistos. "
    "Estamos aqui para auxiliar você em todo o processo — desde a solicitação até o acompanhamento do status.\n\n"
    "Você pode:\n"
    "- **Solicitar Visto** — iniciar uma nova solicitação\n"
    "- **Meu Status** — consultar o andamento do seu processo\n"
    "- **Atualizar e-mail** — cadastrar ou alterar seu e-mail de contato\n\n"
    "O que você gostaria de fazer?"
)

# Resposta fixa para quem quer abrir novo processo (não confundir com status do processo atual)
YOUVISA_ABRIR_NOVO_PROCESSO = (
    "Para abrir uma nova solicitação de visto, use a opção **Solicitar Visto** no menu "
    "(ou digite \"Solicitar Visto\" aqui no chat). Em seguida, selecione o país de destino e envie os documentos solicitados. "
    "Se estiver no Telegram, você também pode usar /start e escolher \"Solicitar Visto\"."
)


def chat_with_bot(user_message, user_context=None):
    """
    Chat com classificação de intenção: status, documentos faltando, próximo passo,
    abrir novo processo, saudação. Usa mensagens pré-aprovadas (guard rails).
    O bot só informa o que a plataforma fornece e só sugere ações que a plataforma permite.
    """
    try:
        intent = classify_intent(user_message)

        # Intenções tratadas igualmente com ou sem processo ativo (resposta fixa)
        if intent == "saudacao":
            return YOUVISA_SAUDACAO

        if intent == "abrir_novo_processo":
            return YOUVISA_ABRIR_NOVO_PROCESSO

        if user_context and user_context.get('active_task'):
            task = user_context['active_task']
            country_name = task.get('country_name', 'o país selecionado')
            required_docs = task.get('required_docs', '') or ''
            uploaded_docs = user_context.get('uploaded_docs') or []
            task_status = task.get('status', 'RECEBIDO')

            uploaded_types = []
            for d in uploaded_docs:
                try:
                    if isinstance(d, dict):
                        value = d.get('doc_type', '')
                    else:
                        # sqlite3.Row e estruturas similares suportam acesso por chave
                        value = d['doc_type']
                    if value:
                        uploaded_types.append(str(value))
                except Exception:
                    continue

            required_list = [d.strip() for d in required_docs.split(',') if d.strip()]
            missing_docs = [doc for doc in required_list if doc not in uploaded_types]

            # Respostas determinísticas para intenções de status (governança)
            if intent == "status_processo":
                status_text = explain_status_simple(task_status)
                return f"**Status do seu processo:** {status_text}\n\nDestino: {country_name}."

            if intent == "documentos_faltando":
                if not missing_docs:
                    return "Você já enviou todos os documentos necessários para esta solicitação."
                return f"Ainda faltam os seguintes documentos: {', '.join(missing_docs)}. Por favor envie foto ou PDF de cada um pelo chat."

            if intent == "proximo_passo":
                if missing_docs:
                    return (
                        "O próximo passo é enviar os documentos que ainda faltam: "
                        f"{', '.join(missing_docs)}. Envie cada um por aqui (foto ou PDF).\n\n"
                        "Se tiver dúvidas sobre o andamento, você também pode perguntar \"qual o status?\" "
                        "ou tocar em \"Meu Status\" no menu."
                    )

                status_text = explain_status_simple(task_status)
                return (
                    "Todos os documentos obrigatórios desta solicitação já foram recebidos.\n"
                    f"{status_text}\n\n"
                    "A partir de agora, você pode:\n"
                    "- Acompanhar o andamento perguntando \"qual o status?\" ou tocando em \"Meu Status\".\n"
                    "- Abrir uma nova solicitação com \"Solicitar Visto\".\n"
                    "- Atualizar seu e-mail em \"Atualizar e-mail\", se precisar."
                )

            # Para "outro" ou dúvidas gerais: IA com contexto restrito. Só o que a plataforma fornece.
            system_prompt = f"""Você é o assistente da YOUVISA dentro desta plataforma.

Contexto que a plataforma fornece sobre o usuário:
- País do processo atual: {country_name}
- Documentos necessários: {required_docs}
- Já enviados: {', '.join(uploaded_types) if uploaded_types else 'Nenhum'}
- Faltando: {', '.join(missing_docs) if missing_docs else 'Nenhum'}
- Status do processo: {task_status}

Regras obrigatórias:
1. Responda APENAS com base nas informações acima. Não invente prazos, datas ou decisões que a plataforma não informou.
2. Não mencione consulado, embaixada nem órgãos externos como fonte de informação — você só tem acesso ao que esta plataforma fornece.
3. Não sugira "entrar em contato com suporte" para ações que o usuário pode fazer aqui (ex.: solicitar visto, enviar documentos, ver status). Oriente a usar "Solicitar Visto", "Meu Status" ou "Atualizar e-mail".
4. Para enviar documentos faltantes ou abrir nova solicitação, indique sempre a ação disponível na plataforma (ex.: "Solicitar Visto", envio pelo chat).
5. Seja breve e em português."""
        else:
            # Não há solicitação ativa: respostas determinísticas para intenções principais,
            # evitando depender da IA para o básico.
            if intent == "status_processo":
                return (
                    "Você ainda não tem uma solicitação ativa. "
                    "Use /start ou toque em \"Solicitar Visto\" para abrir uma nova solicitação."
                )

            if intent == "documentos_faltando":
                return (
                    "Ainda não há uma solicitação ativa. "
                    "Use /start ou \"Solicitar Visto\" para iniciar; depois você poderá enviar os documentos pelo chat."
                )

            if intent == "proximo_passo":
                return (
                    "Você pode:\n"
                    "- **Solicitar Visto** — iniciar uma nova solicitação\n"
                    "- **Meu Status** — ver o andamento de uma solicitação\n"
                    "- **Atualizar e-mail** — cadastrar ou alterar seu e-mail\n\n"
                    "Escolha uma opção no menu ou digite o nome da ação."
                )

            # Dúvidas gerais: só ações da plataforma, sem consulado/suporte externo
            system_prompt = """Você é o assistente da YOUVISA nesta plataforma.

O usuário não tem solicitação ativa ou está no menu. As únicas ações que ele pode fazer aqui são as que a plataforma oferece:
- **Solicitar Visto** — iniciar nova solicitação de visto (depois escolher país e enviar documentos)
- **Meu Status** — consultar andamento do processo
- **Atualizar e-mail** — cadastrar ou alterar e-mail

Regras obrigatórias:
1. Não invente informações que a plataforma não forneceu (prazos, taxas externas, formulários fora da plataforma).
2. Não mencione consulado, embaixada ou "fale com o suporte" para coisas que ele pode resolver aqui. Se ele pode solicitar visto ou ver status pela plataforma, oriente a usar essas opções.
3. Recomende sempre a ação exata disponível (ex.: "Solicitar Visto", "Meu Status") citando o texto do botão/opção.
4. Seja breve e em português."""

        response = get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=300,
        )
        return response.choices[0].message.content
    except Exception as e:
        return "Desculpe, estou com problemas técnicos. Tente novamente ou use /start para reiniciar."
