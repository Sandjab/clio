---
name: résumé-de-texte
description: Produit un résumé concis d'un texte en un court paragraphe.
---

# Résumé de texte

Cette compétence lit un fichier texte et produit un résumé en un seul paragraphe.

## Étapes

1. **load_text** (exact, Python) : lit le fichier indiqué par `path` et retourne son contenu.
2. **summarise** (judgment) : prend le texte et produit un résumé en trois phrases.
