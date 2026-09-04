"""Plugins Claude Code requis par un reviewer.

Un plugin apporte des skills et des agents que le moteur de review peut mobiliser pendant
son analyse. Un reviewer déclare ce dont il a besoin, le core l'installe avant de lancer la
review :

    [plugins]
    marketplaces = ["owner/marketplace-repo"]   # ajoutés d'abord
    install      = ["mon-plugin"]               # ou "mon-plugin@marketplace"

Idempotent : ce qui est déjà installé n'est pas réinstallé (une review ne doit pas payer
une résolution réseau à chaque PR).

⚠️ SÉCURITÉ : installer un plugin, c'est faire tourner du **code tiers** dans l'environnement
de review, aux côtés des secrets du job. Un plugin se relit comme du code (même règle que
les prechecks). Le core continue d'imposer son allowlist d'outils : un skill de plugin ne
peut pas s'accorder plus de droits que le reviewer lui-même.
"""
from __future__ import annotations

import logging
import subprocess

_TIMEOUT_S = 180


class PluginError(RuntimeError):
    """Installation impossible : le reviewer qui l'exige ne peut pas tourner correctement."""


def _run(cli: str, args: list[str], logger: logging.Logger) -> tuple[int, str]:
    try:
        proc = subprocess.run([cli, "plugin", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return 1, f"timeout après {_TIMEOUT_S}s"
    except OSError as e:
        return 1, str(e)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def installed_plugins(cli: str, logger: logging.Logger) -> set[str]:
    """Noms des plugins déjà installés, sans leur suffixe `@marketplace`.

    Sortie de `claude plugin list` :

        Installed plugins:

          ❯ context7@claude-plugins-official
            Version: unknown
            Scope: user

    Les lignes de détail (`Version:`, `Scope:`) sont ignorées ; seules comptent celles qui
    portent un identifiant `nom@marketplace`.
    """
    code, out = _run(cli, ["list"], logger)
    if code != 0:
        logger.info("`plugin list` indisponible : on tentera l'installation sans court-circuit")
        return set()

    names: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip().lstrip("❯•-*").strip()
        if not line or ":" in line or "@" not in line:
            continue
        names.add(line.split("@", 1)[0].strip())
    return names


def ensure_plugins(spec, cli: str, logger: logging.Logger) -> None:
    """Installe ce que le reviewer déclare. Lève PluginError si c'est impossible.

    L'appelant traite l'échec comme « ce reviewer ne peut pas tourner » — pas comme un échec
    de toute la review : les autres reviewers restent utiles.
    """
    if not (spec.plugins_marketplaces or spec.plugins_install):
        return

    for source in spec.plugins_marketplaces:
        code, out = _run(cli, ["marketplace", "add", source], logger)
        # Déjà ajouté = succès : l'opération doit rester rejouable à chaque review.
        if code != 0 and "already" not in out.lower():
            raise PluginError(f"marketplace '{source}' non ajouté : {out.strip()[:200]}")
        logger.info("[%s] marketplace prêt : %s", spec.id, source)

    if not spec.plugins_install:
        return

    already = installed_plugins(cli, logger)
    for plugin in spec.plugins_install:
        short = plugin.split("@")[0]
        if short in already:
            logger.info("[%s] plugin déjà installé : %s", spec.id, short)
            continue
        code, out = _run(cli, ["install", plugin], logger)
        if code != 0 and "already" not in out.lower():
            raise PluginError(f"plugin '{plugin}' non installé : {out.strip()[:200]}")
        logger.info("[%s] plugin installé : %s", spec.id, plugin)
