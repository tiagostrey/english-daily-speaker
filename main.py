import re
import os
import json
import base64
import asyncio
import requests
import telebot
import edge_tts
from dotenv import load_dotenv
from datetime import date

# ============================================================
# CONFIGURAÇÃO
# ============================================================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    print("ERRO: Verifique seu arquivo .env. Faltam chaves.")
    exit()

bot = telebot.TeleBot(TELEGRAM_TOKEN)

MEMORIA_PATH = "memoria_usuarios.json"

# Vozes edge-tts por idioma
VOZES_IDIOMA = {
    "ingles":    "en-US-ChristopherNeural",
    "espanhol":  "es-ES-AlvaroNeural",
    "frances":   "fr-FR-HenriNeural",
    "alemao":    "de-DE-ConradNeural",
    "italiano":  "it-IT-DiegoNeural",
    "japones":   "ja-JP-KeitaNeural",
    "portugues": "pt-BR-AntonioNeural",
}

IDIOMAS_DISPONIVEIS = ", ".join(VOZES_IDIOMA.keys())

# ============================================================
# MEMÓRIA DE LONGO PRAZO (JSON)
# ============================================================
def carregar_memoria() -> dict:
    if os.path.exists(MEMORIA_PATH):
        with open(MEMORIA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def salvar_memoria(memoria: dict):
    with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)

def get_perfil(user_id: str) -> dict:
    memoria = carregar_memoria()
    if user_id not in memoria:
        memoria[user_id] = {
            "nome": "",
            "idioma": "ingles",
            "nivel_estimado": "A1",
            "erros_recorrentes": [],
            "favoritos": {"musicas": [], "series": []},
            "ultima_palavra": None,
            "ultima_data_vocabulario": None,
            "historico": []
        }
        salvar_memoria(memoria)
    return memoria[user_id]

def salvar_perfil(user_id: str, perfil: dict):
    memoria = carregar_memoria()
    memoria[user_id] = perfil
    salvar_memoria(memoria)

