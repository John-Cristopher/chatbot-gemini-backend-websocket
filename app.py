# O monkey_patch DEVE ser a primeira coisa absoluta no arquivo, antes de qualquer outro import.
# Isso resolve os erros de "RuntimeError: Working outside of application context" nos logs.
import eventlet
eventlet.monkey_patch()

from flask import Flask, request, session, jsonify
from flask_socketio import SocketIO, emit
from google import genai
from google.genai import types
from dotenv import load_dotenv
from uuid import uuid4
import os
from config import INSTRUCAO_RAPIDA, INSTRUCAO_DETALHADA

# Carrega as variáveis ocultas do arquivo .env (como a chave da API do Gemini)
load_dotenv()

# Modelos e Instruções por modo
MODOS_CONFIG = {
    "rapido": {"model": "gemini-1.5-flash", "instruction": INSTRUCAO_RAPIDA},
    "detalhado": {"model": "gemini-1.5-flash", "instruction": INSTRUCAO_DETALHADA}
}
MODELO_PADRAO = "gemini-1.5-flash"

# Inicializa a conexão com a inteligência artificial do Google usando a chave da API
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Cria o nosso aplicativo web principal (o servidor)
app = Flask(__name__)

# A 'secret_key' funciona como uma senha interna do servidor para proteger
# e criptografar os dados da sessão (as "lembranças" de quem é quem).
app.secret_key = "ch@tb07"

# Inicializa o SocketIO. Usamos 'eventlet' como async_mode para melhor performance
# em produção e suporte total a WebSockets.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# Dicionário que funciona como a "memória temporária" do servidor.
# Ele guarda a conversa de cada aluno separadamente usando um ID único.
active_chats = {}


def get_user_chat(modo="detalhado"):
    """
    Função principal de gerenciamento de usuários.
    Ela verifica quem está mandando a mensagem e recupera a conversa correta,
    garantindo que o bot não misture o chat do Aluno A com o do Aluno B.
    """

    # Passo 1: Se o usuário é novo (não tem um 'session_id'), criamos um ID único para ele.
    # Usamos o 'uuid4' para gerar um código aleatório impossível de repetir.
    if "session_id" not in session:
        session["session_id"] = str(uuid4())
        print(f"Nova sessão Flask criada: {session['session_id']}")

    session_id = session["session_id"]
    # Criamos uma chave única que combina o ID do usuário e o modo escolhido
    chat_key = f"{session_id}_{modo}"
    config_modo = MODOS_CONFIG.get(modo, MODOS_CONFIG["detalhado"])

    # Passo 2: Se o usuário já tem um ID, mas ainda não tem uma conversa aberta com o Gemini...
    if chat_key not in active_chats:
        print(f"Criando novo chat Gemini ({modo}) para session_id: {session_id}")
        try:
            # ...nós criamos uma nova conversa e passamos as instruções (personalidade).
            chat_session = client.chats.create(
                model=config_modo["model"],
                config=types.GenerateContentConfig(system_instruction=config_modo["instruction"]),
            )
            active_chats[chat_key] = chat_session
            print(f"Novo chat Gemini ({modo}) criado e armazenado.")
        except Exception as e:
            app.logger.error(
                f"Erro ao criar chat Gemini para {session_id}: {e}", exc_info=True
            )
            raise  # Se der erro aqui, repassa para o sistema avisar que falhou

    # Retorna o histórico de mensagens exato daquele usuário.
    return active_chats[chat_key]


# Rota simples para verificar se o servidor está rodando.
# Ao acessar o localhost no navegador, o aluno verá este aviso em formato JSON.
@app.route("/")
def root():
    return jsonify({"api-websocket": "chatbot", "status": "ok"})


# ------------------------------------------------------------------
# EVENTOS SOCKET.IO (Onde a mágica do tempo real acontece)
# ------------------------------------------------------------------


@socketio.on("connect")
def handle_connect():
    """
    EVENTO: Disparado quando o Front-end se conecta.
    Podemos capturar o modo inicial enviado na conexão através dos argumentos.
    """
    print(f"Cliente conectado: {request.sid}")
    
    # Captura o modo enviado pelo front-end (padrão é detalhado se não enviar)
    auth_data = request.args if request.args else {}
    modo_inicial = auth_data.get("modo", "detalhado")

    try:
        # Inicializa o chat com o modo correto
        get_user_chat(modo=modo_inicial)
        user_session_id = session.get("session_id", "N/A")
        print(f"Sessão Flask para {request.sid} iniciada no modo: {modo_inicial}")

        emit(
            "status_conexao",
            {"data": f"Link estabelecido. Modo: {modo_inicial.upper()}", "session_id": user_session_id},
        )
    except Exception as e:
        app.logger.error(f"Erro no connect para {request.sid}: {e}", exc_info=True)
        emit("erro", {"erro": "Falha ao inicializar a sessão de chat no servidor."})


@socketio.on("enviar_mensagem")
def handle_enviar_mensagem(data):
    """
    EVENTO: Recebe a mensagem do usuário e o modo tático atual.
    """
    try:
        mensagem_usuario = data.get("mensagem")
        # Captura o modo que o usuário está usando no momento do envio
        modo_atual = data.get("modo", "detalhado") 
        
        app.logger.info(
            f"Mensagem [{modo_atual.upper()}] recebida de {session.get('session_id', request.sid)}: {mensagem_usuario}"
        )

        if not mensaje_usuario:
            emit("erro", {"erro": "Mensagem não pode ser vazia."})
            return

        # Puxa o histórico de conversa ESPECÍFICO deste modo
        user_chat = get_user_chat(modo=modo_atual)
        if user_chat is None:
            emit("erro", {"erro": "Sessão de chat não pôde ser estabelecida."})
            return

        # Comunicação com o Gemini
        resposta_gemini = user_chat.send_message(mensagem_usuario)

        resposta_texto = (
            resposta_gemini.text
            if hasattr(resposta_gemini, "text")
            else resposta_gemini.candidates[0].content.parts[0].text
        )

        emit(
            "nova_mensagem",
            {
                "remetente": "bot",
                "texto": resposta_texto,
                "session_id": session.get("session_id"),
            },
        )

    except Exception as e:
        app.logger.error(f"Erro ao processar mensagem: {e}", exc_info=True)
        emit("erro", {"erro": f"Ocorreu um erro no servidor: {str(e)}"})


@socketio.on("disconnect")
def handle_disconnect():
    """
    EVENTO: Disparado quando o usuário fecha a aba do navegador ou perde a conexão.
    """
    print(
        f"Cliente desconectado: {request.sid}, session_id: {session.get('session_id', 'N/A')}"
    )


# Inicia o servidor local. A porta padrão do Flask costuma ser a 5000.
if __name__ == "__main__":
    socketio.run(app, debug=False, host="0.0.0.0", port=5000)
