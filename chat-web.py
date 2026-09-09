import datetime
import time
import re
import sqlite3
import threading
import uuid
from pathlib import Path

import streamlit as st
from mistralai.client import Mistral

from htbuilder.units import rem
from htbuilder import div, styles

# -----------------------------------------------------------------------------
# Configure Streamlit

st.set_page_config(
    page_title="Chatbot lifelong-learning.lu", 
    page_icon="🤖",
    initial_sidebar_state="collapsed"
)

# -----------------------------------------------------------------------------
# CONSTANTS

MODEL = "mistral-small-latest"
AGENT_MODEL = "mistral-large-latest"
MIN_TIME_BETWEEN_REQUESTS = datetime.timedelta(seconds=3)

# Nombre maximum de questions par jour et par adresse IP.
MAX_QUESTIONS_PER_DAY = 20

INSTRUCTIONS = (
    "Tu es un assistant expert en formation professionnelle continue exclusivement au Luxembourg. "
    "Utilises en priorité les informations fournies par les sites www.lifelong-learning.lu et www.infpc.lu. "
    "Réponds de manière claire, précise et utile. "
    "Ne fournis aucune réponse hors de ton domaine de compétence. "
    "Si une information est incertaine, indique-le et conseille de vérifier auprès des sources officielles."
)

SUGGESTIONS_FR = {
    "q1": (
        ":blue[:material/local_library:] C'est quoi la VAE ?"
    ),
    "q2": (
        ":green[:material/database:] Quelles aides pour me former en tant que particulier ?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] Comment devenir organisme de formation ?"
    ),
    "q4": (
        ":violet[:material/apparel:] Comment cofinancer les formations de mon entreprise ?"
    ),
    "q5": (
        ":red[:material/deployed_code:] Comment obtenir un diplôme ?"
    ),
}

SUGGESTIONS_EN = {
    "q1": (
        ":blue[:material/local_library:] What is VAE?"
    ),
    "q2": (
        ":green[:material/database:] What funding is available to train myself?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] How can I become a training provider?"
    ),
    "q4": (
        ":violet[:material/apparel:] How can companies co-finance training?"
    ),
    "q5": (
        ":red[:material/deployed_code:] How can I obtain a diploma?"
    ),
}

# -----------------------------------------------------------------------------
# Translations
translations = {
    "fr": {
        "title": "Discutez avec un expert en formation continue", 
        "ask": "Posez votre question",
        "new": "Nouveau Chat",
        "loading": "Veuillez patienter...",
        "searching": "Je recherche dans la documentation...",
        "download": "Télécharger le Chat",
        "disclaimer": "&nbsp;:small[:gray[:material/balance: Avertissement sur l'IA]]",
        "warning": "Avertissement",
        "language": "Choix de la langue",
        "quota_reached": (
            "Vous avez atteint le nombre maximum de "
            "**{limit} questions par jour**.\n\n"
            "Merci de revenir demain. En attendant, vous pouvez consulter "
            "directement [www.lifelong-learning.lu](https://www.lifelong-learning.lu) "
            "ou [www.infpc.lu](https://www.infpc.lu)."
        ),
        "quota_remaining": "Questions restantes aujourd'hui : {remaining} / {limit}",
    },
    "en": {
        "title": "Ask a lifelong learning expert", 
        "ask": "Ask your question",
        "new": "New Chat",
        "loading": "Please wait...",
        "searching": "Searching in my knowledge base...",
        "download": "Download Chat",        
        "disclaimer": "&nbsp;:small[:gray[:material/balance: AI Disclaimer]]",
        "warning": "Warning",
        "language": "Language choice",
        "quota_reached": (
            "You have reached the maximum of "
            "**{limit} questions per day**.\n\n"
            "Please come back tomorrow. In the meantime, you can browse "
            "[www.lifelong-learning.lu](https://www.lifelong-learning.lu) "
            "or [www.infpc.lu](https://www.infpc.lu)."
        ),
        "quota_remaining": "Questions left today: {remaining} / {limit}",
    }
}
if "lang" not in st.session_state:
    st.session_state.lang = "fr" 


