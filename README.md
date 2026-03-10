# YOUVISA - Plataforma Inteligente de Atendimento (Sprint 3)

Sistema de acompanhamento de processos de visto com chatbot, gestão de status, notificações por e-mail, extração de dados em documentos e API de status.

---

## 1. Funcionalidades (Sprint 3)

- **Status de processo e documento**: Máquina de estados com transições válidas (ex.: RECEBIDO → EM_ANALISE → APROVADO/FINALIZADO). Persistência e histórico auditável.
- **Chatbot**: Responde "qual o status?", "falta algum documento?", "qual o próximo passo?" com classificação de intenção e respostas governadas (sem inventar prazos/decisões).
- **Notificações**: E-mail ao administrador quando um documento é recebido; e-mail ao usuário quando o status do processo ou do documento muda (configuração de e-mail na plataforma e no cadastro do usuário).
- **Extração de dados**: Por tipo de documento (ex.: Carteira de Motorista → CPF), configurável no painel; dados extraídos pela IA visíveis e editáveis pelo gestor; falha na extração não gera erro.
- **Painel administrativo**: Gestão de status (processo e por documento), visualização/edição de dados extraídos, configuração de tipos de documento e e-mail da plataforma, linha do tempo do processo.
- **API de status**: Endpoints REST para consulta de status por `telegram_id` ou `task_id` e histórico de transições.

---

## 2. Tecnologias

| Camada | Tecnologias |
|--------|-------------|
| Chatbot | `python-telegram-bot` |
| Backend / API | `FastAPI`, `uvicorn` |
| IA | `OpenAI GPT-4o` (classificação, extração, explicação de status) |
| Persistência | SQLite (`database/`) |
| Painel | Streamlit, pandas |
| Notificações | SMTP (stdlib) |

---

## 3. Variáveis de ambiente (.env)

```env
TELEGRAM_TOKEN=seu_token_telegram
OPENAI_API_KEY=sua_chave_openai

# Opcional: notificações por e-mail
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=seu_email@gmail.com
SMTP_PASSWORD=sua_senha_app
SMTP_FROM=youvisa@exemplo.com
SMTP_USE_TLS=true
```

O e-mail do administrador (quem recebe aviso de novo documento) é configurado no **Painel → Configuração → E-mail da plataforma**.

---

## 4. Execução

1. **Ambiente**
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Banco de dados** (na raiz do projeto)
   ```bash
   python -c "import database; database.init_db()"
   ```

3. **Chatbot Telegram**
   ```bash
   python src/bot.py
   ```

4. **Painel administrativo**
   ```bash
   streamlit run src/admin_app.py
   ```

5. **API de status** (opcional)
   ```bash
   uvicorn src.api:app --host 0.0.0.0 --port 8000
   ```
   - `GET /api/status/telegram/{telegram_id}` – status da solicitação ativa do usuário  
   - `GET /api/status/task/{task_id}` – status de uma solicitação por ID  
   - `GET /api/history/{entity_type}/{entity_id}` – histórico de status (entity_type: `task` ou `document`)

---

## 5. Estrutura do projeto

```
youvisa/
├── database/
│   ├── __init__.py   # Schema, máquina de estados, CRUD
│   └── youvisa.db
├── src/
│   ├── admin_app.py  # Painel Streamlit (usuários, solicitações, status, config)
│   ├── api.py        # API REST de status
│   ├── bot.py        # Chatbot Telegram (fluxo + chat com intenções)
│   ├── notifications.py  # E-mails (documento recebido, mudança de status)
│   └── services.py   # IA (classificação, extração, explicação de status, intenções)
├── storage/          # Arquivos enviados (por telegram_id)
├── requirements.txt
└── README.md
```

---

## 6. Fluxo resumido

1. Usuário inicia no Telegram (`/start`), informa nome, CPF e e-mail (opcional).
2. Escolhe o país e envia documentos (foto/PDF). Cada documento é classificado e, se houver configuração, são extraídos campos (ex.: CPF).
3. Administrador recebe e-mail ao chegar novo documento; no painel altera status do processo e dos documentos e pode editar dados extraídos.
4. Usuário recebe e-mail quando o status muda e pode perguntar no chatbot: "qual o status?", "falta algum documento?", "o que fazer agora?".
5. Todas as transições de status ficam registradas para auditoria (API e painel).

Entrega alinhada aos requisitos da Sprint 3: status de processo, chatbot com intenções, notificações event-driven, IA para explicação em linguagem simples, extração configurável, interface de gestão e API de status com histórico.
