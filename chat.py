import datetime
import time
import re

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
MIN_TIME_BETWEEN_REQUESTS = datetime.timedelta(seconds=3)

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
        "searching": "Je recherche dans mes connaissances...",
        "download": "Télécharger le Chat",
        "disclaimer": "&nbsp;:small[:gray[:material/balance: Avertissement sur l'IA]]",
        "warning": "Avertissement",
        "language": "Choix de la langue"
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
    }
}
if "lang" not in st.session_state:
    st.session_state.lang = "fr" 


suggestions = {}

# -----------------------------------------------------------------------------
# Create a new MISTRAL client

mistral_api_key = st.secrets["MISTRAL_API_KEY"]

client = Mistral(api_key=mistral_api_key)

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
        with st.spinner(t["loading"]):
            # Rate-limit the input if needed.
            question_timestamp = datetime.datetime.now()
            time_diff = question_timestamp - st.session_state.prev_question_timestamp
            st.session_state.prev_question_timestamp = question_timestamp

            if time_diff < MIN_TIME_BETWEEN_REQUESTS:
                time.sleep(time_diff.seconds + time_diff.microseconds * 0.001)

        # Send prompt to LLM.
        with st.spinner(t["searching"]):
            def ask_mistral(messages):
                context_messages = [
                    {"role": "system", "content": INSTRUCTIONS},
                    *messages,
                ]

                stream = client.chat.stream(
                    model=MODEL,
                    messages=context_messages,
                )

                for chunk in stream:
                    if chunk.data.choices:
                        content = chunk.data.choices[0].delta.content
                        if content:
                            yield content

            response_gen = ask_mistral(st.session_state.messages)

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