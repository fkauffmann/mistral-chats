#!/usr/bin/env python3
"""Script pour splitter le fichier faq.md en plusieurs fichiers markdown basés sur les headers de niveau 1"""

import re
import unicodedata
import os

FAQ_FILE = "/home/fabrice/Projects/Python/mistral-chats/documents/faq.md"
OUTPUT_DIR = "/home/fabrice/Projects/Python/mistral-chats/documents/faq_split"


def slugify(text):
    """Convertit un texte en slug URL-friendly (minuscules, pas d'accents, pas d'espaces, pas de ponctuation)"""
    # Normaliser pour décomposer les caractères accentués
    text = unicodedata.normalize('NFKD', text)
    # Garder uniquement les caractères ASCII
    text = text.encode('ascii', 'ignore').decode('ascii')
    # Convertir en minuscules
    text = text.lower()
    # Remplacer les apostrophes par des underscores
    text = text.replace("'", "_")
    # Remplacer les espaces par des tirets
    text = re.sub(r'\s+', '-', text)
    # Supprimer les caractères de ponctuation (sauf les tirets et underscores)
    text = re.sub(r'[^a-z0-9-_]', '', text)
    # Supprimer les tirets en double
    text = re.sub(r'-+', '-', text)
    # Supprimer les tirets en début et fin
    text = text.strip('-')
    return text


def split_faq():
    """Split le fichier faq.md en plusieurs fichiers basés sur les headers de niveau 1"""
    # Créer le dossier de sortie s'il n'existe pas
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    try:
        with open(FAQ_FILE, "r", encoding="utf-8") as file:
            content = file.read()
    except FileNotFoundError:
        return f"Erreur : Le fichier {FAQ_FILE} n'existe pas."
    except Exception as e:
        return f"Erreur lors de la lecture du fichier : {e}"
    
    # Split par les headers de niveau 1
    # On utilise un regex qui capture le header et tout ce qui suit jusqu'au prochain header
    sections = re.split(r'(?=^#\s)', content, flags=re.MULTILINE)
    
    # Le premier élément peut être vide ou du contenu avant le premier header
    current_header = None
    
    for section in sections:
        section = section.strip()
        if not section:
            continue
        
        # Vérifier si la section commence par un header de niveau 1
        header_match = re.match(r'^#\s+(.+)$', section, re.MULTILINE)
        
        if header_match:
            # Extraire le texte du header
            current_header = header_match.group(1).strip()
            # Le contenu de la section est tout ce qui suit le header
            section_content = section[header_match.end():].strip()
        else:
            # Section sans header, on l'ajoute au contenu de la section précédente
            if current_header is None:
                # C'est du contenu avant le premier header, on l'ignore ou on crée un fichier spécial
                continue
            section_content = section
        
        if current_header:
            # Nettoyer le nom du header pour le fichier
            slug = slugify(current_header)
            filename = os.path.join(OUTPUT_DIR, f"{slug}.md")
            
            # Écrire le contenu dans le fichier
            # On ajoute le header au début du fichier
            with open(filename, "w", encoding="utf-8") as outfile:
                outfile.write(f"# {current_header}\n\n{section_content}\n")
            
            print(f"Créé : {filename}")
    
    return f"Split terminé. {len([f for f in os.listdir(OUTPUT_DIR) if f.endswith('.md')])} fichiers créés dans {OUTPUT_DIR}"


if __name__ == "__main__":
    result = split_faq()
    print(result)