# ============================================================
# AUTO-DESCOBERTA DE MODELO GEMINI
# ============================================================
def descobrir_melhor_modelo():
    print("🔍 Consultando modelos disponíveis...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GOOGLE_API_KEY}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return "gemini-1.5-flash"
        dados = response.json()
        nomes = [m['name'].replace('models/', '') for m in dados.get('models', [])]
        preferencias = ["gemini-2.5-flash", "gemini-1.5-pro", "gemini-1.5-flash"]
        for pref in preferencias:
            if pref in nomes:
                return pref
        for nome in nomes:
            if 'flash' in nome and '2.0' not in nome:
                return nome
        return "gemini-1.5-flash"
    except:
        return "gemini-1.5-flash"

NOME_MODELO = descobrir_melhor_modelo()
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{NOME_MODELO}:generateContent?key={GOOGLE_API_KEY}"
print(f"🔥 Bot Pronto! Usando motor: {NOME_MODELO}")

# ============================================================
# BUSCA DE LETRA DE MÚSICA
# ============================================================
def buscar_letra(artista: str, musica: str) -> str:
    """Busca letra via lyrics.ovh (API gratuita, sem chave)."""
    try:
        url = f"https://api.lyrics.ovh/v1/{requests.utils.quote(artista)}/{requests.utils.quote(musica)}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            letra = r.json().get("lyrics", "")
            # Limita a 1500 chars para não explodir o contexto
            return letra[:1500].strip()
        return ""
    except:
        return ""

# ============================================================
# CÉREBRO (IA)
# ============================================================
def chamar_gemini(conteudo_parts: list, historico: list, system_prompt: str) -> str:
    headers = {'Content-Type': 'application/json'}

    # Monta histórico com system prompt
    contexto = [
        {"role": "user",  "parts": [{"text": system_prompt}]},
        {"role": "model", "parts": [{"text": "Understood. Ready."}]}
    ] + historico[-20:] + [{"role": "user", "parts": conteudo_parts}]

    payload = {"contents": contexto}
    try:
        response = requests.post(GEMINI_URL, headers=headers, json=payload)
        if response.status_code != 200:
            return f"Erro Google ({response.status_code}): {response.text}"
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"Erro de conexão: {e}"


def montar_system_prompt(perfil: dict) -> str:
    idioma    = perfil.get("idioma", "ingles")
    nivel     = perfil.get("nivel_estimado", "A1")
    erros     = perfil.get("erros_recorrentes", [])
    erros_str = ", ".join(erros) if erros else "nenhum ainda"

    nome_idioma_map = {
        "ingles": "English", "espanhol": "Spanish", "frances": "French",
        "alemao": "German",  "italiano": "Italian", "japones": "Japanese",
        "portugues": "Portuguese"
    }
    lang = nome_idioma_map.get(idioma, "English")

    return f"""
You are 'Daily Speaker', a language tutor teaching {lang}.
Student level: {nivel}. Recurring errors to watch: {erros_str}.

CRITICAL RULES:
1. Adapt explanations to level {nivel}. Simple for beginners, richer for advanced.
2. Correct grammar and spelling. Strike errors ~~like this~~ and **bold** corrections.
3. After each interaction, silently estimate the student's level (A1-C2) and note recurring errors.
4. Always answer in Brazilian Portuguese for tips/explanations; use {lang} for practice sentences.

OUTPUT FORMAT (tutor mode):
📊 **Score: [0-100]**
📝 **Correction:** (mark errors and corrections)
💡 **Tip (PT-BR):** (short tip in Portuguese)
🗣️ **Practice/Chat:** (follow-up question or answer in {lang})
📈 **[LEVEL:{nivel}]** (silently update if changed, e.g. [LEVEL:B1])
"""


def falar_com_ia(user_id: str, entrada, tipo="texto", modo="tutor") -> str:
    perfil = get_perfil(user_id)
    historico = perfil.get("historico", [])

    if modo == "simplificador":
        system = "You are a Text Simplifier. Rewrite in simple A2 level. No analysis. Just the rewritten text."
        parts = [{"text": entrada}]
        return chamar_gemini(parts, [], system)

    if modo == "vocabulario":
        system = montar_system_prompt(perfil)
        parts = [{"text": entrada}]
        return chamar_gemini(parts, [], system)

    system = montar_system_prompt(perfil)

    if tipo == "texto":
        parts = [{"text": entrada}]
    elif tipo == "audio":
        parts = [{"inline_data": {"mime_type": "audio/ogg", "data": entrada}}]
    else:
        parts = [{"text": entrada}]

    resposta = chamar_gemini(parts, historico, system)

    # Atualiza histórico
    historico.append({"role": "user",  "parts": parts})
    historico.append({"role": "model", "parts": [{"text": resposta}]})
    perfil["historico"] = historico[-40:]  # Guarda últimas 40 mensagens

    # Detecta nível atualizado na resposta
    match_nivel = re.search(r'\[LEVEL:([A-C][1-2])\]', resposta)
    if match_nivel:
        novo_nivel = match_nivel.group(1)
        if novo_nivel != perfil.get("nivel_estimado"):
            perfil["nivel_estimado"] = novo_nivel
            print(f"📈 Nível atualizado para {novo_nivel} ({user_id})")

    salvar_perfil(user_id, perfil)
    return resposta

# ============================================================
# ÁUDIO (edge-tts)
# ============================================================
async def _gerar_audio(texto: str, voz: str, arquivo: str):
    communicate = edge_tts.Communicate(texto, voz)
    await communicate.save(arquivo)

def enviar_audio_resposta(chat_id: int, texto_markdown: str, idioma: str = "ingles"):
    try:
        padrao = r'\*\*Practice.*:\*\*(.*)'
        match = re.search(padrao, texto_markdown, re.DOTALL)
        if not match:
            return
        texto_para_falar = match.group(1)
        texto_limpo = re.sub(r'[\*\_~]', '', texto_para_falar)
        texto_limpo = re.sub(r'\[LEVEL:[A-C][1-2]\]', '', texto_limpo)
        texto_limpo = re.sub(r'[^\w\s,.:;?!\'"]', '', texto_limpo).strip()

        if not texto_limpo:
            return

        voz = VOZES_IDIOMA.get(idioma, "en-US-ChristopherNeural")
        arquivo = f"audio_{chat_id}.mp3"
        asyncio.run(_gerar_audio(texto_limpo, voz, arquivo))

        with open(arquivo, 'rb') as audio:
            bot.send_voice(chat_id, audio)
        os.remove(arquivo)
    except Exception as e:
        print(f"Erro no áudio: {e}")

# ============================================================
# TELEGRAM HANDLERS
# ============================================================

@bot.message_handler(commands=['start'])
def welcome(message):
    user_id = str(message.from_user.id)
    perfil = get_perfil(user_id)
    perfil["nome"] = message.from_user.first_name
    salvar_perfil(user_id, perfil)

    texto = (
        f"Hello, {message.from_user.first_name}! I am *Daily Speaker*, your AI language tutor. 🌍\n\n"
        "📚 *Commands:*\n"
        "• Send *text or audio* to practice\n"
        "• `/musica Artista - Música` — learn from song lyrics\n"
        "• `/serie Nome da Série` — learn from TV show dialogues\n"
        "• `/vocabulario` — word/expression of the day\n"
        "• `/meunivel` — see your current estimated level\n"
        "• `/favoritos` — see your saved songs & series\n"
        "• `/idioma [nome]` — change language "
        f"_(options: {IDIOMAS_DISPONIVEIS})_\n"
        "• `/simplificar` — rewrite complex text simply\n"
        "• `/reset` — clear session history\n\n"
        "Let's go! Send me something. 🚀"
    )
    bot.reply_to(message, texto, parse_mode="Markdown")


@bot.message_handler(commands=['idioma'])
def cmd_idioma(message):
    user_id = str(message.from_user.id)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, f"Use: `/idioma [nome]`\nOpções: {IDIOMAS_DISPONIVEIS}", parse_mode="Markdown")
        return
    idioma = args[1].strip().lower()
    if idioma not in VOZES_IDIOMA:
        bot.reply_to(message, f"Idioma não reconhecido. Opções: {IDIOMAS_DISPONIVEIS}")
        return
    perfil = get_perfil(user_id)
    perfil["idioma"] = idioma
    perfil["historico"] = []  # Reset histórico ao trocar idioma
    salvar_perfil(user_id, perfil)
    bot.reply_to(message, f"✅ Idioma alterado para *{idioma}*! Histórico resetado.", parse_mode="Markdown")


