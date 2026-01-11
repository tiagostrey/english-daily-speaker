from gtts import gTTS
import re
import os
import telebot
import requests
import json
import base64
from dotenv import load_dotenv

# --- CONFIGURAÇÃO ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    print("ERRO: Verifique seu arquivo .env. Faltam chaves.")
    exit()

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Memória Global (O Caderno)
historico_usuarios = {}

# --- 1. AUTO-DESCOBERTA DE MODELO ---
def descobrir_melhor_modelo():
    print("🔍 Consultando modelos disponíveis...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GOOGLE_API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200: return "gemini-1.5-flash"
        
        dados = response.json()
        nomes = [m['name'].replace('models/', '') for m in dados.get('models', [])]
        
        # Prioridades (2.5 é o melhor atual)
        preferencias = ["gemini-2.5-flash", "gemini-1.5-pro", "gemini-1.5-flash"]
        for pref in preferencias:
            if pref in nomes:
                return pref
        
        # Fallback genérico
        for nome in nomes:
            if 'flash' in nome and '2.0' not in nome: return nome
            
        return "gemini-1.5-flash"
    except:
        return "gemini-1.5-flash"

NOME_MODELO = descobrir_melhor_modelo()
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{NOME_MODELO}:generateContent?key={GOOGLE_API_KEY}"
print(f"🔥 Bot Pronto! Usando motor: {NOME_MODELO}")

# --- 2. CÉREBRO (IA) COM MEMÓRIA ---
def falar_com_google(user_id, entrada, tipo="texto", modo="tutor"):
    headers = {'Content-Type': 'application/json'}
    
    # 1. Definição da Personalidade (Prompt)
    if modo == "tutor":
        txt_instrucao = """
        You are 'Daily Speaker', an English Tutor.
        
        CRITICAL RULES:
        1. **Memory:** You remember everything the user says. Use the conversation history!
        2. **Analysis:** Correct the grammar/spelling of the user's input.
        3. **Interaction:** If the user asks a question (like "what is my name?"), ANSWER IT in the 'Practice' section.
        
        OUTPUT FORMAT:
        📊 **Score: [0-100]**
        📝 **Correction:** (Strike errors ~~like this~~ and **bold** corrections)
        💡 **Tip:** (Short tip in Portuguese)
        🗣️ **Practice/Chat:** (Answer the user's question OR ask a follow-up question to keep the chat going)
        """
    elif modo == "simplificador":
        txt_instrucao = "You are a Text Simplifier. Rewrite in simple A2 English. No analysis."

    # 2. Inicializa o histórico do usuário se não existir
    if user_id not in historico_usuarios:
        historico_usuarios[user_id] = [
            {"role": "user", "parts": [{"text": txt_instrucao}]},
            {"role": "model", "parts": [{"text": "Understood. I am ready."}]}
        ]

    # 3. Prepara a nova mensagem do usuário
    nova_parte = {}
    if tipo == "texto":
        nova_parte = {"text": entrada}
    elif tipo == "audio":
        nova_parte = {
            "inline_data": {
                "mime_type": "audio/ogg",
                "data": entrada
            }
        }
    
    msg_usuario = {"role": "user", "parts": [nova_parte]}

    # 4. Monta o pacote de envio (Histórico + Mensagem Atual)
    # Enviamos uma cópia das últimas 20 mensagens para não ficar pesado
    contexto_envio = historico_usuarios[user_id][-20:] + [msg_usuario]

    payload = {"contents": contexto_envio}

    try:
        response = requests.post(GEMINI_URL, headers=headers, data=json.dumps(payload))
        
        if response.status_code != 200:
            return f"Erro Google ({response.status_code}): {response.text}"
        
        # 5. Processa a resposta
        resposta_ia = response.json()['candidates'][0]['content']['parts'][0]['text']
        
        # 6. Atualiza a Memória (Salva o par Pergunta/Resposta)
        historico_usuarios[user_id].append(msg_usuario)
        historico_usuarios[user_id].append({"role": "model", "parts": [{"text": resposta_ia}]})

        return resposta_ia

    except Exception as e:
        return f"Erro de conexão: {e}"

# --- FUNÇÃO AUXILIAR: TEXTO PARA ÁUDIO ---
def enviar_audio_resposta(chat_id, texto_markdown):
    try:
        # 1. DEFINIR O ALVO (REGEX)
        # r'' -> Indica string bruta (raw) para regex
        # \*\* -> Procura dois asteriscos literais
        # Practice.*: -> Procura a palavra Practice seguida de qualquer coisa (ex: /Chat) e dois pontos
        # (.*) -> O GRUPO DE CAPTURA. Pega tudo o que vier depois disso até o fim.
        # re.DOTALL -> Permite que o ponto (.) pegue também quebras de linha
        padrao = r'\*\*Practice.*:\*\*(.*)'
        
        match = re.search(padrao, texto_markdown, re.DOTALL)

        # 2. VERIFICAR SE ACHOU
        if match:
            # group(1) pega apenas o conteúdo capturado dentro dos parênteses (.*)
            # ou seja, ignora o título "**Practice:**" e pega só a fala.
            texto_para_falar = match.group(1)
        else:
            # Se não achar o padrão (ex: erro no prompt), não fala nada para não falar besteira.
            print("⚠️ Não encontrei o trecho de Practice para ler.")
            return

        # 3. LIMPEZA (A VASSOURA)
        # Remove markdown (*, _, ~)
        texto_limpo = re.sub(r'[*_~]', '', texto_para_falar)
        
        # Remove emojis e caracteres estranhos (Deixa apenas letras, números e pontuação básica)
        # [^\w\s,.:;?!] -> Significa "Tudo que NÃO for letra, espaço ou pontuação"
        texto_limpo = re.sub(r'[^\w\s,.:;?!\'"]', '', texto_limpo)
        
        # Remove espaços extras que sobraram
        texto_limpo = texto_limpo.strip()

        print(f"🗣️ Falando apenas: {texto_limpo}")

        # 4. GERAR O ÁUDIO (Se sobrou algum texto)
        if texto_limpo:
            tts = gTTS(text=texto_limpo, lang='en', slow=False)
            nome_arquivo = f"audio_{chat_id}.ogg"
            tts.save(nome_arquivo)
            
            with open(nome_arquivo, 'rb') as audio:
                bot.send_voice(chat_id, audio)
            
            os.remove(nome_arquivo)

    except Exception as e:
        print(f"Erro no áudio: {e}")

# --- 3. TELEGRAM HANDLERS ---

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "Hello! 🎧 I can now hear you.\nSend a voice message or text!")

