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

# --- 2. CÉREBRO (IA) ---
def falar_com_google(entrada, tipo="texto", modo="tutor"):
    headers = {'Content-Type': 'application/json'}
    
    # --- SELEÇÃO DE PERSONALIDADE ---
    if modo == "tutor":
        # Modo Padrão: Corrige, dá nota e formata bonito
        prompt_sistema = """
        You are 'Daily Speaker', an English Tutor Bot.
        TASK: Analyze the user's input (Text or Audio).
        OUTPUT FORMAT (Strict markdown):
        📊 **Score: [0-100]/100**
        [One sentence comment]
        📝 **Correction:**
        "[Full sentence with ~~errors~~ and **corrections**]"
        💡 **Dica:**
        [Tip in Portuguese]
        🗣️ **Practice:**
        [Question in English]
        """
    elif modo == "simplificador":
        # Novo Modo: Apenas reescreve simples
        prompt_sistema = """
        You are an expert Text Simplifier.
        TASK: Rewrite the user's text into clear, simple English (Level A2).
        RULES:
        1. Use short sentences.
        2. Use easy vocabulary.
        3. Do NOT give feedback or scores. Just the text.
        """

    # --- MONTAGEM DO PACOTE (Igual para todos) ---
    conteudo_usuario = []
    
    if tipo == "texto":
        conteudo_usuario.append({"text": f"Input: {entrada}"})
    elif tipo == "audio":
        conteudo_usuario.append({
            "inline_data": {
                "mime_type": "audio/ogg",
                "data": entrada
            }
        })
        conteudo_usuario.append({"text": "Listen to this."})

    payload = {
        "contents": [{
            "parts": [{"text": prompt_sistema}] + conteudo_usuario
        }]
    }

    try:
        response = requests.post(GEMINI_URL, headers=headers, data=json.dumps(payload))
        if response.status_code != 200:
            return f"Erro Google ({response.status_code}): {response.text}"
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"Erro de conexão: {e}"

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
    resposta = falar_com_google(texto_original, tipo="texto", modo="simplificador")
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
        resposta = falar_com_google(audio_b64, tipo="audio")
        
        try: bot.reply_to(message, resposta, parse_mode="Markdown")
        except: bot.reply_to(message, resposta)
            
    except Exception as e:
        bot.reply_to(message, f"Erro ao processar áudio: {e}")

# Handler de TEXTO
@bot.message_handler(func=lambda m: True)
def receber_texto(message):
    bot.send_chat_action(message.chat.id, 'typing')
    resposta = falar_com_google(message.text, tipo="texto")
    try: bot.reply_to(message, resposta, parse_mode="Markdown")
    except: bot.reply_to(message, resposta)

if __name__ == "__main__":
    bot.infinity_polling()