# Contrat de sortie (STRICT)

Ta réponse finale doit être **exclusivement** un objet JSON valide (aucun texte avant/après,
pas de fence ```). Structure :

```json
{
  "status": "COMMENT",
  "summary": "Synthèse en 2-4 phrases : l'essentiel de la review.",
  "findings": [
    {
      "path": "chemin/relatif/du/fichier.ext",
      "line": 42,
      "severity": "BLOCKER | IMPORTANT | MINOR",
      "message": "Le problème en 1-2 lignes, avec le geste correctif.",
      "snippet": "la ligne concernée (copiée du diff, pour contexte)",
      "rule_id": "identifiant-court-optionnel",
      "confidence": 0
    }
  ]
}
```

## Règles NON négociables pour l'ancrage inline
1. **`line` DOIT être une ligne AJOUTÉE ou de contexte du diff de CETTE PR** (une ligne
   présente dans le nouveau fichier, à l'intérieur d'un hunk). Un finding sur une ligne hors
   diff sera basculé en remarque globale, jamais posté en inline — donc si tu veux qu'il soit
   inline, choisis une ligne réellement modifiée.
2. `path` = le chemin exact tel qu'il apparaît dans le diff (relatif à la racine du repo).
3. `line` = le numéro de ligne **dans le nouveau fichier** (côté droit du diff).
4. `snippet` sert uniquement à l'affichage — recopie la ligne, ne la paraphrase pas.
5. Ne mets JAMAIS de secret, de token, ni de chemin absolu machine dans un champ.
6. `confidence` honnête (voir la calibration du prompt système). Les findings sous le seuil
   configuré ne seront pas postés — ne gonfle pas.

## Champs
- `status` : toujours `"COMMENT"` (le bot ne pose jamais APPROVE/REQUEST_CHANGES).
- `summary` : requis, non vide (sert de corps à la review et de fallback).
- `findings` : liste (éventuellement vide). Un finding sans `path`/`line` exploitable est ignoré.
- `severity` : une des trois valeurs. `rule_id` et `snippet` sont optionnels (affichage).

Si tu n'as rien à signaler : `{"status":"COMMENT","summary":"RAS — changements sains.","findings":[]}`.