suggestions = {}

# -----------------------------------------------------------------------------
# Create a new MISTRAL client
#
# @st.cache_resource : le client est créé UNE seule fois pour tout le processus
# et réutilisé par toutes les sessions, au lieu d'être recréé à chaque rerun.

@st.cache_resource
def get_mistral_client():
    return Mistral(api_key=st.secrets["MISTRAL_API_KEY"])


client = get_mistral_client()

# -----------------------------------------------------------------------------
# Quota journalier par adresse IP -- stocke dans SQLite
#
# Interet par rapport a un dict en memoire :
#   - les compteurs survivent au redemarrage de l'application ;
#   - aucun service supplementaire a installer, superviser ou securiser ;
#   - le fichier est inspectable a la main (sqlite3 quota.db "SELECT ...").
#
# Ce que SQLite ne fait PAS : coordonner plusieurs machines. Tant que vous avez
# un seul `streamlit run`, ce n'est pas un probleme.

# Chemin du fichier de base. Doit etre inscriptible par l'utilisateur qui lance
# Streamlit, et pointer vers un disque local (jamais un partage NFS).
QUOTA_DB_PATH = Path(__file__).parent / "quota.db"

# Comportement si la base est illisible (disque plein, droits, corruption) :
#   True  = on laisse passer la question (disponibilite privilegiee)
#   False = on bloque (maitrise des couts privilegiee)
FAIL_OPEN_IF_DB_DOWN = True