# Handler para o comando 'simplificar'
@bot.message_handler(commands=['simplificar'])
def comando_simplificar(message):
    bot.reply_to(message, "Envie o texto em inglês que você quer simplificar.")
    bot.register_next_step_handler(message, processar_simplificacao)

def processar_simplificacao(message):
    bot.send_chat_action(message.chat.id, 'typing')
    texto_original = message.text
    resposta = falar_com_google(message.from_user.id, texto_original, tipo="texto", modo="simplificador")
    bot.reply_to(message, f"🔄 **Texto Simplificado:**\n\n{resposta}", parse_mode="Markdown")

# Handler de ÁUDIO (Voz)
@bot.message_handler(content_types=['voice'])
def receber_audio(message):
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        # 1. Baixar o arquivo do Telegram
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # 2. Converter para Base64 (formato que a API aceita)
        audio_b64 = base64.b64encode(downloaded_file).decode('utf-8')
        
        # 3. Enviar para IA
        resposta = falar_com_google(message.from_user.id, audio_b64, tipo="audio")
        
        try: bot.reply_to(message, resposta, parse_mode="Markdown")
        except: bot.reply_to(message, resposta)

        # NOVO: Envia o áudio também!
        enviar_audio_resposta(message.chat.id, resposta)
            
    except Exception as e:
        bot.reply_to(message, f"Erro ao processar áudio: {e}")

# Handler para limpeza da memória
@bot.message_handler(commands=['reset'])
def resetar_memoria(message):
    user_id = message.from_user.id
    if user_id in historico_usuarios:
        del historico_usuarios[user_id] # Apaga o histórico
        bot.reply_to(message, "🧠 Memória apagada! Começamos do zero.")
    else:
        bot.reply_to(message, "Já estamos no zero!")

# Handler de TEXTO
@bot.message_handler(func=lambda m: True)
def receber_texto(message):
    bot.send_chat_action(message.chat.id, 'typing')
    resposta = falar_com_google(message.from_user.id, message.text, tipo="texto")
    try: bot.reply_to(message, resposta, parse_mode="Markdown")
    except: bot.reply_to(message, resposta)

    # NOVO: Envia o áudio também!
    enviar_audio_resposta(message.chat.id, resposta)

if __name__ == "__main__":
    bot.infinity_polling()