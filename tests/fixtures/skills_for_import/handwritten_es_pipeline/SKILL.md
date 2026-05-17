---
name: resumen-de-texto
description: Genera un resumen conciso de un texto en un párrafo corto.
---

# Resumen de texto

Esta habilidad lee un archivo de texto y produce un resumen en un solo párrafo.

## Pasos

1. **load_text** (exact, Python): lee el archivo indicado por `path` y devuelve su contenido.
2. **summarise** (judgment): toma el texto y produce un resumen en tres frases.