@bot.message_handler(commands=['meunivel'])
def cmd_meu_nivel(message):
    user_id = str(message.from_user.id)
    perfil = get_perfil(user_id)
    nivel = perfil.get("nivel_estimado", "A1")
    erros = perfil.get("erros_recorrentes", [])
    idioma = perfil.get("idioma", "ingles")

    erros_str = "\n".join(f"• {e}" for e in erros) if erros else "• Nenhum registrado ainda"
    texto = (
        f"📊 *Seu perfil de aprendizado:*\n\n"
        f"🌍 Idioma: *{idioma}*\n"
        f"🎯 Nível estimado: *{nivel}*\n\n"
        f"⚠️ *Erros recorrentes:*\n{erros_str}"
    )
    bot.reply_to(message, texto, parse_mode="Markdown")


@bot.message_handler(commands=['musica'])
def cmd_musica(message):
    user_id = str(message.from_user.id)
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or "-" not in args[1]:
        bot.reply_to(message, "Use: `/musica Artista - Nome da Música`\nExemplo: `/musica The Beatles - Hey Jude`", parse_mode="Markdown")
        return

    partes = args[1].split("-", 1)
    artista = partes[0].strip()
    musica  = partes[1].strip()

    bot.send_chat_action(message.chat.id, 'typing')
    bot.reply_to(message, f"🎵 Buscando letra de *{musica}* — *{artista}*...", parse_mode="Markdown")

    letra = buscar_letra(artista, musica)

    perfil = get_perfil(user_id)
    idioma = perfil.get("idioma", "ingles")

    if letra:
        # Salva nos favoritos
        fav = perfil.setdefault("favoritos", {"musicas": [], "series": []})
        entrada_fav = f"{artista} - {musica}"
        if entrada_fav not in fav["musicas"]:
            fav["musicas"].append(entrada_fav)
        salvar_perfil(user_id, perfil)

        prompt = (
            f"Use the following song lyrics excerpt from '{musica}' by {artista} to teach {idioma}.\n\n"
            f"LYRICS:\n{letra}\n\n"
            "Create a lesson with:\n"
            "🎵 **Song context** (1-2 sentences about the song)\n"
            "📖 **Key vocabulary** (5 words/expressions from the lyrics with translation to PT-BR)\n"
            "🗣️ **Idioms & expressions** (highlight any informal/idiomatic language)\n"
            "✍️ **Practice** (ask the student to write a sentence using one of the words)\n"
            "🔊 **Pronunciation tip** (one phonetic tip from the lyrics)"
        )
    else:
        # Letra não encontrada — usa o conhecimento do Gemini
        prompt = (
            f"I couldn't find the lyrics for '{musica}' by {artista}. "
            f"Use your knowledge of this song to teach {idioma}. "
            "If you know the song, use real lyrics excerpts. "
            "Create a lesson with vocabulary, expressions, and a practice exercise."
        )

    resposta = falar_com_ia(user_id, prompt, tipo="texto", modo="vocabulario")

    try:
        bot.reply_to(message, resposta, parse_mode="Markdown")
    except:
        bot.reply_to(message, resposta)

    enviar_audio_resposta(message.chat.id, resposta, idioma)


