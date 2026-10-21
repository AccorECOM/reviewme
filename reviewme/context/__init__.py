"""Fournisseurs de contexte externe (ADR v3 D9).

Un fournisseur enrichit le prompt d'un reviewer avec des données que le diff ne contient
pas (ticket, spec, incident...). Règle non négociable : c'est TOUJOURS optionnel. Source
indisponible -> le reviewer qui l'exige est skippé, jamais une erreur, et la review
technique passe quand même. Le core ne dépend d'aucun service externe.
"""