@st.cache_resource
def get_quota_db():
    """Connexion SQLite unique, partagee par toutes les sessions.

    check_same_thread=False est indispensable : Streamlit execute chaque session
    dans un thread different, et sqlite3 refuse par defaut qu'une connexion soit
    utilisee ailleurs que dans le thread qui l'a creee.
    """
    conn = sqlite3.connect(
        QUOTA_DB_PATH,
        check_same_thread=False,
        timeout=5,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quota (
            ip    TEXT    NOT NULL,
            day   TEXT    NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (ip, day)
        ) WITHOUT ROWID
        """
    )
    conn.commit()

    return {"conn": conn, "lock": threading.Lock(), "purged_on": None}


def get_client_ip() -> str:
    """Adresse IP du visiteur, lue dans les en-tetes du reverse proxy.

    Necessite un proxy (nginx, Traefik, Cloudflare...) qui positionne
    X-Forwarded-For. Sans proxy, ou si l'en-tete est absent, on retombe sur un
    identifiant propre a la session pour ne pas confondre tous les visiteurs.
    """
    try:
        headers = st.context.headers or {}
    except Exception:
        headers = {}

    forwarded = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For: <client>, <proxy1>, <proxy2>
        return forwarded.split(",")[0].strip()

    real_ip = headers.get("X-Real-Ip") or headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    if "fallback_client_id" not in st.session_state:
        st.session_state.fallback_client_id = f"session-{uuid.uuid4()}"
    return st.session_state.fallback_client_id


def _purge_old_days(store: dict, today: str) -> None:
    """Supprime les lignes des jours passes, une fois par jour au maximum."""
    if store["purged_on"] == today:
        return

    store["conn"].execute("DELETE FROM quota WHERE day < ?", (today,))
    store["conn"].commit()
    store["purged_on"] = today


def consume_question(ip: str) -> tuple[bool, int]:
    """Decompte une question. Retourne (autorise, questions restantes).

    Le verrou protege la sequence lire-puis-ecrire : sans lui, deux threads
    peuvent lire la meme valeur avant que l'un des deux ecrive.
    """
    store = get_quota_db()
    today = datetime.date.today().isoformat()

    try:
        with store["lock"]:
            conn = store["conn"]
            _purge_old_days(store, today)

            row = conn.execute(
                "SELECT count FROM quota WHERE ip = ? AND day = ?", (ip, today)
            ).fetchone()
            used = row[0] if row else 0

            if used >= MAX_QUESTIONS_PER_DAY:
                return False, 0

            conn.execute(
                """
                INSERT INTO quota (ip, day, count) VALUES (?, ?, 1)
                ON CONFLICT (ip, day) DO UPDATE SET count = count + 1
                """,
                (ip, today),
            )
            conn.commit()

        return True, MAX_QUESTIONS_PER_DAY - (used + 1)

    except sqlite3.Error:
        # Base indisponible : on n'empeche pas le chatbot de fonctionner.
        return FAIL_OPEN_IF_DB_DOWN, MAX_QUESTIONS_PER_DAY


def remaining_questions(ip: str) -> int:
    """Questions restantes, SANS decompter (pour l'affichage)."""
    store = get_quota_db()
    today = datetime.date.today().isoformat()

    try:
        with store["lock"]:
            row = store["conn"].execute(
                "SELECT count FROM quota WHERE ip = ? AND day = ?", (ip, today)
            ).fetchone()
    except sqlite3.Error:
        return MAX_QUESTIONS_PER_DAY if FAIL_OPEN_IF_DB_DOWN else 0

    used = row[0] if row else 0
    return max(0, MAX_QUESTIONS_PER_DAY - used)

# -----------------------------------------------------------------------------
# Show a disclaimer popup

@st.dialog(" ")
def show_disclaimer_dialog():
    if st.session_state.lang=="fr":
        st.caption("""
                # ⚠️ Avertissement sur l'IA
                Ce chatbot est propulsé par un modèle de langage basé 
                sur l'intelligence artificielle (IA). 
                Il génère des réponses automatiquement à partir d’un vaste 
                corpus de textes et d’exemples préalablement intégrés lors 
                de son entraînement. 
                Bien que conçu pour fournir des informations utiles, ce 
                système ne garantit pas l’exactitude, l’exhaustivité ni 
                l’actualité des réponses fournies.
                \n\n
                L'INFPC décline toute responsabilité quant à l’usage qui 
                pourrait être fait des réponses fournies. Il vous incombe 
                de vérifier, croiser ou faire valider toute information 
                critique par des sources fiables ou des experts compétents.
                \n\n
                Pour plus d'information, visitez notre site
                https://www.infpc.lu
            """)
    if st.session_state.lang=="en":
        st.caption("""
                # AI Disclaimer
                This chatbot is powered by an artificial intelligence (AI)-based language model. 
                It automatically generates responses based on a vast corpus of texts and examples 
                incorporated during its training. 
                While designed to provide useful information, this system does not guarantee 
                the accuracy, completeness, or currency of the responses provided.
                \n\n
                The INFPC accepts no liability for how the provided responses are used. 
                It is your responsibility to verify, cross-check, or have any critical 
                information validated by reliable sources or qualified experts.
                \n\n
                For more information, visit our website:
                https://www.infpc.lu
            """)

# ----------------------------------------------------------------------------
# Get avatar path based on role
def get_avatar(role: str) -> str:
    """Return the avatar image path based on the message role."""
    if role == "assistant":
        return "./images/avatar-bot.png"
    return "./images/avatar-user.png"


# ----------------------------------------------------------------------------
# Remove Streamlit Markdown
def remove_streamlit_markdown(text: str) -> str:

    # Supprime les icônes Material : :material/xxx:
    text = re.sub(r":material/[^:]+:", "", text)

    # Supprime les couleurs Streamlit : :blue[...], :red[...], etc.
    text = re.sub(
        r":(?:red|blue|green|orange|yellow|violet|gray|grey)\[([^\]]*)\]",
        r"\1",
        text,
    )

    return text.strip()

# -----------------------------------------------------------------------------
# Clear the current conversation
def clear_conversation():
    st.session_state.messages = []
    st.session_state.initial_question = None
    st.session_state.selected_suggestion = None
    st.session_state.conversation_export_text = ""
    # Le conversation_id Mistral est propre a un fil de discussion : on en
    # redemande un nouveau au prochain message plutot que de reutiliser
    # l'historique de la conversation precedente.
    st.session_state.conversation_id = None

def translate_suggestions():
    global suggestions

    if st.session_state.lang=="fr":
        suggestions = SUGGESTIONS_FR.copy()

    if st.session_state.lang=="en":
        suggestions = SUGGESTIONS_EN.copy()

# -----------------------------------------------------------------------------
# Export the current conversation into a string

def build_conversation_text():
    messages = st.session_state.get("messages", [])
    lines = []

    for message in messages:
        role = message.get("role", "")
        content = (message.get("content") or "").strip()

        if not content:
            continue

        if role == "user":
            lines.append(f"Question: {content}")
        elif role == "assistant":
            lines.append(f"Réponse: {content}")
        else:
            lines.append(f"{role.capitalize()}: {content}")

    return "\n\n".join(lines)


def refresh_conversation_export():
    st.session_state.conversation_export_text = build_conversation_text()


# -----------------------------------------------------------------------------
# Draw the UI

if "conversation_export_text" not in st.session_state:
    st.session_state.conversation_export_text = ""

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None

sidebar = st.sidebar

with sidebar:

    st.image("./images/lifelong-learning.svg", width=200)

    selected_lang = st.sidebar.selectbox(" ", 
        options=["fr", "en"],
        format_func=lambda x: {"en": "English", "fr": "Français"}[x]
    )
    st.session_state.lang = selected_lang

    translate_suggestions()
    t = translations[st.session_state.lang]

    st.button(
        t["new"],
        icon=":material/add_circle:",
        on_click=clear_conversation,
        width="stretch",
    ) 


st.image("./images/chatbot.svg", width=128, link="https://www.lifelong-learning.lu")

st.title(
    t["title"],
    anchor=False
)

# Identifiant utilisé pour le quota journalier.
client_ip = get_client_ip()

user_just_asked_initial_question = (
    "initial_question" in st.session_state and st.session_state.initial_question
)

user_just_clicked_suggestion = (
    "selected_suggestion" in st.session_state and st.session_state.selected_suggestion
)

user_first_interaction = (
    user_just_asked_initial_question or user_just_clicked_suggestion
)

has_message_history = (
    "messages" in st.session_state and len(st.session_state.messages) > 0
)

# Show a different home page if no question has been asked
if not user_first_interaction and not has_message_history:
    st.session_state.messages = []

    # Quota déjà épuisé : on n'affiche même pas le champ de saisie.
    if remaining_questions(client_ip) <= 0:
        with st.chat_message("assistant", avatar=get_avatar("assistant")):
            st.warning(t["quota_reached"].format(limit=MAX_QUESTIONS_PER_DAY))
        st.stop()

    with st.container():
        st.chat_input(t["ask"], key="initial_question")

        selected_suggestion = st.pills(
            label="Exemples",
            label_visibility="collapsed",
            options=suggestions.keys(),
            format_func=lambda x: suggestions[x],
            key="selected_suggestion",
        )

    st.button(
        t["disclaimer"],
        type="tertiary",
        on_click=show_disclaimer_dialog,
    )
    st.stop()

# Show chat input at the bottom when a question has been asked.
user_message = st.chat_input(t["ask"])

if not user_message:
    if user_just_asked_initial_question:
        user_message = st.session_state.initial_question
    if user_just_clicked_suggestion:
        user_message = remove_streamlit_markdown(suggestions[st.session_state.selected_suggestion])

if "prev_question_timestamp" not in st.session_state:
    st.session_state.prev_question_timestamp = datetime.datetime.fromtimestamp(0)

# Display chat messages from history as speech bubbles.
for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"], avatar=get_avatar(message["role"])):
        if message["role"] == "assistant":
            st.container()  # Fix ghost message bug.

        st.markdown(message["content"])

if user_message:
    # Décompte du quota journalier. C'est le SEUL endroit où l'on incrémente :
    # on n'arrive ici que si une question est réellement soumise, jamais sur un
    # simple rerun (clic sur un bouton, changement de langue, etc.).
    allowed, _ = consume_question(client_ip)

    if not allowed:
        with st.chat_message("user", avatar=get_avatar("user")):
            st.text(user_message)

        with st.chat_message("assistant", avatar=get_avatar("assistant")):
            st.container()  # Fix ghost message bug.
            st.warning(t["quota_reached"].format(limit=MAX_QUESTIONS_PER_DAY))

        # La question n'est ni enregistrée dans l'historique ni envoyée au LLM.
        # On neutralise user_message plutôt que d'appeler st.stop(), afin que la
        # barre latérale (bouton de téléchargement) reste affichée.
        user_message = None

if user_message:
    # When the user posts a message...
    st.session_state.messages.append(
        {"role": "user", "content": user_message}
    )

    # Display message as a speech bubble.
    with st.chat_message("user", avatar=get_avatar("user")):
        st.text(user_message)

    refresh_conversation_export()

    # Display assistant response as a speech bubble.
    with st.chat_message("assistant", avatar=get_avatar("assistant")):
        with st.spinner(t["loading"]):
            # Rate-limit the input if needed.
            question_timestamp = datetime.datetime.now()
            time_diff = question_timestamp - st.session_state.prev_question_timestamp
            st.session_state.prev_question_timestamp = question_timestamp

            if time_diff < MIN_TIME_BETWEEN_REQUESTS:
                # On attend le temps RESTANT, pas le temps déjà écoulé.
                time.sleep((MIN_TIME_BETWEEN_REQUESTS - time_diff).total_seconds())

        # Send prompt to Mistral via the Conversations API (web_search tool).
        #
        # Le connecteur web_search n'est pas supporte par le endpoint agent
        # historique (client.beta.agents.create + client.agents.complete :
        # erreur "WebSearchTool connector is not supported"). Il fonctionne en
        # revanche avec l'API Conversations, qui gere aussi l'historique cote
        # serveur via conversation_id : start_stream cree le fil au premier
        # message, append_stream l'alimente ensuite, sans avoir a renvoyer tout
        # l'historique ni a creer/supprimer un agent a chaque question.
        with st.spinner(t["searching"]):
            def ask_mistral(message):
                conversation_id = st.session_state.get("conversation_id")

                try:
                    if conversation_id:
                        stream = client.beta.conversations.append_stream(
                            conversation_id=conversation_id,
                            inputs=message,
                        )
                    else:
                        stream = client.beta.conversations.start_stream(
                            model=AGENT_MODEL,
                            instructions=INSTRUCTIONS,
                            inputs=message,
                            tools=[{"type": "web_search"}],
                        )

                    got_content = False

                    for event in stream:
                        data = event.data
                        event_type = getattr(data, "type", None)

                        if event_type == "conversation.response.started":
                            st.session_state.conversation_id = data.conversation_id

                        elif event_type == "message.output.delta":
                            content = data.content
                            if isinstance(content, str):
                                got_content = True
                                yield content
                            elif isinstance(content, list):
                                # Le contenu peut melanger texte et citations ;
                                # on ne garde que les morceaux de texte pur.
                                for chunk in content:
                                    if getattr(chunk, "type", None) == "text":
                                        got_content = True
                                        yield chunk.text

                    if not got_content:
                        raise RuntimeError("Reponse vide")

                except Exception as exc:
                    print(f"Erreur lors de l'appel a la conversation Mistral : {exc}")
                    yield (
                        "Desole, je n'ai pas pu obtenir de reponse. Merci de reessayer."
                        if st.session_state.lang == "fr"
                        else "Sorry, I could not get an answer. Please try again."
                    )

            response_gen = ask_mistral(user_message)

        # Put everything after the spinners
        with st.container():
            # Stream the LLM response.
            response = st.write_stream(response_gen)

            # Add messages to chat history.
            st.session_state.messages.append({"role": "assistant", "content": response})
            refresh_conversation_export()

            #send_telemetry(question=user_message, response=response)
            st.button(
                t["new"],
                icon=":material/refresh:",
                on_click=clear_conversation,
            )


with sidebar:
    st.download_button(
        label=t["download"],
        data=st.session_state.conversation_export_text,
        file_name=f"chat_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.txt",
        mime="text/plain",
        disabled=not st.session_state.conversation_export_text,
        width="stretch",
    )

    st.caption(
        t["quota_remaining"].format(
            remaining=remaining_questions(client_ip),
            limit=MAX_QUESTIONS_PER_DAY,
        )
    )