@bot.message_handler(commands=['serie'])
def cmd_serie(message):
    user_id = str(message.from_user.id)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Use: `/serie Nome da Série`\nExemplo: `/serie Breaking Bad`\nOu com episódio: `/serie Friends S01E01`", parse_mode="Markdown")
        return

    nome_serie = args[1].strip()
    bot.send_chat_action(message.chat.id, 'typing')

    perfil = get_perfil(user_id)
    idioma = perfil.get("idioma", "ingles")
    nivel  = perfil.get("nivel_estimado", "A1")

    # Salva nos favoritos
    fav = perfil.setdefault("favoritos", {"musicas": [], "series": []})
    if nome_serie not in fav["series"]:
        fav["series"].append(nome_serie)
    salvar_perfil(user_id, perfil)

    prompt = (
        f"Use real dialogues or scenes from the TV show '{nome_serie}' to teach {idioma}.\n"
        f"Student level: {nivel}. Choose scenes appropriate for this level.\n\n"
        "Create a lesson with:\n"
        "🎬 **Scene context** (describe the scene briefly in PT-BR)\n"
        "💬 **Dialogue excerpt** (2-4 lines from the show)\n"
        "📖 **Key vocabulary** (5 words/expressions with PT-BR translation)\n"
        "🗣️ **Cultural/language note** (slang, accent, or cultural reference)\n"
        "✍️ **Practice** (ask the student to rewrite one line or create a similar dialogue)"
    )

    resposta = falar_com_ia(user_id, prompt, tipo="texto", modo="vocabulario")

    try:
        bot.reply_to(message, resposta, parse_mode="Markdown")
    except:
        bot.reply_to(message, resposta)

    enviar_audio_resposta(message.chat.id, resposta, idioma)


@bot.message_handler(commands=['vocabulario'])
def cmd_vocabulario(message):
    user_id = str(message.from_user.id)
    perfil = get_perfil(user_id)
    idioma = perfil.get("idioma", "ingles")
    nivel  = perfil.get("nivel_estimado", "A1")
    hoje   = str(date.today())

    # Uma palavra por dia — não repete no mesmo dia
    if perfil.get("ultima_data_vocabulario") == hoje:
        bot.reply_to(message, "✅ Você já recebeu sua palavra do dia! Volte amanhã. 😊")
        return

    bot.send_chat_action(message.chat.id, 'typing')

    ultima = perfil.get("ultima_palavra", "")
    prompt = (
        f"Give me a new {idioma} word or expression appropriate for level {nivel}. "
        f"Not the same as the last one: '{ultima}'.\n\n"
        "Format:\n"
        "📚 **Word/Expression:**\n"
        "🔤 **Pronunciation (IPA):**\n"
        "🇧🇷 **PT-BR Translation:**\n"
        "💡 **Example sentence:**\n"
        "🗣️ **Practice:** (ask the student to use the word in a sentence)"
    )

    resposta = falar_com_ia(user_id, prompt, tipo="texto", modo="vocabulario")

    # Extrai a palavra para não repetir
    match = re.search(r'\*\*Word/Expression:\*\*\s*(.+)', resposta)
    if match:
        perfil["ultima_palavra"] = match.group(1).strip()

    perfil["ultima_data_vocabulario"] = hoje
    salvar_perfil(user_id, perfil)

    try:
        bot.reply_to(message, resposta, parse_mode="Markdown")
    except:
        bot.reply_to(message, resposta)

    enviar_audio_resposta(message.chat.id, resposta, idioma)


