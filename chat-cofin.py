import datetime
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
LIBRARY_IDS = ["019fa84f-323a-7250-b211-ab0283ec1362"]

# Nombre maximum de questions par jour et par adresse IP.
MAX_QUESTIONS_PER_DAY = 20

SUGGESTIONS_FR = {
    "q1": (
        ":blue[:material/local_library:] Quels salariés sont éligibles au cofinancement ?"
    ),
    "q2": (
        ":green[:material/database:] Les frais de déplacement sont-ils éligibles ?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] Où trouver les informations sur la masse salariale/ ?"
    ),
    "q4": (
        ":violet[:material/apparel:] Quelles pièces justificatives sont obligatoires ?"
    ),
    "q5": (
        ":red[:material/deployed_code:] C'est quoi l'adapatation au poste de travail ?"
    ),
}

SUGGESTIONS_EN = {
    "q1": (
        ":blue[:material/local_library:] Which employees are eligible for co-financing?"
    ),
    "q2": (
        ":green[:material/database:] Are travel expenses eligible?"
    ),
    "q3": (
        ":orange[:material/multiline_chart:] Where can I find information on payroll?"
    ),
    "q4": (
        ":violet[:material/apparel:] Which supporting documents are mandatory?"
    ),
    "q5": (
        ":red[:material/deployed_code:] What is workplace adaptation?"
    ),
}

# -----------------------------------------------------------------------------
# Translations
translations = {
    "fr": {
        "title": "Discutez avec un expert en cofinancement", 
        "ask": "Posez votre question",
        "new": "Nouveau Chat",
        "loading": "Veuillez patienter...",
        "searching": "Je recherche dans mes connaissances...",
        "download": "Télécharger le Chat",
        "disclaimer": "&nbsp;:small[:gray[:material/balance: Avertissement sur l'IA]]",
        "warning": "Avertissement",
        "language": "Choix de la langue",
        "quota_reached": (
            "Vous avez atteint le nombre maximum de "
            "**{limit} questions par jour**.\n\n"
            "Merci de revenir demain. En attendant, vous pouvez consulter "
            "directement [www.infpc.lu](https://www.infpc.lu)."
        ),
        "quota_remaining": "Questions restantes aujourd'hui : {remaining} / {limit}",
    },
    "en": {
        "title": "Ask a cofunding expert", 
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
            "[www.infpc.lu](https://www.infpc.lu)."
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
# @st.cache_resource : cree UNE seule fois pour tout le processus et reutilise
# par toutes les sessions, au lieu d'etre recree a chaque rerun de Streamlit.

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
QUOTA_DB_PATH = Path(__file__).parent / "quota-cofin.db"

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
    conn = sqlite3.connect(QUOTA_DB_PATH, check_same_thread=False, timeout=5)
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

sidebar = st.sidebar

with sidebar:

    st.image("./images/infpc.svg", width=200)

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


st.image("./images/chatlib.svg", width=128, link="https://www.infpc.lu")

st.title(
    t["title"],
    anchor=False
)

# Identifiant utilise pour le quota journalier.
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

    # Quota deja epuise : on n'affiche meme pas le champ de saisie.
    if remaining_questions(client_ip) <= 0:
        with st.chat_message("assistant"):
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
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.container()  # Fix ghost message bug.

        st.markdown(message["content"])

if user_message:
    # Decompte du quota journalier. C'est le SEUL endroit ou l'on incremente :
    # on n'arrive ici que si une question est reellement soumise, jamais sur un
    # simple rerun (clic sur un bouton, changement de langue, etc.).
    allowed, _ = consume_question(client_ip)

    if not allowed:
        with st.chat_message("user"):
            st.text(user_message)

        with st.chat_message("assistant"):
            st.container()  # Fix ghost message bug.
            st.warning(t["quota_reached"].format(limit=MAX_QUESTIONS_PER_DAY))

        # La question n'est ni enregistree dans l'historique ni envoyee au LLM.
        # On neutralise user_message plutot que d'appeler st.stop(), afin que la
        # barre laterale (bouton de telechargement) reste affichee.
        user_message = None

if user_message:
    # When the user posts a message...
    st.session_state.messages.append(
        {"role": "user", "content": user_message}
    )

    # Display message as a speech bubble.
    with st.chat_message("user"):
        st.text(user_message)

    refresh_conversation_export()

    # Display assistant response as a speech bubble.
    with st.chat_message("assistant"):
        # Create and execute agent
        # L'agent est cree pour cette question puis supprime dans le `finally`,
        # y compris si l'appel echoue. `agent` reste a None si la creation
        # elle-meme echoue, pour ne pas tenter de supprimer un agent inexistant.
        full_text = ""
        agent = None

        with st.spinner(t["searching"]):
            try:
                agent = client.beta.agents.create(
                    model=AGENT_MODEL,
                    name="Expert Cofinancement",
                    instructions=(
                        "Reponds aux questions en te basant uniquement sur la "
                        "librairie fournie."
                    ),
                    tools=[
                        {
                            "type": "document_library",
                            "library_ids": LIBRARY_IDS,
                        }
                    ],
                )
                print(f"Agent configure avec succes ! ID de l'agent : {agent.id}")

                response = client.agents.complete(
                    agent_id=agent.id,
                    # Send only the last question
                    messages=[{"role": "user", "content": user_message}],
                    # Send all previous questions
                    #messages=[m for m in st.session_state.messages if m.get("role") == "user"],
                )

                # Reconstruire le texte a partir des TextChunks du dernier message
                final_message = response.choices[0].messages[-1]

                if final_message.content:
                    for chunk in final_message.content:
                        # On ne prend que les morceaux de type texte pur
                        if chunk.type == "text":
                            full_text += chunk.text
                else:
                    print("L'agent n'a pas renvoye de texte brut.")

            except Exception as exc:
                print(f"Erreur lors de l'appel a l'agent : {exc}")

            finally:
                if agent is not None:
                    try:
                        client.beta.agents.delete(agent_id=agent.id)
                        print(f"L'agent {agent.id} a ete supprime avec succes.")
                    except Exception as exc:
                        # Un echec de suppression ne doit pas casser la page ni
                        # masquer l'erreur d'origine.
                        print(f"Suppression de l'agent {agent.id} impossible : {exc}")

        if not full_text:
            full_text = (
                "Desole, je n'ai pas pu obtenir de reponse. Merci de reessayer."
                if st.session_state.lang == "fr"
                else "Sorry, I could not get an answer. Please try again."
            )

        # Put everything after the spinners
        with st.container():
            # Stream the LLM response.
            st.write(full_text)

            # Add messages to chat history.
            st.session_state.messages.append({"role": "assistant", "content": full_text})
            refresh_conversation_export()

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