@bot.message_handler(commands=['favoritos'])
def cmd_favoritos(message):
    user_id = str(message.from_user.id)
    perfil = get_perfil(user_id)
    fav = perfil.get("favoritos", {"musicas": [], "series": []})

    musicas = "\n".join(f"• {m}" for m in fav["musicas"]) or "• Nenhuma ainda"
    series  = "\n".join(f"• {s}" for s in fav["series"])  or "• Nenhuma ainda"

    texto = (
        f"⭐ *Seus favoritos:*\n\n"
        f"🎵 *Músicas:*\n{musicas}\n\n"
        f"🎬 *Séries:*\n{series}"
    )
    bot.reply_to(message, texto, parse_mode="Markdown")


@bot.message_handler(commands=['simplificar'])
def cmd_simplificar(message):
    bot.reply_to(message, "Envie o texto em inglês que você quer simplificar.")
    bot.register_next_step_handler(message, processar_simplificacao)

def processar_simplificacao(message):
    bot.send_chat_action(message.chat.id, 'typing')
    resposta = falar_com_ia(str(message.from_user.id), message.text, tipo="texto", modo="simplificador")
    try:
        bot.reply_to(message, f"🔄 *Texto Simplificado:*\n\n{resposta}", parse_mode="Markdown")
    except:
        bot.reply_to(message, resposta)


@bot.message_handler(commands=['reset'])
def cmd_reset(message):
    user_id = str(message.from_user.id)
    perfil = get_perfil(user_id)
    perfil["historico"] = []
    salvar_perfil(user_id, perfil)
    bot.reply_to(message, "🧠 Histórico da conversa apagado! Contexto de longo prazo (nível, favoritos) mantido.")


@bot.message_handler(content_types=['voice'])
def receber_audio(message):
    user_id = str(message.from_user.id)
    bot.send_chat_action(message.chat.id, 'typing')
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        audio_b64 = base64.b64encode(downloaded_file).decode('utf-8')
        resposta = falar_com_ia(user_id, audio_b64, tipo="audio")
        try:
            bot.reply_to(message, resposta, parse_mode="Markdown")
        except:
            bot.reply_to(message, resposta)
        perfil = get_perfil(user_id)
        enviar_audio_resposta(message.chat.id, resposta, perfil.get("idioma", "ingles"))
    except Exception as e:
        bot.reply_to(message, f"Erro ao processar áudio: {e}")


@bot.message_handler(func=lambda m: True)
def receber_texto(message):
    user_id = str(message.from_user.id)
    print(f'📩 {message.from_user.first_name}: {message.text}')

    perfil = get_perfil(user_id)
    if not perfil.get("nome"):
        perfil["nome"] = message.from_user.first_name
        salvar_perfil(user_id, perfil)
        welcome(message)

    bot.send_chat_action(message.chat.id, 'typing')
    resposta = falar_com_ia(user_id, message.text, tipo="texto")

    try:
        bot.reply_to(message, resposta, parse_mode="Markdown")
    except:
        bot.reply_to(message, resposta)

    enviar_audio_resposta(message.chat.id, resposta, perfil.get("idioma", "ingles"))


# ============================================================
if __name__ == "__main__":
    print("🤖 Daily Speaker iniciado.")
    bot.infinity_polling()
